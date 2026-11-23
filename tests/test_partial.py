"""이름 부분 공개(Lv1): vault.partial_token() 호출과 audit.restore_log(mode=partial) 기록.

단위 (DB·모델 없이 실행)
- _fetch_partials: 이름 토큰만 DB 함수에 넘기고, request_id·user_id 를 함께 넘기고, 커밋한다
- /api/verify-password: Lv1 은 partial_verified 만 켠다 (실명 복원 인증과 섞이지 않음), Lv0 403, 비밀번호 틀림 401
- /api/partial-tokens: Lv1 + 2차 인증만, request_id 형식 검사, 권한 없음 403 / 기타 오류 503, 원문·토큰은 로그에 남기지 않음
실제 DB (없거나 05_audit.sql 미적용이면 건너뜀, 모두 롤백)
- 이름 마스킹 규칙
- Lv1 + 본인 질문 + 그 답변의 이름 → 마스킹 값, restore_log 1행(partial)
- 문서(returned_doc_ids)에만 나온 이름도 공개
- 다른 사람 질문·없는 질문·Lv1 아님 → 42501 / 답변에 없던 이름·이름 아닌 토큰 → NULL, 기록 없음
"""
import uuid

import psycopg
import pytest
from fastapi.testclient import TestClient

from service import app as app_module
from service import audit
from starter import db
from tests.test_restore import FakeConn

LV1 = {"user_id": "SB260003", "user_name": "가상", "team_cd": "DEPT_CREDIT", "team_name": "여신팀",
       "position": "팀원", "grade": "대리", "access_level": 1, "is_admin": False}
TOK = "<PERSON_bd52a13d0d35>"
TOK2 = "<PHONE_0a1b2c3d4e5f>"
RID = "req_20261123_3f9a1c07b2e4"


# ---- _fetch_partials ----
def test_fetch_partials_calls_db_function_with_person_tokens_only(monkeypatch):
    fake = FakeConn(rows=[(TOK, "윤*영")])
    monkeypatch.setattr(app_module.db, "restore_conn", lambda: fake)
    got = app_module._fetch_partials({TOK, TOK2, "<PERSON_BD52A13D0D35>", "엉터리"}, RID, "SB260003")
    assert got == {TOK: "윤*영"}
    sql, params = fake.calls[0]
    assert "vault.partial_token" in sql and "token_map" not in sql and "restore_token" not in sql
    assert params == (RID, "SB260003", [TOK])            # 전화 토큰·대문자 hex·형식 틀린 토큰은 제외
    assert fake.committed


def test_fetch_partials_drops_null(monkeypatch):
    fake = FakeConn(rows=[(TOK, None)])                  # 답변에 없던 이름 등
    monkeypatch.setattr(app_module.db, "restore_conn", lambda: fake)
    assert app_module._fetch_partials({TOK}, RID, "SB260003") == {}


def test_fetch_partials_no_person_token_skips_db(monkeypatch):
    monkeypatch.setattr(app_module.db, "restore_conn", lambda: pytest.fail("DB를 부르면 안 됨"))
    assert app_module._fetch_partials({TOK2, "x"}, RID, "SB260003") == {}


def test_fetch_partials_error_propagates_without_commit(monkeypatch):
    fake = FakeConn(exc=psycopg.errors.InsufficientPrivilege("본인 질문이 아닙니다"))
    monkeypatch.setattr(app_module.db, "restore_conn", lambda: fake)
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        app_module._fetch_partials({TOK}, RID, "SB260003")
    assert not fake.committed


# ---- API ----
@pytest.fixture
def env(monkeypatch):
    s = {"user": dict(LV1), "fetch": [], "fetch_result": {TOK: "윤*영"}, "errors": []}
    monkeypatch.setattr(app_module.auth, "get_active_user", lambda uid, conn=None: dict(s["user"]))
    monkeypatch.setattr(app_module.auth, "authenticate",
                        lambda u, p, conn=None: app_module.auth.LoginResult(True, app_module.auth.OK, user=dict(s["user"])))
    monkeypatch.setattr(app_module.audit, "_log_error", lambda msg, *args: s["errors"].append(msg % args))
    monkeypatch.setattr(app_module, "_fetch_originals", lambda *a: pytest.fail("Lv1 은 실명 복원을 부르면 안 됨"))

    def fake_fetch(tokens, request_id, user_id):
        s["fetch"].append((set(tokens), request_id, user_id))
        r = s["fetch_result"]
        if isinstance(r, Exception):
            raise r
        return r
    monkeypatch.setattr(app_module, "_fetch_partials", fake_fetch)
    client = TestClient(app_module.app)
    client.post("/api/login", json={"user_id": LV1["user_id"], "password": "x"})
    s["client"] = client
    return s


def _verify(env):
    assert env["client"].post("/api/verify-password", json={"password": "x"}).status_code == 200


def test_partial_tokens_lv1_after_second_auth(env):
    _verify(env)
    r = env["client"].post("/api/partial-tokens", json={"request_id": RID, "tokens": [TOK]})
    assert r.status_code == 200 and r.json() == {"map": {TOK: "윤*영"}}
    assert env["fetch"] == [({TOK}, RID, "SB260003")]       # 사번은 세션에서 꺼낸 값


def test_partial_tokens_needs_second_auth(env):
    r = env["client"].post("/api/partial-tokens", json={"request_id": RID, "tokens": [TOK]})
    assert r.status_code == 403 and env["fetch"] == []


def test_verify_password_wrong_password(env, monkeypatch):
    monkeypatch.setattr(app_module.auth, "authenticate",
                        lambda u, p, conn=None: app_module.auth.LoginResult(False, "invalid"))
    assert env["client"].post("/api/verify-password", json={"password": "x"}).status_code == 401
    r = env["client"].post("/api/partial-tokens", json={"request_id": RID, "tokens": [TOK]})
    assert r.status_code == 403 and env["fetch"] == []


def test_verify_password_lv0_denied(env):
    env["user"]["access_level"] = 0
    assert env["client"].post("/api/verify-password", json={"password": "x"}).status_code == 403


def test_lv1_verification_does_not_open_full_restore(env):
    _verify(env)                                             # Lv1 인증은 partial_verified 만 켠다
    env["user"]["access_level"] = 2                          # 이후 Lv2 로 바뀌어도 실명 복원은 다시 인증해야 함
    r = env["client"].post("/api/restore-tokens", json={"request_id": RID, "tokens": [TOK]})
    assert r.status_code == 403


@pytest.mark.parametrize("level", [0, 2])
def test_partial_tokens_lv1_only(env, level):
    _verify(env)
    env["user"]["access_level"] = level
    r = env["client"].post("/api/partial-tokens", json={"request_id": RID, "tokens": [TOK]})
    assert r.status_code == 403 and env["fetch"] == []


def test_partial_tokens_requires_login():
    r = TestClient(app_module.app).post("/api/partial-tokens", json={"request_id": RID, "tokens": [TOK]})
    assert r.status_code == 401


@pytest.mark.parametrize("body", [
    {"tokens": [TOK]},
    {"request_id": "req_20261123_3F9A1C07B2E4", "tokens": [TOK]},
    {"request_id": RID, "tokens": []},
])
def test_partial_tokens_validates_body(env, body):
    _verify(env)
    assert env["client"].post("/api/partial-tokens", json=body).status_code == 422
    assert env["fetch"] == []


@pytest.mark.parametrize("exc, status", [
    (psycopg.errors.InsufficientPrivilege("본인 질문이 아니거나 없는 요청입니다"), 403),
    (psycopg.errors.CheckViolation("restore_log_mode_chk 윤선영"), 503),      # 기록 실패
    (psycopg.OperationalError("connection refused"), 503),
])
def test_partial_tokens_db_errors(env, exc, status):
    _verify(env)
    env["fetch_result"] = exc
    r = env["client"].post("/api/partial-tokens", json={"request_id": RID, "tokens": [TOK]})
    assert r.status_code == status
    assert "윤선영" not in r.text and "map" not in r.json()
    assert len(env["errors"]) == 1 and RID in env["errors"][0]
    assert "윤선영" not in env["errors"][0] and TOK not in env["errors"][0]


# ---- 실제 DB (없으면 건너뜀). db.restore_conn() 으로 접속하고 끝나면 롤백 ----
@pytest.fixture
def conn():
    try:
        c = db.restore_conn()
    except psycopg.OperationalError as e:
        pytest.skip(f"DB 연결(db.restore_conn)에 실패했습니다: {e}")
    with c.cursor() as cur:
        cur.execute("SELECT to_regprocedure('vault.partial_token(text,text,text)') IS NOT NULL")
        if not cur.fetchone()[0]:
            c.close()
            pytest.skip("vault.partial_token 이 없습니다 (infra/init/05_audit.sql 다시 실행 필요)")
    yield c
    c.rollback()
    c.close()


def _user(conn, level):
    with conn.cursor() as cur:
        cur.execute("SELECT user_id, team_cd, position FROM policy.users "
                    "WHERE access_level = %s AND is_active ORDER BY user_id LIMIT 1", (level,))
        row = cur.fetchone()
    if row is None:
        pytest.skip(f"활성 Lv{level} 계정이 없습니다")
    return {"user_id": row[0], "team_cd": row[1], "position": row[2], "access_level": level}


def _token(conn, label="person", originals=("윤선영", "윤 선영")):
    token = f"<{label.upper()}_{uuid.uuid4().hex[:12]}>"
    with conn.cursor() as cur:
        cur.execute("INSERT INTO vault.token_map (token, label, normalized, originals) VALUES (%s, %s, %s, %s)",
                    (token, label, token, list(originals)))
    return token


def _query(conn, user, tokens=(), doc_ids=()):
    """user 가 한 질문 1건을 query_log 에 남기고 request_id 를 돌려준다."""
    rid = audit.new_request_id()
    result = {"masked_query": " ".join(tokens) or "질문", "query_tokens": list(tokens),
              "docs": [{"doc_id": d} for d in doc_ids], "state": "answered"}
    audit.write_query_log(audit.build_row(rid, user, result=result), conn=conn)
    return rid


def _partial(conn, rid, uid, tokens):
    with conn.cursor() as cur:
        cur.execute(app_module._PARTIAL_SQL, (rid, uid, tokens))
        return dict(cur.fetchall())


def _logs(conn, rid):
    with conn.cursor() as cur:
        cur.execute("SELECT user_id, level, token, label, mode FROM audit.restore_log WHERE request_id = %s", (rid,))
        return cur.fetchall()


@pytest.mark.parametrize("name, masked", [
    ("윤선영", "윤*영"), ("윤 선영", "윤*영"), ("김민", "김*"), ("남궁민수", "남**수"), ("이", "*"),
])
def test_db_mask_name(conn, name, masked):
    with conn.cursor() as cur:
        cur.execute("SELECT vault.mask_name(%s)", (name,))
        assert cur.fetchone()[0] == masked


def test_db_partial_masks_and_logs(conn):
    user, token = _user(conn, 1), _token(conn)
    rid = _query(conn, user, tokens=[token])
    assert _partial(conn, rid, user["user_id"], [token]) == {token: "윤*영"}   # 원문은 나오지 않음
    assert _logs(conn, rid) == [(user["user_id"], 1, token, "person", "partial")]


def test_db_partial_token_in_returned_doc(conn):
    user, token = _user(conn, 1), _token(conn)
    doc_id = f"TEST_{uuid.uuid4().hex[:8]}"
    with conn.cursor() as cur:                           # 임베딩은 기존 청크 것을 빌린다 (차원 맞추기)
        cur.execute("""INSERT INTO rag.chunks (chunk_id, doc_id, chunk_idx, masked_text, tokens, embedding)
                       SELECT %s, %s, 0, %s, %s, embedding FROM rag.chunks LIMIT 1""",
                    (doc_id + "#0", doc_id, f"{token} 상담", [token]))
        if cur.rowcount == 0:
            pytest.skip("rag.chunks 가 비어 있습니다")
    rid = _query(conn, user, doc_ids=[doc_id])
    assert _partial(conn, rid, user["user_id"], [token]) == {token: "윤*영"}


def test_db_partial_skips_tokens_outside_answer_or_not_person(conn):
    user = _user(conn, 1)
    shown, hidden, phone = _token(conn), _token(conn), _token(conn, "phone_number", ["010-1234-5678"])
    rid = _query(conn, user, tokens=[shown, phone])
    got = _partial(conn, rid, user["user_id"], [shown, hidden, phone])
    assert got == {shown: "윤*영", hidden: None, phone: None}   # 답변에 없던 이름·전화 토큰은 공개 안 함
    assert [r[2] for r in _logs(conn, rid)] == [shown]         # 공개한 토큰만 기록


def test_db_partial_rejects_other_users_request(conn):
    owner, token = _user(conn, 1), _token(conn)
    with conn.cursor() as cur:
        cur.execute("SELECT user_id FROM policy.users WHERE access_level = 1 AND is_active AND user_id <> %s LIMIT 1",
                    (owner["user_id"],))
        row = cur.fetchone()
    if row is None:
        pytest.skip("활성 Lv1 계정이 2명 이상 필요합니다")
    rid = _query(conn, owner, tokens=[token])
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        _partial(conn, rid, row[0], [token])


def test_db_partial_rejects_unknown_request(conn):
    user, token = _user(conn, 1), _token(conn)
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        _partial(conn, audit.new_request_id(), user["user_id"], [token])   # query_log 에 없는 request_id


def test_db_partial_rejects_level2(conn):
    user, token = _user(conn, 2), _token(conn)
    rid = _query(conn, user, tokens=[token])
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        _partial(conn, rid, user["user_id"], [token])
