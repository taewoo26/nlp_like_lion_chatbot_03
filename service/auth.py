"""로그인 판정. 사번·비밀번호가 맞는지 확인합니다.

- 테이블: infra/init/04_policy.sql 의 policy.users·teams (develop DDL 기준, 스키마는 수정하지 않음)
- DB 계정: db.auth_conn(). 지금은 관리자 계정(POSTGRES_USER)을 씁니다.
- 이 모듈은 policy.teams·users 읽기와 users.last_login_at 갱신만 합니다.
- 화면에 보여 줄 문구는 API(service/app.py)가 reason 값으로 정합니다. 이 모듈은 판정만 합니다.
- 고도화 때 추가 예정: 로그인 실패 잠금(F-68), 세션 자동 만료(F-03), 로그인 전용 DB 계정

사용:
    result = authenticate("SB260001", "qwer0001")
    if result.ok: result.user["access_level"]
"""
from __future__ import annotations

from dataclasses import dataclass

import bcrypt

from starter import db

MAX_USER_ID_LENGTH = 20
BCRYPT_MAX_BYTES = 72          # bcrypt 는 72바이트까지만 비교한다

# reason 값
OK = "ok"
INVALID = "invalid"     # 사번 없음·비밀번호 틀림 (화면에서는 구분하지 않음)
INACTIVE = "inactive"   # 비활성 계정

# 없는 사번에도 비밀번호 비교 시간을 똑같이 쓰기 위한 해시 (응답 시간으로 사번 존재를 알 수 없게).
# 'dummy-password' 의 bcrypt 해시(cost 12, 시연 계정과 같은 비용). 비밀값이 아님.
_DUMMY_HASH = b"$2b$12$RdVCqaot6Rbb8.fnEa6qDel5SJdXVnv3e5oojhpmbOiGIfI4ihU/2"

_USER_KEYS = ("user_id", "user_name", "team_cd", "team_name", "position", "grade", "access_level", "is_admin")


@dataclass
class LoginResult:
    ok: bool
    reason: str
    user: dict | None = None    # 성공 시: user_id, user_name, team_cd, team_name, position, grade, access_level, is_admin


def normalize_user_id(user_id: str) -> str:
    """앞뒤 공백 제거, 소문자 입력 허용 (sb260001 → SB260001)."""
    return (user_id or "").strip().upper()


def _check_password(password: str, password_hash: str | None) -> bool:
    """비밀번호 비교. 사번이 없거나(password_hash None) 입력이 비었거나 72바이트를 넘으면 False.
    어느 경우든 bcrypt 비교를 한 번 해서 응답 시간을 맞춘다."""
    raw = (password or "").encode("utf-8")
    if password_hash is None or not raw or len(raw) > BCRYPT_MAX_BYTES:
        bcrypt.checkpw(b"dummy-password", _DUMMY_HASH)
        return False
    return bcrypt.checkpw(raw, password_hash.encode("ascii"))


_SELECT_USER = """
SELECT u.password_hash, u.is_active,
       u.user_id, u.user_name, u.team_cd, t.team_name, u.position, u.grade, u.access_level, u.is_admin
  FROM policy.users u
  JOIN policy.teams t USING (team_cd)
 WHERE u.user_id = %s
"""

# 로그인한 뒤 요청마다 계정 상태를 다시 확인한다 (비활성·레벨 변경이 바로 반영되도록). 비밀번호 해시는 읽지 않는다.
_SELECT_ACTIVE_USER = """
SELECT u.user_id, u.user_name, u.team_cd, t.team_name, u.position, u.grade, u.access_level, u.is_admin
  FROM policy.users u
  JOIN policy.teams t USING (team_cd)
 WHERE u.user_id = %s AND u.is_active
"""

_RECORD_LOGIN = "UPDATE policy.users SET last_login_at = now() WHERE user_id = %s"


def _judge(conn, user_id: str, password: str) -> LoginResult:
    uid = normalize_user_id(user_id)
    if not uid or len(uid) > MAX_USER_ID_LENGTH:
        _check_password(password, None)
        return LoginResult(False, INVALID)

    with conn.cursor() as cur:
        cur.execute(_SELECT_USER, (uid,))
        row = cur.fetchone()
        if row is None:
            _check_password(password, None)
            return LoginResult(False, INVALID)

        password_hash, is_active, *user_values = row
        if not _check_password(password, password_hash):
            return LoginResult(False, INVALID)
        if not is_active:                       # 비밀번호가 맞을 때만 비활성 여부를 알려 준다
            return LoginResult(False, INACTIVE)

        user = dict(zip(_USER_KEYS, user_values))
        cur.execute(_RECORD_LOGIN, (user["user_id"],))
        return LoginResult(True, OK, user=user)


def authenticate(user_id: str, password: str, conn=None) -> LoginResult:
    """사번·비밀번호로 로그인을 판정한다.

    conn 을 주면 그 연결에서 실행하고 커밋하지 않는다(테스트에서 롤백용). 없으면 db.auth_conn() 으로 연결해 커밋한다.
    """
    if conn is not None:
        return _judge(conn, user_id, password)
    with db.auth_conn() as own:
        result = _judge(own, user_id, password)
        own.commit()
        return result


def get_active_user(user_id: str, conn=None) -> dict | None:
    """로그인 세션의 사번으로 현재 계정 정보를 읽는다. 없거나 비활성이면 None."""
    def run(c):
        with c.cursor() as cur:
            cur.execute(_SELECT_ACTIVE_USER, (user_id,))
            row = cur.fetchone()
        return dict(zip(_USER_KEYS, row)) if row else None

    if conn is not None:
        return run(conn)
    with db.auth_conn() as own:
        return run(own)
