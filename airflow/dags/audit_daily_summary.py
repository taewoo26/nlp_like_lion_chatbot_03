"""DAG 2: 감사 로그 일일 요약. 매일 00:10(서울)에 전날 하루치를 집계해 JSON 파일로 남긴다.

DB 는 읽기만 한다(세션을 read-only 로 연다). 결과는 DB 에 쓰지 않고
outputs/audit_reports/audit_summary_<날짜>.json 에만 저장한다.
보고서에는 숫자만 넣는다: masked_query·토큰·원문은 넣지 않는다.
"""
from datetime import timedelta

import pendulum
from airflow.sdk import Param, dag, task

# 이상 징후 기준 (임시값. 시연 데이터를 보고 조정)
ERROR_RATE_MAX = 0.10          # 하루 질문 중 error 비율
RESTORE_PER_USER_MAX = 50      # 한 사용자의 하루 복원 건수 (full + partial)


@dag(
    dag_id="audit_daily_summary",
    schedule="10 0 * * *",
    start_date=pendulum.datetime(2026, 11, 24, tz="Asia/Seoul"),
    catchup=False,
    max_active_runs=1,
    default_args={"retries": 1, "retry_delay": timedelta(minutes=5)},
    params={
        "target_date": Param(None, type=["null", "string"], format="date",
                             description="집계할 날짜 YYYY-MM-DD. 비우면 실행일의 전날"),
    },
    tags=["pii-rag", "audit"],
    doc_md=__doc__,
)
def audit_daily_summary():
    @task
    def summarize(**context):
        import json
        from datetime import date, datetime, time
        import psycopg
        from pii_common import KST, PROJECT_ROOT, use_project

        given = context["params"].get("target_date")
        if given:
            day = date.fromisoformat(given)
        else:
            ref = context.get("logical_date") or pendulum.now("Asia/Seoul")
            day = pendulum.instance(ref).in_timezone("Asia/Seoul").date() - timedelta(days=1)
        start = datetime.combine(day, time.min, tzinfo=KST)
        end = start + timedelta(days=1)
        w = (start, end)

        use_project()
        from starter import config          # RAG_DSN: DB_HOST(compose 에서 PROJECT_DB_HOST)로 조립

        def rows(cur, sql):
            cur.execute(sql, w)
            return cur.fetchall()

        with psycopg.connect(config.RAG_DSN, connect_timeout=5) as conn:
            conn.read_only = True
            cur = conn.cursor()
            q = "FROM audit.query_log WHERE created_at >= %s AND created_at < %s"
            total, users, avg_ms, p95_ms = rows(cur, f"""
                SELECT count(*), count(DISTINCT user_id), avg(latency_ms),
                       percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_ms) {q}""")[0]
            by_state = dict(rows(cur, f"SELECT state, count(*) {q} GROUP BY state"))
            by_level = {str(k): v for k, v in rows(cur, f"SELECT level, count(*) {q} GROUP BY level")}
            recheck = dict(rows(cur, f"SELECT coalesce(recheck_action, 'none'), count(*) {q} GROUP BY 1"))
            labels = {k: int(v) for k, v in rows(cur, """
                SELECT e.key, sum(e.value::numeric)
                  FROM audit.query_log ql, jsonb_each_text(ql.recheck_labels) e
                 WHERE ql.created_at >= %s AND ql.created_at < %s GROUP BY e.key""")}

            r = "FROM audit.restore_log WHERE created_at >= %s AND created_at < %s"
            by_mode = dict(rows(cur, f"SELECT mode, count(*) {r} GROUP BY mode"))
            by_user = {}
            for uid, mode, n in rows(cur, f"SELECT user_id, mode, count(*) {r} GROUP BY 1, 2"):
                by_user.setdefault(uid, {})[mode] = n
            by_label = dict(rows(cur, f"SELECT label, count(*) {r} GROUP BY label"))
            orphan_full = rows(cur, """
                SELECT count(*) FROM audit.restore_log rl
                 WHERE rl.created_at >= %s AND rl.created_at < %s AND rl.mode = 'full'
                   AND NOT EXISTS (SELECT 1 FROM audit.query_log ql WHERE ql.request_id = rl.request_id)""")[0][0]

            p = "FROM audit.policy_change_log WHERE created_at >= %s AND created_at < %s"
            policy = dict(rows(cur, f"SELECT entity, count(*) {p} GROUP BY entity"))

        # DB 기록에 실패해 파일로만 남은 질의 로그 (내용은 읽지 않고 날짜만 센다)
        fallback = 0
        fb_path = PROJECT_ROOT / "outputs" / "audit_fallback.jsonl"
        if fb_path.is_file():
            with fb_path.open(encoding="utf-8") as f:
                for line in f:
                    try:
                        ts = datetime.fromisoformat(json.loads(line)["created_at"])
                    except (ValueError, KeyError, TypeError):
                        continue
                    if ts.tzinfo is None:
                        ts = ts.replace(tzinfo=KST)
                    fallback += start <= ts < end

        return {
            "date": day.isoformat(),
            "generated_at": pendulum.now("Asia/Seoul").isoformat(),
            "query_log": {
                "total": total, "users": users,
                "latency_ms": {"avg": round(float(avg_ms), 1) if avg_ms is not None else None,
                               "p95": round(float(p95_ms), 1) if p95_ms is not None else None},
                "by_state": by_state, "by_level": by_level,
                "recheck_action": recheck, "recheck_labels": labels,
            },
            "restore_log": {
                "total": sum(by_mode.values()), "by_mode": by_mode,
                "by_user": by_user, "by_label": by_label, "full_without_query_log": orphan_full,
            },
            "policy_change_log": {"total": sum(policy.values()), "by_entity": policy},
            "fallback_rows": fallback,
        }

    @task
    def check_anomalies(s):
        found = []
        ql, rl = s["query_log"], s["restore_log"]
        if ql["total"]:
            rate = ql["by_state"].get("error", 0) / ql["total"]
            if rate > ERROR_RATE_MAX:
                found.append({"rule": "error_rate", "value": round(rate, 3), "max": ERROR_RATE_MAX})
        if ql["recheck_action"].get("blocked"):
            found.append({"rule": "recheck_blocked", "value": ql["recheck_action"]["blocked"]})
        for uid, modes in rl["by_user"].items():
            n = sum(modes.values())
            if n > RESTORE_PER_USER_MAX:
                found.append({"rule": "restore_per_user", "user_id": uid, "value": n, "max": RESTORE_PER_USER_MAX})
        if rl["full_without_query_log"]:
            found.append({"rule": "full_restore_without_query_log", "value": rl["full_without_query_log"]})
        if s["fallback_rows"]:
            found.append({"rule": "audit_fallback_rows", "value": s["fallback_rows"]})
        if s["policy_change_log"]["total"]:
            found.append({"rule": "policy_changed", "value": s["policy_change_log"]["total"]})
        return {**s, "anomalies": found}

    @task
    def save_report(report):
        import json
        from pii_common import PROJECT_ROOT

        out = PROJECT_ROOT / "outputs" / "audit_reports" / f"audit_summary_{report['date']}.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"{report['date']} 질문 {report['query_log']['total']}건, 복원 {report['restore_log']['total']}건, "
              f"이상 징후 {len(report['anomalies'])}건 → {out.relative_to(PROJECT_ROOT)}")
        return str(out)

    save_report(check_anomalies(summarize()))


audit_daily_summary()
