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
