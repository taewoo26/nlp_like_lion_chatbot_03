"""실명 복원: vault.restore_token() 호출과 audit.restore_log 기록.

단위 (DB·모델 없이 실행)
- _fetch_originals: 형식이 맞는 토큰만 DB 함수에 넘기고, request_id·user_id 를 함께 넘기고, 커밋한다
- /api/restore-tokens: request_id 필수(형식 검사), Lv2·2차 인증 확인, 권한 없음 403 / 기타 오류 503
- /api/chat restore 경로: 같은 request_id 로 복원, 복원 실패해도 답변은 토큰 그대로 나간다
실제 DB (없으면 건너뜀, 모두 롤백)
- Lv2 복원 → restore_log 1행, 토큰 없음 → NULL·기록 없음, Lv2 아님 → 42501
"""
import uuid

import psycopg
import pytest
from fastapi.testclient import TestClient

from service import app as app_module
from service import audit
from starter import db

LV2 = {"user_id": "SB260001", "user_name": "가상", "team_cd": "DEPT_RISK", "team_name": "리스크팀",
       "position": "팀장", "grade": "부장", "access_level": 2, "is_admin": True}
TOK = "<PERSON_bd52a13d0d35>"
TOK2 = "<PHONE_0a1b2c3d4e5f>"
RID = "req_20261118_3f9a1c07b2e4"


# ---- 가짜 DB 연결 ----
class FakeConn:
    def __init__(self, rows=None, exc=None):
        self.rows, self.exc, self.calls, self.committed = rows or [], exc, [], False

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def cursor(self):
        return FakeCur(self)

    def commit(self):
        self.committed = True


class FakeCur:
    def __init__(self, conn):
        self.conn = conn

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params):
        self.conn.calls.append((sql, params))
        if self.conn.exc:
            raise self.conn.exc

    def fetchall(self):
        return self.conn.rows


def test_fetch_originals_calls_db_function(monkeypatch):
    fake = FakeConn(rows=[(TOK, ["홍길동", "홍 길동"]), (TOK2, None)])
    monkeypatch.setattr(app_module.db, "restore_conn", lambda: fake)
    got = app_module._fetch_originals({TOK, TOK2, "<PERSON_BD52A13D0D35>", "엉터리"}, RID, "SB260001")
    assert got == {TOK: "홍길동"}                         # 첫 번째 표기, NULL(토큰 없음)은 빠짐
    sql, params = fake.calls[0]
    assert "vault.restore_token" in sql and "token_map" not in sql
    assert params == (RID, "SB260001", sorted([TOK, TOK2]))   # 대문자 hex·형식 틀린 토큰은 제외
    assert fake.committed


def test_fetch_originals_no_valid_token_skips_db(monkeypatch):
    monkeypatch.setattr(app_module.db, "restore_conn", lambda: pytest.fail("DB를 부르면 안 됨"))
    assert app_module._fetch_originals({"<PERSON_ABC>", "x"}, RID, "SB260001") == {}


def test_fetch_originals_error_propagates_without_commit(monkeypatch):
    fake = FakeConn(exc=psycopg.errors.InsufficientPrivilege("복원 권한이 없습니다"))
    monkeypatch.setattr(app_module.db, "restore_conn", lambda: fake)
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        app_module._fetch_originals({TOK}, RID, "SB260001")
    assert not fake.committed


# ---- API ----
@pytest.fixture
def env(monkeypatch):
    s = {"user": dict(LV2), "fetch": [], "fetch_result": {TOK: "홍길동"}, "errors": []}
    monkeypatch.setattr(app_module.auth, "get_active_user", lambda uid, conn=None: dict(s["user"]))
    monkeypatch.setattr(app_module.auth, "authenticate",
                        lambda u, p, conn=None: app_module.auth.LoginResult(True, app_module.auth.OK, user=dict(s["user"])))
    monkeypatch.setattr(app_module.audit, "record", lambda row, conn=None: "db")
    monkeypatch.setattr(app_module.audit, "_log_error", lambda msg, *args: s["errors"].append(msg % args))

    def fake_fetch(tokens, request_id, user_id):
        s["fetch"].append((set(tokens), request_id, user_id))
        r = s["fetch_result"]
        if isinstance(r, Exception):
            raise r
        return r
    monkeypatch.setattr(app_module, "_fetch_originals", fake_fetch)
    client = TestClient(app_module.app)
    client.post("/api/login", json={"user_id": LV2["user_id"], "password": "x"})
    s["client"] = client
    return s


def _verify(env):
    assert env["client"].post("/api/verify-password", json={"password": "x"}).status_code == 200


def test_restore_tokens_passes_request_id(env):
    _verify(env)
    r = env["client"].post("/api/restore-tokens", json={"request_id": RID, "tokens": [TOK]})
    assert r.status_code == 200 and r.json() == {"map": {TOK: "홍길동"}}
    assert env["fetch"] == [({TOK}, RID, "SB260001")]


@pytest.mark.parametrize("body", [
    {"tokens": [TOK]},                                          # request_id 없음
    {"request_id": "", "tokens": [TOK]},
    {"request_id": "req_2026111_3f9a1c07b2e4", "tokens": [TOK]},   # 날짜 7자리
    {"request_id": "req_20261118_3F9A1C07B2E4", "tokens": [TOK]},  # 대문자 hex
    {"request_id": RID + "x", "tokens": [TOK]},
])
def test_restore_tokens_requires_valid_request_id(env, body):
    _verify(env)
    assert env["client"].post("/api/restore-tokens", json=body).status_code == 422
    assert env["fetch"] == []


def test_restore_tokens_needs_level2_and_verification(env):
    r = env["client"].post("/api/restore-tokens", json={"request_id": RID, "tokens": [TOK]})
    assert r.status_code == 403                                 # 2차 인증 전
    env["user"]["access_level"] = 1
    r = env["client"].post("/api/restore-tokens", json={"request_id": RID, "tokens": [TOK]})
    assert r.status_code == 403
    assert env["fetch"] == []


@pytest.mark.parametrize("exc, status", [
    (psycopg.errors.InsufficientPrivilege("복원 권한이 없습니다 (user_id=SB260001)"), 403),
    (psycopg.errors.CheckViolation("restore_log_token_fmt 홍길동"), 503),       # 기록 실패
    (psycopg.OperationalError("connection refused"), 503),
])
def test_restore_tokens_db_errors(env, exc, status):
    _verify(env)
    env["fetch_result"] = exc
    r = env["client"].post("/api/restore-tokens", json={"request_id": RID, "tokens": [TOK]})
    assert r.status_code == status
    assert "홍길동" not in r.text and "map" not in r.json()
    assert len(env["errors"]) == 1 and RID in env["errors"][0]
    assert "홍길동" not in env["errors"][0] and TOK not in env["errors"][0]   # 메시지·토큰은 로그에 남기지 않음


def _answer(q, k):
    return {"masked_query": f"{TOK} 고객 한도", "query_tokens": [TOK], "answer": f"{TOK} 고객 1건입니다.",
            "notice": None, "docs": [{"doc_id": "H0001", "masked_text": f"{TOK} 한도 상향", "snippet": f"{TOK} 한도 상향"}],
            "state": "answered"}


def test_chat_restore_uses_same_request_id(env, monkeypatch):
    monkeypatch.setattr(app_module.chat, "answer", _answer)
    _verify(env)
    r = env["client"].post("/api/chat", json={"query": "홍길동 고객 한도", "restore": True})
    body = r.json()
    assert r.status_code == 200 and body["restored"] is True
    assert body["answer"] == "홍길동 고객 1건입니다."
    assert env["fetch"] == [({TOK}, body["request_id"], "SB260001")]


def test_chat_restore_failure_keeps_tokens(env, monkeypatch):
    monkeypatch.setattr(app_module.chat, "answer", _answer)
    _verify(env)
    env["fetch_result"] = psycopg.OperationalError("down")
    r = env["client"].post("/api/chat", json={"query": "홍길동 고객 한도", "restore": True})
    body = r.json()
    assert r.status_code == 200 and body["restored"] is False and body["state"] == "found"
    assert TOK in body["answer"] and "홍길동" not in body["answer"]
    assert len(env["errors"]) == 1 and body["request_id"] in env["errors"][0]


# ---- 실제 DB (없으면 건너뜀). db.restore_conn() 으로 접속하고 끝나면 롤백 ----
@pytest.fixture
def conn():
    try:
        c = db.restore_conn()
    except psycopg.OperationalError as e:
        pytest.skip(f"DB 연결(db.restore_conn)에 실패했습니다: {e}")
    with c.cursor() as cur:
        cur.execute("SELECT to_regprocedure('vault.restore_token(text,text,text)') IS NOT NULL")
        if not cur.fetchone()[0]:
            c.close()
            pytest.skip("vault.restore_token 이 없습니다 (infra/init/05_audit.sql 실행 필요)")
    yield c
    c.rollback()
    c.close()


def _user(conn, level):
    with conn.cursor() as cur:
        cur.execute("SELECT user_id FROM policy.users WHERE access_level = %s AND is_active ORDER BY user_id LIMIT 1",
                    (level,))
        row = cur.fetchone()
    if row is None:
        pytest.skip(f"활성 Lv{level} 계정이 없습니다")
    return row[0]


def _fake_token(conn):
    token = f"<TEST_{uuid.uuid4().hex[:12]}>"
    with conn.cursor() as cur:
        cur.execute("INSERT INTO vault.token_map (token, label, normalized, originals) VALUES (%s, 'TEST', 'x', %s)",
                    (token, ["가상원문", "가상 원문"]))
    return token


def _restore_rows(conn, request_id):
    with conn.cursor() as cur:
        cur.execute("SELECT user_id, level, token, label, mode FROM audit.restore_log WHERE request_id = %s",
                    (request_id,))
        return cur.fetchall()


def test_db_restore_writes_log(conn):
    uid, token, rid = _user(conn, 2), _fake_token(conn), audit.new_request_id()
    missing = f"<TEST_{uuid.uuid4().hex[:12]}>"
    with conn.cursor() as cur:
        cur.execute(app_module._RESTORE_SQL, (rid, uid, [token, missing]))
        got = dict(cur.fetchall())
    assert got == {token: ["가상원문", "가상 원문"], missing: None}
    assert _restore_rows(conn, rid) == [(uid, 2, token, "TEST", "full")]   # 없는 토큰은 기록 안 됨


def test_db_restore_denied_below_level2(conn):
    uid, token = _user(conn, 1), _fake_token(conn)
    with pytest.raises(psycopg.errors.InsufficientPrivilege), conn.cursor() as cur:
        cur.execute(app_module._RESTORE_SQL, (audit.new_request_id(), uid, [token]))
