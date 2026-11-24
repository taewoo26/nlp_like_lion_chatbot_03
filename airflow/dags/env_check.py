"""환경 점검 DAG: 이미지·마운트·프로젝트 DB 연결이 맞는지 수동으로 확인한다."""
import pendulum
from airflow.sdk import dag, task


@dag(
    dag_id="env_check",
    schedule=None,
    start_date=pendulum.datetime(2026, 11, 1, tz="Asia/Seoul"),
    catchup=False,
    tags=["pii-rag", "check"],
    doc_md="파이썬 버전, 핵심 패키지, 레포 마운트, outputs 쓰기 권한, 프로젝트 DB 접속을 확인합니다.",
)
def env_check():
    @task
    def check():
        import platform
        import psycopg
        from pii_common import PROJECT_ROOT, project_dsn

        print("python", platform.python_version())
        import kiwipiepy, onnxruntime, torch, sentence_transformers, transformers  # noqa: E401
        print("kiwipiepy", kiwipiepy.__version__, "| onnxruntime", onnxruntime.__version__,
              "| torch", torch.__version__, "(cuda:", torch.cuda.is_available(), ")",
              "| sentence-transformers", sentence_transformers.__version__,
              "| transformers", transformers.__version__)

        for name in ("starter", "detection", "models", "outputs"):
            p = PROJECT_ROOT / name
            print(f"{p}: {'있음' if p.exists() else '없음'}")
            if not p.exists():
                raise FileNotFoundError(p)

        probe = PROJECT_ROOT / "outputs" / ".airflow_write_probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        print("outputs 쓰기: 가능")

        with psycopg.connect(project_dsn("INGEST_DSN"), connect_timeout=5) as conn:
            n = conn.execute("SELECT count(DISTINCT doc_id) FROM rag.chunks").fetchone()[0]
        print(f"프로젝트 DB 접속: 가능 (적재 문서 {n}건)")

    check()


env_check()
