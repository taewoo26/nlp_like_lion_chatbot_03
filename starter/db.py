"""DB 연결. 검색용(rag_user), 매핑용(vault_user) 계정을 따로 쓰고, 로그인 판정은 지금 관리자 계정을 씁니다.

- rag_conn()   : rag.chunks 읽기/쓰기. vault 스키마에는 접근할 수 없습니다.
- vault_conn() : vault.token_map 읽기/쓰기. 적재(가명처리) 단계에서만 사용합니다.
  검색·답변 단계 코드는 vault_conn() 을 쓰지 않아야 합니다. (실명 복원은 종합에서 다룸)
- auth_conn()  : 로그인 판정(service/auth.py) 전용. 지금은 관리자 계정(POSTGRES_USER)으로 연결합니다(구조 단순화).
  고도화 때 config.AUTH_DSN 만 app_search 로 바꾸면 됩니다(권한은 infra/init/06_grants.sh 에 있음).
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


def auth_conn():
    return psycopg.connect(config.AUTH_DSN, autocommit=False)
