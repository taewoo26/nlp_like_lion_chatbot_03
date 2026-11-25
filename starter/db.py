"""DB 연결. 웹 서비스는 app_search 계정, 적재는 app_ingest·app_reset 계정을 씁니다(권한은 infra/init/06_grants.sh).

- rag_conn()   : rag.chunks 읽기/쓰기. vault 스키마에는 접근할 수 없습니다.
- vault_conn() : vault.token_map 읽기/쓰기. 적재(가명처리) 단계에서만 사용합니다.
  검색·답변 단계 코드는 vault_conn() 을 쓰지 않아야 합니다. 실명 복원은 restore_conn() 으로 합니다.
- search_conn(): 웹 서비스가 쓰는 app_search 접속(감사 로그 기록 등). SEARCH_DSN 이 없으면 rag_conn() 으로 대체하고 경고합니다.
- admin_conn() : 관리자 화면(/api/admin/*) 전용 app_admin 접속. ADMIN_DSN 이 없으면 관리자 계정으로 대체하고 경고합니다.
- auth_conn()  : 로그인 판정(service/auth.py) 전용. config.AUTH_DSN = app_search (없으면 관리자 계정으로 대체).
- restore_conn(): 실명 복원(service/app.py) 전용. vault.restore_token() 만 호출합니다(토큰마다 audit.restore_log 기록).
  auth_conn() 과 같은 접속(app_search)입니다.
  함수는 SECURITY DEFINER(소유자 vault_user)라 내부 조회·기록은 접속 계정과 상관없이 vault_user 권한으로 실행됩니다.
"""
import logging

import psycopg
from pgvector.psycopg import register_vector

from . import config

log = logging.getLogger(__name__)
_warned = set()


def _warn_once(key, msg):
    if key not in _warned:
        _warned.add(key)
        log.warning(msg)


def rag_conn():
    conn = psycopg.connect(config.RAG_DSN, autocommit=False, connect_timeout=5,
                           options="-c statement_timeout=30000")
    register_vector(conn)
    return conn


def search_conn():
    """웹 서비스용 접속(app_search). 벡터 컬럼을 읽을 수 있게 register_vector 를 건다."""
    if not config.SEARCH_DSN:
        _warn_once("search", "SEARCH_DSN/APP_SEARCH_PASSWORD 가 없어 rag_user 로 접속합니다. .env 를 확인하세요.")
        return rag_conn()
    conn = psycopg.connect(config.SEARCH_DSN, autocommit=False, connect_timeout=5,
                           options="-c statement_timeout=30000")
    register_vector(conn)
    return conn


def vault_conn():
    return psycopg.connect(config.VAULT_DSN, autocommit=False, connect_timeout=5)


def ingest_conn():
    """rag·vault를 함께 갱신할 전용 연결. 두 스키마의 쓰기 권한이 필요하다."""
    dsn = config.env("INGEST_DSN")
    if not dsn:
        raise ValueError("PERSON_DICT=on 적재에는 INGEST_DSN을 설정하세요 (app_ingest 계정).")
    conn = psycopg.connect(dsn, autocommit=False, connect_timeout=5)
    register_vector(conn)
    return conn


def reset_conn():
    """전체 초기화·재적재 전용 계정. 일반 검색/적재 계정에 TRUNCATE를 부여하지 않는다."""
    dsn = config.env('RESET_DSN')
    if not dsn:
        raise ValueError('--reset(PERSON_DICT on/off 공통)에는 RESET_DSN을 설정하세요 (app_reset 계정).')
    conn = psycopg.connect(dsn, autocommit=False, connect_timeout=5)
    register_vector(conn)
    return conn


def auth_conn():
    if not config.SEARCH_DSN:
        _warn_once("auth", "SEARCH_DSN/APP_SEARCH_PASSWORD 가 없어 관리자 계정으로 접속합니다. .env 를 확인하세요.")
    return psycopg.connect(config.AUTH_DSN, autocommit=False, connect_timeout=5,
                           options="-c statement_timeout=30000")


def admin_conn():
    """관리자 화면용. app_admin: rag·audit·policy 조회, 인물 검토 상태·사용자 레벨/활성 수정. 토큰 원문(originals)은 못 읽는다."""
    dsn = config.ADMIN_DSN
    if not dsn:
        _warn_once("admin", "ADMIN_DSN/APP_ADMIN_PASSWORD 가 없어 관리자 계정으로 접속합니다. .env 를 확인하세요.")
        dsn = config._SUPERUSER_DSN
    return psycopg.connect(dsn, autocommit=False, connect_timeout=5,
                           options="-c statement_timeout=30000")


def restore_conn():
    # 로그인 판정과 같은 접속(app_search). SEARCH_DSN 이 없으면 관리자 계정으로 대체된다.
    return psycopg.connect(config.AUTH_DSN, autocommit=False, connect_timeout=5,
                           options="-c statement_timeout=30000")
