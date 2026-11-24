"""DAG 들이 같이 쓰는 도우미. 프로젝트 코드는 태스크 안에서만 import 한다 (DAG 파싱을 가볍게)."""
import os
import re
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

PROJECT_ROOT = Path(os.environ.get("PROJECT_ROOT", "/opt/pii-rag"))
KST = ZoneInfo("Asia/Seoul")


def use_project():
    """프로젝트 패키지(starter 등)를 import 할 수 있게 한다."""
    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT))


def project_dsn(key):
    """.env 의 DSN 에서 host 만 PROJECT_DB_HOST 로 바꾼다 (.env 는 로컬 실행용 127.0.0.1)."""
    dsn = os.environ.get(key, "")
    if not dsn:
        raise RuntimeError(f"{key} 가 설정되지 않았습니다 (레포 루트 .env 확인)")
    host = os.environ.get("PROJECT_DB_HOST")
    return re.sub(r"host=\S+", f"host={host}", dsn) if host else dsn
