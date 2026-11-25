"""웹 서비스 DB 계정(app_search) 전환 테스트.

- 단위 테스트: SEARCH_DSN 유무에 따른 접속 선택 (DB 불필요)
- 실제 DB 테스트: SEARCH_DSN(app_search)이 설정되어 있고 접속될 때만 실행. 허용된 일은 되고, 막힌 일은 권한 오류여야 한다.
"""
import psycopg
import pytest

from starter import config, db


class _FakeConn:
    def __init__(self):
        self.registered = False


def test_search_conn_uses_search_dsn(monkeypatch):
    seen = {}
    monkeypatch.setattr(config, "SEARCH_DSN", "host=h user=app_search password=x")
    monkeypatch.setattr(db.psycopg, "connect", lambda dsn, **kw: seen.update(dsn=dsn) or _FakeConn())
    monkeypatch.setattr(db, "register_vector", lambda c: setattr(c, "registered", True))
    conn = db.search_conn()
    assert seen["dsn"] == "host=h user=app_search password=x"
    assert conn.registered


def test_search_conn_falls_back_to_rag_conn_without_dsn(monkeypatch):
    monkeypatch.setattr(config, "SEARCH_DSN", "")
    sentinel = object()
    monkeypatch.setattr(db, "rag_conn", lambda: sentinel)
    assert db.search_conn() is sentinel


def test_auth_and_restore_use_auth_dsn(monkeypatch):
    seen = []
    monkeypatch.setattr(config, "AUTH_DSN", "host=h user=app_search password=x")
    monkeypatch.setattr(db.psycopg, "connect", lambda dsn, **kw: seen.append(dsn) or _FakeConn())
    db.auth_conn()
    db.restore_conn()
    assert seen == ["host=h user=app_search password=x"] * 2


# ---- 실제 DB (app_search 로 접속될 때만) ----
@pytest.fixture
def search_db():
    if "user=app_search" not in (config.SEARCH_DSN or ""):
        pytest.skip("SEARCH_DSN(app_search)이 설정되지 않았습니다.")
    try:
        conn = psycopg.connect(config.SEARCH_DSN, connect_timeout=3)
    except Exception as e:
        pytest.skip(f"app_search 접속에 실패했습니다: {e}")
    yield conn
    conn.rollback()
    conn.close()


def _denied(conn, sql):
    """권한 오류가 나야 하는 문장. 오류 후 트랜잭션은 롤백한다."""
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with conn.cursor() as cur:
            cur.execute(sql)
    conn.rollback()


def test_app_search_can_execute_restore_functions_only(search_db):
    """복원·부분 공개 함수는 실행할 수 있고, 그 안에서 쓰는 보조 함수(mask_name)는 직접 실행할 수 없다."""
    with search_db.cursor() as cur:
        cur.execute("""SELECT has_function_privilege('app_search', 'vault.restore_token(text,text,text)', 'EXECUTE'),
                              has_function_privilege('app_search', 'vault.partial_token(text,text,text)', 'EXECUTE'),
                              has_function_privilege('app_search', 'vault.mask_name(text)', 'EXECUTE')""")
        assert cur.fetchone() == (True, True, False)


def test_app_search_can_insert_query_log(search_db):
    """감사 로그 기록 경로(audit.write_query_log)가 app_search 로 동작한다. 읽기는 못 하므로 INSERT 만 확인하고 롤백."""
    from service import audit
    from tests.test_audit import RESULT, USER
    row = audit.build_row(audit.new_request_id(), USER, result=RESULT, latency_ms=1)
    audit.write_query_log(row, search_db)
    search_db.rollback()


def test_search_uses_search_conn(monkeypatch):
    """검색이 소유자 계정(rag_conn)이 아니라 app_search(search_conn)로 접속한다."""
    from starter import search
    monkeypatch.setattr(search.db, "rag_conn", lambda: pytest.fail("검색은 rag_conn 을 쓰면 안 됨"))
    import inspect
    assert "db.search_conn()" in inspect.getsource(search._search_masked)


def test_app_search_reads_search_tables(search_db):
    """검색 경로가 읽는 세 표(rag.chunks·look_up·role_master)를 읽을 수 있다."""
    with search_db.cursor() as cur:
        for sql in ("SELECT chunk_id FROM rag.look_up LIMIT 1",
                    "SELECT role_code FROM rag.role_master LIMIT 1",
                    "SELECT doc_id, persons, tokens FROM rag.chunks LIMIT 1"):
            cur.execute(sql)
            cur.fetchall()


def test_app_search_allowed_reads(search_db):
    with search_db.cursor() as cur:
        for sql in ("SELECT count(*) FROM rag.chunks", "SELECT count(*) FROM policy.users",
                    "SELECT count(*) FROM policy.doc_type_level"):
            cur.execute(sql)
            cur.fetchone()


def test_app_search_cannot_touch_vault_tables(search_db):
    _denied(search_db, "SELECT * FROM vault.token_map LIMIT 1")
    _denied(search_db, "SELECT * FROM vault.person_info LIMIT 1")


def test_app_search_cannot_modify_or_delete_audit_logs(search_db):
    _denied(search_db, "UPDATE audit.query_log SET state = state")
    _denied(search_db, "DELETE FROM audit.query_log")
    _denied(search_db, "SELECT * FROM audit.restore_log LIMIT 1")


def test_app_search_cannot_write_chunks_or_policy(search_db):
    _denied(search_db, "DELETE FROM rag.chunks")
    _denied(search_db, "UPDATE policy.users SET access_level = 2")
    _denied(search_db, "TRUNCATE rag.chunks")


# ---- 관리자 화면 계정 (app_admin) ----
def test_admin_conn_uses_admin_dsn(monkeypatch):
    seen = []
    monkeypatch.setattr(config, "ADMIN_DSN", "host=h user=app_admin password=x")
    monkeypatch.setattr(db.psycopg, "connect", lambda dsn, **kw: seen.append(dsn) or _FakeConn())
    db.admin_conn()
    assert seen == ["host=h user=app_admin password=x"]


def test_admin_conn_falls_back_to_superuser_without_dsn(monkeypatch):
    seen = []
    monkeypatch.setattr(config, "ADMIN_DSN", "")
    monkeypatch.setattr(db.psycopg, "connect", lambda dsn, **kw: seen.append(dsn) or _FakeConn())
    db.admin_conn()
    assert seen == [config._SUPERUSER_DSN]


def test_admin_api_uses_only_admin_conn():
    """관리자 API 코드가 소유자·Vault 계정(rag_conn·vault_conn)이나 로그인 계정(auth_conn)을 직접 쓰지 않는다."""
    import pathlib
    src = pathlib.Path(__file__).resolve().parents[1].joinpath("service", "app.py").read_text(encoding="utf-8")
    admin_part = src[src.index('@app.get("/admin")'):]
    for name in ("rag_conn", "vault_conn", "auth_conn"):
        assert f"db.{name}(" not in admin_part


@pytest.fixture
def admin_db():
    if "user=app_admin" not in (config.ADMIN_DSN or ""):
        pytest.skip("ADMIN_DSN(app_admin)이 설정되지 않았습니다.")
    try:
        conn = psycopg.connect(config.ADMIN_DSN, connect_timeout=3)
    except Exception as e:
        pytest.skip(f"app_admin 접속에 실패했습니다: {e}")
    yield conn
    conn.rollback()
    conn.close()


def test_app_admin_allowed(admin_db):
    with admin_db.cursor() as cur:
        for sql in ("SELECT count(*) FROM rag.chunks", "SELECT count(*) FROM vault.token_map",
                    "SELECT token, label FROM vault.token_map LIMIT 1",
                    "SELECT person_id, token, role, doc_id, resolution_status FROM vault.person_info LIMIT 1",
                    "SELECT count(*) FROM audit.query_log", "SELECT count(*) FROM audit.restore_log",
                    "SELECT count(*) FROM audit.policy_change_log",
                    "SELECT user_id, access_level, is_active FROM policy.users LIMIT 1"):
            cur.execute(sql)
            cur.fetchall()


def test_app_admin_cannot_read_originals_or_evidence(admin_db):
    _denied(admin_db, "SELECT originals FROM vault.token_map LIMIT 1")
    _denied(admin_db, "SELECT * FROM vault.token_map LIMIT 1")
    _denied(admin_db, "SELECT evidence FROM vault.person_info LIMIT 1")


def test_app_admin_cannot_modify_audit_policy_or_chunks(admin_db):
    _denied(admin_db, "UPDATE audit.query_log SET state = state")
    _denied(admin_db, "DELETE FROM audit.restore_log")
    _denied(admin_db, "UPDATE policy.doc_type_level SET min_level = min_level")
    _denied(admin_db, "UPDATE policy.users SET is_admin = true")
    _denied(admin_db, "DELETE FROM rag.chunks")


def test_app_admin_can_update_review_status_and_user_level_in_transaction(admin_db):
    """허용된 수정 열은 실제로 UPDATE 가 된다(행이 없어도 문장 자체는 통과). 끝나면 롤백."""
    with admin_db.cursor() as cur:
        cur.execute("UPDATE vault.person_info SET resolution_status = resolution_status WHERE false")
        cur.execute("UPDATE policy.users SET access_level = access_level, is_active = is_active WHERE false")
