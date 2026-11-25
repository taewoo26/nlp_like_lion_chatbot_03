"""service/audit.py: request_id 형식, 행 구성, 기록 실패 시 대체 파일·오류 로그.

DB 테스트(맨 아래)는 Docker DB와 audit 스키마가 있을 때만 돌고, 한 트랜잭션 안에서 실행한 뒤 롤백한다.
"""
import json
import logging
import re
from datetime import datetime
from unittest.mock import MagicMock

import psycopg
import pytest

from service import audit
from starter import db

USER = {"user_id": "SB260003", "access_level": 1, "team_cd": "DEPT_CREDIT", "position": "팀원"}
TOK = "<PERSON_bd52a13d0d35>"
RESULT = {"masked_query": f"{TOK} 한도 결과", "query_tokens": [TOK], "state": "answered",
          "docs": [{"doc_id": "H0001", "masked_text": "..."}, {"doc_id": "H0002", "masked_text": "..."}]}


@pytest.fixture
def files(tmp_path, monkeypatch):
    monkeypatch.setattr(audit, "FALLBACK_PATH", tmp_path / "audit_fallback.jsonl")
    errors = []
    monkeypatch.setattr(audit, "_log_error", lambda msg, *a: errors.append(msg % a))
    return tmp_path / "audit_fallback.jsonl", errors


def test_request_id_format():
    rid = audit.new_request_id(datetime(2026, 11, 17, 9, 0, tzinfo=audit.KST))
    assert re.fullmatch(r"req_20261117_[0-9a-f]{12}", rid)
    assert audit.new_request_id() != audit.new_request_id()


def test_build_row_from_result():
    row = audit.build_row("req_x", USER, result=RESULT, latency_ms=120)
    assert row["level"] == 1 and row["team_cd"] == "DEPT_CREDIT" and row["position"] == "팀원"
    assert row["masked_query"] == f"{TOK} 한도 결과" and row["query_tokens"] == [TOK]
    assert row["returned_doc_ids"] == ["H0001", "H0002"]
    assert row["state"] == "answered" and row["latency_ms"] == 120
    assert row["recheck_action"] is None and row["recheck_labels"] == {} and row["recheck_detector"] is None


def test_build_row_error_after_masking():
    exc = RuntimeError("down")
    exc.masked_query, exc.query_tokens = f"{TOK} 대출", [TOK]
    row = audit.build_row("req_x", USER, error=exc, latency_ms=5)
    assert row["state"] == "error" and row["masked_query"] == f"{TOK} 대출"
    assert row["query_tokens"] == [TOK] and row["returned_doc_ids"] == []


def test_build_row_error_before_masking():
    row = audit.build_row("req_x", USER, error=RuntimeError("detector"), latency_ms=5)
    assert row["masked_query"] == audit.MASK_FAILED and row["query_tokens"] == []


def test_record_db_success_writes_no_fallback(files, monkeypatch):
    fallback, errors = files
    monkeypatch.setattr(audit, "write_query_log", lambda row, conn=None: None)
    assert audit.record(audit.build_row("req_ok", USER, result=RESULT)) == "db"
    assert not fallback.exists() and errors == []


def test_record_db_failure_goes_to_fallback(files, monkeypatch):
    fallback, errors = files

    def fail(row, conn=None):
        raise psycopg.OperationalError("connection refused 홍길동")   # 메시지는 남기지 않아야 함
    monkeypatch.setattr(audit, "write_query_log", fail)
    row = audit.build_row("req_fb", USER, result=RESULT, latency_ms=10)
    assert audit.record(row) == "fallback"
    lines = fallback.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    saved = json.loads(lines[0])
    assert saved["request_id"] == "req_fb" and saved["state"] == "answered"
    assert saved["policy_version"] is None and saved["db_error"] == "OperationalError"
    assert "홍길동" not in lines[0]
    assert errors == ["query_log 기록 실패 → fallback 저장 request_id=req_fb error=OperationalError"]


def test_record_both_fail_is_lost(files, monkeypatch, tmp_path):
    _, errors = files
    monkeypatch.setattr(audit, "write_query_log", MagicMock(side_effect=psycopg.OperationalError("x")))
    monkeypatch.setattr(audit, "FALLBACK_PATH", tmp_path)            # 폴더라서 열 수 없음
    assert audit.record(audit.build_row("req_lost", USER, result=RESULT)) == "lost"
    assert "fallback 저장도 실패 request_id=req_lost" in errors[0]


def test_write_query_log_sql_and_commit(monkeypatch):
    conn = MagicMock()
    cur = conn.cursor.return_value.__enter__.return_value
    monkeypatch.setattr(audit.db, "search_conn", lambda: conn)
    audit.write_query_log(audit.build_row("req_sql", USER, result=RESULT))
    sql, params = cur.execute.call_args.args
    assert "audit.query_log" in sql and "policy.policy_state" in sql
    assert params["request_id"] == "req_sql" and params["returned_doc_ids"] == ["H0001", "H0002"]
    conn.commit.assert_called_once()
    conn.close.assert_called_once()


def test_error_log_file_is_written(tmp_path, monkeypatch):
    log_path = tmp_path / "server_error.log"
    monkeypatch.setattr(audit, "ERROR_LOG_PATH", log_path)
    for h in list(audit._logger.handlers):
        audit._logger.removeHandler(h)
    try:
        audit._log_error("query_log 기록 실패 → fallback 저장 request_id=%s error=%s", "req_f", "X")
        for h in audit._logger.handlers:
            h.flush()
        assert "request_id=req_f error=X" in log_path.read_text(encoding="utf-8")
    finally:
        for h in list(audit._logger.handlers):
            h.close()
            audit._logger.removeHandler(h)


# ---- 실제 DB (없으면 건너뜀) ----
@pytest.fixture
def conn():
    try:
        c = db.rag_conn()  # 제약 조건 검증용: 넣은 행을 읽어야 해서 소유자 접속 (app_search 권한은 test_db_accounts.py)
    except psycopg.OperationalError as e:
        pytest.skip(f"DB 연결(db.rag_conn)에 실패했습니다: {e}")
    with c.cursor() as cur:
        cur.execute("SELECT to_regclass('audit.query_log') IS NOT NULL")
        if not cur.fetchone()[0]:
            c.close()
            pytest.skip("audit.query_log 가 없습니다 (infra/init/05_audit.sql 실행 필요)")
    yield c
    c.rollback()
    c.close()


@pytest.mark.parametrize("state", ["error", "clarify", "no_result", "search_only", "withheld", "answered"])
def test_db_insert_passes_constraints(conn, state):
    row = audit.build_row(audit.new_request_id(), USER, result=dict(RESULT, state=state), latency_ms=10)
    audit.write_query_log(row, conn)
    with conn.cursor() as cur:
        cur.execute("""SELECT state, masked_query, query_tokens, returned_doc_ids, recheck_labels, policy_version
                       FROM audit.query_log WHERE request_id = %s""", (row["request_id"],))
        got = cur.fetchone()
    assert got[0] == state and got[1] == RESULT["masked_query"]
    assert got[2] == [TOK] and got[3] == ["H0001", "H0002"] and got[4] == {}
    assert got[5] is not None


def test_db_rejects_unknown_state(conn):
    row = audit.build_row(audit.new_request_id(), USER, result=dict(RESULT, state="found"))
    with pytest.raises(psycopg.errors.CheckViolation):
        audit.write_query_log(row, conn)
