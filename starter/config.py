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
RAG_DSN = env('RAG_DSN') or f"host={DB_HOST} port={DB_PORT} dbname={DB_NAME} user=rag_user password={env('RAG_PASSWORD', '')}"
VAULT_DSN = env('VAULT_DSN') or f"host={DB_HOST} port={DB_PORT} dbname={DB_NAME} user=vault_user password={env('VAULT_PASSWORD', '')}"
# 웹 서비스(로그인 판정·실명 복원·감사 로그 기록)용 DB 연결: infra/init/06_grants.sh 의 app_search 계정.
#   SEARCH_DSN 을 직접 주거나, APP_SEARCH_PASSWORD 만 두면 아래처럼 조립한다.
# 둘 다 없으면 이전처럼 관리자 계정(POSTGRES_USER)으로 접속한다(팀원 .env 가 아직 없을 때의 임시 대체, db.py 가 경고를 남김).
SEARCH_DSN = env('SEARCH_DSN') or (
    f"host={DB_HOST} port={DB_PORT} dbname={DB_NAME} user=app_search password={env('APP_SEARCH_PASSWORD')}"
    if env('APP_SEARCH_PASSWORD') else '')
# 관리자 화면(service/app.py /api/admin/*)용: infra/init/08_admin_grants.sh 의 app_admin 계정.
#   ADMIN_DSN 을 직접 주거나 APP_ADMIN_PASSWORD 만 두면 조립한다. 둘 다 없으면 관리자 계정으로 대체(db.py 가 경고).
ADMIN_DSN = env('ADMIN_DSN') or (
    f"host={DB_HOST} port={DB_PORT} dbname={DB_NAME} user=app_admin password={env('APP_ADMIN_PASSWORD')}"
    if env('APP_ADMIN_PASSWORD') else '')
_SUPERUSER_DSN = f"host={DB_HOST} port={DB_PORT} dbname={DB_NAME} user={env('POSTGRES_USER', 'admin')} password={env('POSTGRES_PASSWORD', '')}"
AUTH_DSN = SEARCH_DSN or _SUPERUSER_DSN

# 로그인 세션 (service/app.py). SESSION_SECRET 은 쿠키 서명 키로, .env 에만 두고 커밋하지 않는다.
SESSION_SECRET = env("SESSION_SECRET", "")

EMBED_PROVIDER = env("EMBED_PROVIDER", "local")  # api | local | bge-m3
EMBED_API_BASE = env("EMBED_API_BASE", "")
EMBED_API_KEY = env("EMBED_API_KEY", "")
EMBED_MODEL = env("EMBED_MODEL", "text-embedding-3-small")
EMBED_DIM = int(env("EMBED_DIM", "384"))  # 팀 기본: e5-small-ko-v2 (384차원). .env.example·compose.yaml 과 같은 값

LLM_API_BASE = env("LLM_API_BASE", "")
LLM_API_KEY = env("LLM_API_KEY", "")
LLM_MODEL = env("LLM_MODEL", "gpt-4o-mini")

HMAC_KEY = env("HMAC_KEY", "")

DATA_DIR = ROOT / "data" / "holdout"
OUT_DIR = ROOT / "outputs"
