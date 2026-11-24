"""DB 연결. 검색용(rag_user), 매핑용(vault_user) 계정을 따로 쓰고, 로그인 판정은 지금 관리자 계정을 씁니다.

- rag_conn()   : rag.chunks 읽기/쓰기. vault 스키마에는 접근할 수 없습니다.
- vault_conn() : vault.token_map 읽기/쓰기. 적재(가명처리) 단계에서만 사용합니다.
  검색·답변 단계 코드는 vault_conn() 을 쓰지 않아야 합니다. 실명 복원은 restore_conn() 으로 합니다.
- auth_conn()  : 로그인 판정(service/auth.py) 전용. 지금은 관리자 계정(POSTGRES_USER)으로 연결합니다(구조 단순화).
  고도화 때 config.AUTH_DSN 만 app_search 로 바꾸면 됩니다(권한은 infra/init/06_grants.sh 에 있음).
- restore_conn(): 실명 복원(service/app.py) 전용. vault.restore_token() 만 호출합니다(토큰마다 audit.restore_log 기록).
  지금은 관리자 계정(AUTH_DSN 과 같은 접속)을 씁니다. 권한 정리 때 pii_search 계정(app_search)으로 바꿉니다.
  함수는 SECURITY DEFINER(소유자 vault_user)라 내부 조회·기록은 접속 계정과 상관없이 vault_user 권한으로 실행됩니다.
"""
import psycopg
from pgvector.psycopg import register_vector

from . import config


def rag_conn():
    conn = psycopg.connect(config.RAG_DSN, autocommit=False, connect_timeout=5,
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
    return psycopg.connect(config.AUTH_DSN, autocommit=False, connect_timeout=5,
                           options="-c statement_timeout=30000")


def restore_conn():
    # 권한 정리 때 이 접속만 pii_search 계정으로 바꾼다 (지금은 로그인 판정과 같은 관리자 계정)
    return psycopg.connect(config.AUTH_DSN, autocommit=False, connect_timeout=5,
                           options="-c statement_timeout=30000")
