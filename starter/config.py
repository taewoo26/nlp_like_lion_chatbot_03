"""환경변수 설정. 값은 프로젝트 루트의 .env 에서 읽습니다."""
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


def env(name, default=None):
    return os.getenv(name, default)


def meta_on(name):
    """REGION_META / ROLE_META = on|off (기본 off). 끄면 실전 2와 같은 결과 (규칙 정의서 COM-09)."""
    mode = (env(name, "off") or "off").strip().lower()
    if mode not in ("on", "off"):
        raise ValueError(f"{name} 는 on 또는 off 여야 합니다: {mode}")
    return mode == "on"


DB_HOST = env("DB_HOST", "localhost")
DB_PORT = int(env("DB_PORT", "5432"))
DB_NAME = env("POSTGRES_DB", "pii_rag")
RAG_DSN = f"host={DB_HOST} port={DB_PORT} dbname={DB_NAME} user=rag_user password={env('RAG_PASSWORD', '')}"
VAULT_DSN = f"host={DB_HOST} port={DB_PORT} dbname={DB_NAME} user=vault_user password={env('VAULT_PASSWORD', '')}"
# 로그인 판정(service/auth.py)용 DB 연결. 지금은 관리자 계정(POSTGRES_USER)을 쓴다(2026-11-12 팀 결정, 구조 단순화).
# 고도화 때 infra/init/06_grants.sh 의 app_search(policy.users 읽기, last_login_at 갱신 권한)로 바꾼다:
#   user=app_search password=APP_SEARCH_PASSWORD
AUTH_DSN = f"host={DB_HOST} port={DB_PORT} dbname={DB_NAME} user={env('POSTGRES_USER', 'admin')} password={env('POSTGRES_PASSWORD', '')}"

# 로그인 세션 (service/app.py). SESSION_SECRET 은 쿠키 서명 키로, .env 에만 두고 커밋하지 않는다.
SESSION_SECRET = env("SESSION_SECRET", "")

EMBED_PROVIDER = env("EMBED_PROVIDER", "local")  # api | local | bge-m3
EMBED_API_BASE = env("EMBED_API_BASE", "")
EMBED_API_KEY = env("EMBED_API_KEY", "")
EMBED_MODEL = env("EMBED_MODEL", "text-embedding-3-small")
EMBED_DIM = int(env("EMBED_DIM", "1536"))

LLM_API_BASE = env("LLM_API_BASE", "")
LLM_API_KEY = env("LLM_API_KEY", "")
LLM_MODEL = env("LLM_MODEL", "gpt-4o-mini")

HMAC_KEY = env("HMAC_KEY", "")

DATA_DIR = ROOT / "data" / "holdout"
OUT_DIR = ROOT / "outputs"
