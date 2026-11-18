"""/api/chat 의 감사 로그 연결과 화면 상태값 (DB·모델 없이 실행).

- 질문마다 audit.record 가 정확히 1번 불린다 (정상·예외 모두)
- 화면 state: answered→found, search_only→nollm, 나머지→sorry
- 기록이 실패해도("fallback"/"lost") 답변은 그대로 나간다
- 기록되는 행에 질문 원문이 없다
"""
import re

import pytest
from fastapi.testclient import TestClient

from service import app as app_module

USER = {"user_id": "SB260003", "user_name": "가상", "team_cd": "DEPT_CREDIT", "team_name": "여신팀",
        "position": "팀원", "grade": "대리", "access_level": 1, "is_admin": False}
RAW = "홍길동 고객 한도 결과"
MASKED = "<PERSON_bd52a13d0d35> 고객 한도 결과"
TOK = "<PERSON_bd52a13d0d35>"


@pytest.fixture
def env(monkeypatch):
    s = {"rows": [], "record_result": "db", "answer": None}
    monkeypatch.setattr(app_module.auth, "get_active_user", lambda uid, conn=None: dict(USER))
    monkeypatch.setattr(app_module.auth, "authenticate",
                        lambda u, p, conn=None: app_module.auth.LoginResult(True, app_module.auth.OK, user=dict(USER)))

    def fake_record(row, conn=None):
        s["rows"].append(row)
        return s["record_result"]
    monkeypatch.setattr(app_module.audit, "record", fake_record)
    monkeypatch.setattr(app_module.chat, "answer", lambda q, k: s["answer"](q, k))
    client = TestClient(app_module.app)
    client.post("/api/login", json={"user_id": "SB260003", "password": "x"})
    s["client"] = client
    return s


def _result(state, docs=True, answer="네, 1건 있습니다."):
    return lambda q, k: {"masked_query": MASKED, "query_tokens": [TOK], "answer": answer, "notice": None,
                         "docs": [{"doc_id": "H0001", "masked_text": f"{TOK} 한도 상향"}] if docs else [],
                         "state": state}


@pytest.mark.parametrize("state, ui", [
    ("answered", "found"), ("search_only", "nollm"), ("withheld", "sorry"),
    ("no_result", "sorry"), ("clarify", "sorry")])
def test_state_mapping_and_one_log_row(env, state, ui):
    env["answer"] = _result(state)
    r = env["client"].post("/api/chat", json={"query": RAW})
    assert r.status_code == 200 and r.json()["state"] == ui
    assert len(env["rows"]) == 1
    row = env["rows"][0]
    assert row["state"] == state and row["request_id"] == r.json()["request_id"]
    assert re.fullmatch(r"req_\d{8}_[0-9a-f]{12}", row["request_id"])
    assert row["user_id"] == "SB260003" and row["level"] == 1
    assert row["team_cd"] == "DEPT_CREDIT" and row["position"] == "팀원"
    assert row["masked_query"] == MASKED and row["returned_doc_ids"] == ["H0001"]
    assert isinstance(row["latency_ms"], int) and row["latency_ms"] >= 0
    assert "홍길동" not in str(row)


def test_error_before_masking(env):
    def boom(q, k):
        raise RuntimeError(f"detector failed on {q}")      # 메시지에 원문이 섞여도 남지 않아야 함
    env["answer"] = boom
    r = env["client"].post("/api/chat", json={"query": RAW})
    assert r.status_code == 500 and "홍길동" not in r.text
    row = env["rows"][0]
    assert row["state"] == "error" and row["masked_query"] == "[MASK_FAILED]" and row["query_tokens"] == []
    assert "홍길동" not in str(row)


def test_error_after_masking(env):
    def boom(q, k):
        exc = ConnectionError("llm down")
        exc.masked_query, exc.query_tokens = MASKED, [TOK]
        raise exc
    env["answer"] = boom
    r = env["client"].post("/api/chat", json={"query": RAW})
    assert r.status_code == 500
    row = env["rows"][0]
    assert row["state"] == "error" and row["masked_query"] == MASKED and row["query_tokens"] == [TOK]
    assert row["returned_doc_ids"] == []


@pytest.mark.parametrize("outcome", ["fallback", "lost"])
def test_answer_returned_even_if_logging_fails(env, outcome):
    env["answer"], env["record_result"] = _result("answered"), outcome
    r = env["client"].post("/api/chat", json={"query": RAW})
    assert r.status_code == 200 and r.json()["answer"] == "네, 1건 있습니다."


def test_not_logged_in_is_not_logged(env):
    env["client"].post("/api/logout")
    env["answer"] = _result("answered")
    assert env["client"].post("/api/chat", json={"query": RAW}).status_code == 401
    assert env["rows"] == []
