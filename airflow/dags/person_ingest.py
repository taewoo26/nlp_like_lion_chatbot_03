"""DAG 1: 문서 적재 (탐지 → 가명처리 → 인물 판정 → 임베딩 → pgvector). 수동 실행.

starter.ingest 를 한 번 실행한다. person_ingest.py 가 한 트랜잭션으로 처리하고
모델 로딩(약 30초)이 반복되지 않도록 단계를 태스크로 나누지 않는다.
"""
from datetime import timedelta

import pendulum
from airflow.providers.standard.operators.bash import BashOperator
from airflow.sdk import Param, dag, task

DATA_PATH_RE = r"^[A-Za-z0-9_./-]+\.jsonl$"


@dag(
    dag_id="person_ingest",
    schedule=None,
    start_date=pendulum.datetime(2026, 11, 1, tz="Asia/Seoul"),
    catchup=False,
    max_active_runs=1,
    default_args={"retries": 0},
    params={
        "data_path": Param("data/holdout/holdout_labeled.jsonl", type="string", pattern=DATA_PATH_RE,
                           description="레포 기준 상대 경로 (.jsonl)"),
        "reset": Param(False, type="boolean",
                       description="true 면 기존 적재를 모두 지우고 다시 적재 (초기 적재·재적재)"),
    },
    tags=["pii-rag", "ingest"],
    doc_md=__doc__,
)
def person_ingest():
    @task
    def preflight(**context):
        import re
        import psycopg
        from pii_common import PROJECT_ROOT, project_dsn

        params = context["params"]
        rel = params["data_path"]
        if not re.match(DATA_PATH_RE, rel) or ".." in rel:
            raise ValueError(f"허용하지 않는 경로: {rel}")
        path = (PROJECT_ROOT / rel).resolve()
        if PROJECT_ROOT.resolve() not in path.parents or not path.is_file():
            raise FileNotFoundError(f"입력 파일이 없습니다: {rel}")
        with path.open(encoding="utf-8") as f:
            docs = sum(1 for line in f if line.strip())

        # reset=true 는 TRUNCATE 권한이 있는 app_reset(RESET_DSN)으로 접속한다 (starter/person_ingest.py)
        key = "RESET_DSN" if params["reset"] else "INGEST_DSN"
        with psycopg.connect(project_dsn(key), connect_timeout=5) as conn:
            before = conn.execute("SELECT count(DISTINCT doc_id) FROM rag.chunks").fetchone()[0]
        print(f"입력 {rel}: 문서 {docs}건 | reset={params['reset']} | 현재 적재 문서 {before}건")
        return {"docs": docs, "before": before}

    ingest = BashOperator(
        task_id="ingest",
        execution_timeout=timedelta(minutes=30),
        bash_command=r"""
set -euo pipefail
cd "$PROJECT_ROOT"
export INGEST_DSN="$(printf '%s' "$INGEST_DSN" | sed -E "s/host=[^ ]+/host=$PROJECT_DB_HOST/")"
export RESET_DSN="$(printf '%s' "$RESET_DSN" | sed -E "s/host=[^ ]+/host=$PROJECT_DB_HOST/")"
python -m starter.ingest --data "{{ params.data_path }}" {{ '--reset' if params.reset else '' }}
""",
    )

    @task
    def postcheck(pre, **context):
        import json
        from pathlib import Path
        import psycopg
        from pii_common import PROJECT_ROOT, project_dsn

        stem = Path(context["params"]["data_path"]).stem
        report = PROJECT_ROOT / "outputs" / f"person_resolution_pseudo_{stem}.json"
        started = context["dag_run"].start_date.timestamp()
        if not report.is_file() or report.stat().st_mtime < started:
            raise RuntimeError(f"이번 실행의 판정 결과 파일이 없습니다: {report.name}")
        r = json.loads(report.read_text(encoding="utf-8"))

        with psycopg.connect(project_dsn("INGEST_DSN"), connect_timeout=5) as conn:
            after = conn.execute("SELECT count(DISTINCT doc_id) FROM rag.chunks").fetchone()[0]
            look_up = conn.execute("SELECT count(*) FROM rag.look_up").fetchone()[0]

        summary = {"input_docs": pre["docs"], "docs_before": pre["before"], "docs_after": after,
                   "candidates": r["candidates"], "resolved": r["resolved"], "registry": r["registry"],
                   "look_up_report": r["look_up"], "look_up_db": look_up, "review": len(r["review"])}
        print("적재 결과:", json.dumps(summary, ensure_ascii=False))
        if after == 0 or r["candidates"] == 0:
            raise RuntimeError("적재 문서나 인물 후보가 0건입니다")
        if r["look_up"] != look_up:
            raise RuntimeError("판정 결과의 look_up 수와 DB 행 수가 다릅니다")
        return summary

    pre = preflight()
    pre >> ingest
    ingest >> postcheck(pre)


person_ingest()
