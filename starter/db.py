"""DB 연결. 검색용(rag_user)과 매핑용(vault_user) 계정을 따로 씁니다.

- rag_conn()   : rag.chunks 읽기/쓰기. vault 스키마에는 접근할 수 없습니다.
- vault_conn() : vault.token_map 읽기/쓰기. 적재(가명처리) 단계에서만 사용합니다.
  검색·답변 단계 코드는 vault_conn() 을 쓰지 않아야 합니다. (실명 복원은 종합에서 다룸)
"""
import psycopg
from pgvector.psycopg import register_vector

from . import config


def rag_conn():
    conn = psycopg.connect(config.RAG_DSN, autocommit=False)
    register_vector(conn)
    return conn


def vault_conn():
    return psycopg.connect(config.VAULT_DSN, autocommit=False)


def ingest_conn():
    """rag·vault를 함께 갱신할 전용 연결. 두 스키마의 쓰기 권한이 필요하다."""
    dsn = config.env("INGEST_DSN")
    if not dsn:
        raise ValueError("PERSON_DICT=on 적재에는 INGEST_DSN을 설정하세요 (app_ingest 계정).")
    conn = psycopg.connect(dsn, autocommit=False)
    register_vector(conn)
    return conn


def reset_conn():
    """전체 초기화·재적재 전용 계정. 일반 검색/적재 계정에 TRUNCATE를 부여하지 않는다."""
    dsn = config.env('RESET_DSN')
    if not dsn:
        raise ValueError('PERSON_DICT=off --reset에는 RESET_DSN을 설정하세요 (app_reset 계정).')
    conn = psycopg.connect(dsn, autocommit=False)
    register_vector(conn)
    return conn
