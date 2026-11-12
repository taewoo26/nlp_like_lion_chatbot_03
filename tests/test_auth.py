"""로그인 판정(service/auth.py)을 실제 DB의 시연 계정으로 확인한다.

필요: Docker DB 실행, .env 의 POSTGRES_PASSWORD, infra/init/07_seed_demo_users.sql 의 SB260001~SB260010.
모든 테스트는 한 트랜잭션 안에서 실행하고 마지막에 롤백하므로 DB는 바뀌지 않는다.
DB에 연결할 수 없거나 시연 계정이 없으면 건너뛴다.
"""
import psycopg
import pytest

from service import auth
from starter import db


@pytest.fixture
def conn():
    try:
        c = db.auth_conn()
    except psycopg.OperationalError as e:
        pytest.skip(f"로그인용 DB 연결(db.auth_conn)에 실패했습니다: {e}")
    with c.cursor() as cur:
        cur.execute("SELECT count(*) FROM policy.users WHERE user_id IN ('SB260001', 'SB260010')")
        found = cur.fetchone()[0]
    if found != 2:
        c.close()
        pytest.skip("시연 계정이 없습니다 (infra/init/07_seed_demo_users.sql 실행 필요)")
    yield c
    c.rollback()
    c.close()


def test_success(conn):
    r = auth.authenticate("SB260001", "qwer0001", conn=conn)
    assert r.ok and r.reason == auth.OK
    assert r.user["user_name"] == "전경희" and r.user["access_level"] == 2
    assert r.user["team_name"] == "인사팀"
    assert "password_hash" not in r.user
    with conn.cursor() as cur:
        cur.execute("SELECT last_login_at IS NOT NULL FROM policy.users WHERE user_id = 'SB260001'")
        assert cur.fetchone()[0]


def test_lowercase_and_spaces(conn):
    assert auth.authenticate("  sb260001 ", "qwer0001", conn=conn).ok


def test_inactive(conn):
    r = auth.authenticate("SB260010", "qwer0010", conn=conn)
    assert not r.ok and r.reason == auth.INACTIVE


def test_inactive_with_wrong_password_is_invalid(conn):
    r = auth.authenticate("SB260010", "wrong", conn=conn)   # 비밀번호가 틀리면 계정 상태를 알려 주지 않음
    assert not r.ok and r.reason == auth.INVALID


@pytest.mark.parametrize("user_id,password", [
    ("SB260001", "wrong"),        # 비밀번호 틀림
    ("SB269999", "qwer0001"),     # 없는 사번
    ("", ""),                     # 빈 값
    ("SB260001", "x" * 100),      # bcrypt 길이 초과
    ("X" * 30, "qwer0001"),       # 사번 길이 초과
])
def test_invalid(conn, user_id, password):
    r = auth.authenticate(user_id, password, conn=conn)
    assert not r.ok and r.reason == auth.INVALID


def test_get_active_user(conn):
    assert auth.get_active_user("SB260003", conn=conn)["access_level"] == 1
    assert auth.get_active_user("SB260010", conn=conn) is None
    assert auth.get_active_user("SB269999", conn=conn) is None
