"""출력 재검사 (starter/recheck.py)와 chat.answer()·query_log 연결.

탐지 모델 없이 실행한다: 가짜 탐지기가 정해 둔 글자를 찾아 준다.
- 이미 가린 구간(토큰·비공개 표시)과 지역만 있는 주소는 적발로 보지 않는다
- masked: 적재와 같은 규칙으로 토큰화(같은 값 = 적재 때와 같은 토큰), 운전면허번호는 비공개 표시
- blocked: 차단 라벨이 나오면 답변을 안내 문구로, 문서 조각을 모두 비움 (search_only 포함) → state withheld
- 기록: recheck → query_log 의 recheck_action / recheck_labels / recheck_detector (원문 없음)
"""
from unittest.mock import Mock

import psycopg
import pytest

from detection.base import Span
from service import audit
from starter import chat, db, pii_pipeline, recheck

TOK = "<PERSON_bd52a13d0d35>"
NO_META = {"regions": [], "ask_region": None, "roles": [], "excluded": 0}
USER = {"user_id": "SB260001", "access_level": 2, "team_cd": "DEPT_RISK", "position": "팀장"}


class FakeDetector:
    name = "fake_detector"

    def __init__(self, targets):
        self.targets = targets               # {글자: 라벨}

    def detect(self, text):
        spans = []
        for word, label in self.targets.items():
            start = text.find(word)
            while start != -1:
                spans.append(Span(start, start + len(word), label, word))
                start = text.find(word, start + 1)
        return sorted(spans, key=lambda s: s.start)


@pytest.fixture
def fake(monkeypatch):
    """재검사를 켜고 가짜 탐지기를 쓴다. fake({"김철수": "person"}) 처럼 찾을 글자를 정한다."""
    monkeypatch.setenv("RECHECK", "on")
    monkeypatch.delenv("RECHECK_BLOCK_LABELS", raising=False)
    monkeypatch.delenv("RECHECK_DETECTOR", raising=False)
    monkeypatch.setattr(pii_pipeline.config, "HMAC_KEY", "synthetic-test-key")

    def use(targets):
        monkeypatch.setattr(recheck, "_detector", lambda policy: FakeDetector(targets))
    return use


def _token(label, value):
    return pii_pipeline.make_token(label, pii_pipeline.normalize_value(label, value))


# ---- find ----
def test_find_skips_existing_tokens_and_marks(fake):
    fake({TOK: "person", "PERSON": "person", "운전면허번호 비공개": "driver_license", "김철수": "person"})
    text = f"{TOK} 고객, [운전면허번호 비공개], 담당 김철수"
    assert [text[s["start"]:s["end"]] for s in recheck.find(text)] == ["김철수"]


def test_find_skips_region_only_address(fake):
    fake({"서울특별시 강남구": "address", "대구 수성구 범어로 12": "address"})
    text = "서울특별시 강남구 지점, 대구 수성구 범어로 12 거주"
    assert [text[s["start"]:s["end"]] for s in recheck.find(text)] == ["대구 수성구 범어로 12"]


def test_find_empty_text(fake):
    fake({"김철수": "person"})
    assert recheck.find(None) == [] and recheck.find("") == []


# ---- check ----
def _result(answer, *doc_texts):
    return {"answer": answer, "docs": [{"doc_id": f"H{i:04d}", "masked_text": t} for i, t in enumerate(doc_texts, 1)]}


def test_check_none(fake):
    fake({"김철수": "person"})
    res = _result(f"네, {TOK} 고객 1건입니다.", f"{TOK} 한도 상향")
    assert recheck.check(res) == {"action": "none", "labels": {}, "detector": "fake_detector"}
    assert res["answer"] == f"네, {TOK} 고객 1건입니다."


def test_check_masked_uses_same_token_as_ingest(fake):
    fake({"김철수": "person", "010-1234-5678": "phone_number", "12-34-567890-12": "driver_license"})
    res = _result("담당 김철수, 연락처 010-1234-5678", "김철수 면허 12-34-567890-12", f"{TOK} 정상 문서")
    rc = recheck.check(res)
    assert rc["action"] == "masked"
    assert rc["labels"] == {"person": 2, "phone_number": 1, "driver_license": 1}   # 답변 + 문서 합계
    person = _token("person", "김철수")
    assert res["answer"] == f"담당 {person}, 연락처 {_token('phone_number', '010-1234-5678')}"
    assert res["docs"][0]["masked_text"] == f"{person} 면허 {pii_pipeline.DELETED_TOKEN}"
    assert res["docs"][1]["masked_text"] == f"{TOK} 정상 문서"
    assert "김철수" not in str(res) and "5678" not in str(res)


def test_check_masked_writes_nothing_to_vault(fake, monkeypatch):
    fake({"김철수": "person"})
    monkeypatch.setattr(db, "vault_conn", lambda: pytest.fail("재검사는 vault 에 접근하지 않아야 함"))
    monkeypatch.setattr(db, "restore_conn", lambda: pytest.fail("재검사는 vault 에 접근하지 않아야 함"))
    assert recheck.check(_result("담당 김철수"))["action"] == "masked"


def test_check_blocked_hides_answer_and_snippets(fake):
    fake({"110-234-567890": "account_number", "김철수": "person"})
    res = _result("김철수 계좌 110-234-567890", "정상 문서", "계좌 110-234-567890")
    rc = recheck.check(res)
    assert rc["action"] == "blocked" and rc["labels"] == {"account_number": 2, "person": 1}
    assert res["answer"] == recheck.BLOCKED_MESSAGE
    assert [d["masked_text"] for d in res["docs"]] == ["", ""]
    assert [d["doc_id"] for d in res["docs"]] == ["H0001", "H0002"]          # 문서 ID 는 남김


def test_block_labels_can_be_turned_off(fake, monkeypatch):
    monkeypatch.setenv("RECHECK_BLOCK_LABELS", "")
    fake({"110-234-567890": "account_number"})
    res = _result("계좌 110-234-567890")
    assert recheck.check(res)["action"] == "masked"
    assert res["answer"] == f"계좌 {_token('account_number', '110-234-567890')}"


def test_recheck_off_changes_nothing(monkeypatch):
    monkeypatch.setattr(recheck, "_detector", lambda policy: pytest.fail("꺼져 있으면 탐지하지 않아야 함"))
    res = _result("김철수")
    assert recheck.check(res) is None and res["answer"] == "김철수"


def test_unknown_detector_setting(fake, monkeypatch):
    fake({})
    monkeypatch.setenv("RECHECK_DETECTOR", "veil")
    with pytest.raises(ValueError):
        recheck.check(_result("김철수"))


# ---- chat.answer 연결 ----
def _search(monkeypatch, docs):
    monkeypatch.setattr(chat, "search", lambda *a, **kw: (f"{TOK} 한도", [TOK], docs, NO_META))


def _llm(monkeypatch, text):
    resp = Mock(status_code=200)
    resp.json.return_value = {"choices": [{"message": {"content": text}}]}
    monkeypatch.setattr(chat.config, "LLM_API_KEY", "synthetic-test-key")
    monkeypatch.setattr(chat.requests, "post", Mock(return_value=resp))


def test_answered_with_leak_is_masked(fake, monkeypatch):
    fake({"김철수": "person"})
    _search(monkeypatch, [{"doc_id": "H0001", "masked_text": f"{TOK} 담당 김철수"}])
    _llm(monkeypatch, f"네, 1건입니다. [H0001] {TOK} 고객, 담당 김철수")
    res = chat.answer("질문")
    assert res["state"] == "answered" and res["recheck"]["action"] == "masked"
    assert "김철수" not in res["answer"] and "김철수" not in res["docs"][0]["masked_text"]


def test_answered_blocked_becomes_withheld(fake, monkeypatch):
    fake({"900101-1234567": "resident_registration_number"})
    _search(monkeypatch, [{"doc_id": "H0001", "masked_text": f"{TOK} 한도"}])
    _llm(monkeypatch, "주민번호 900101-1234567")
    res = chat.answer("질문")
    assert res["state"] == "withheld" and res["recheck"]["action"] == "blocked"
    assert res["answer"] == recheck.BLOCKED_MESSAGE


def test_search_only_rechecks_snippets(fake, monkeypatch):
    fake({"김철수": "person", "4111-1111-1111-1111": "card_number"})
    monkeypatch.setattr(chat.config, "LLM_API_KEY", "")
    _search(monkeypatch, [{"doc_id": "H0001", "masked_text": "담당 김철수"}])
    res = chat.answer("질문")
    assert res["state"] == "search_only" and res["answer"] is None and res["recheck"]["action"] == "masked"
    _search(monkeypatch, [{"doc_id": "H0001", "masked_text": "카드 4111-1111-1111-1111"}])
    res = chat.answer("질문")
    assert res["state"] == "withheld" and res["docs"][0]["masked_text"] == ""


def test_no_result_is_not_rechecked(fake, monkeypatch):
    fake({"김철수": "person"})
    _search(monkeypatch, [])
    assert chat.answer("질문")["recheck"] is None


def test_clarify_rechecks_snippets_only(fake, monkeypatch):
    fake({"김철수": "person", "후보": "person"})
    docs = [{"doc_id": "H0001", "masked_text": "담당 김철수"}]
    monkeypatch.setattr(chat, "search", lambda *a, **kw: (f"{TOK} 한도", [TOK], docs, dict(NO_META, ask_person=True)))
    res = chat.answer("질문")                                   # 인물 되묻기는 문서 목록을 함께 돌려준다
    assert res["state"] == "clarify" and res["recheck"]["labels"] == {"person": 1}   # 되묻기 문구('후보')는 안 봄
    assert "김철수" not in res["docs"][0]["masked_text"] and "후보가 여러 명" in res["answer"]


def test_clarify_without_docs_is_not_rechecked(fake, monkeypatch):
    fake({"중구": "address"})
    monkeypatch.setattr(chat, "search", lambda *a, **kw: ("중구 한도", [], [], dict(NO_META, ask_region="중구")))
    res = chat.answer("질문")
    assert res["state"] == "clarify" and res["recheck"] is None


def test_recheck_error_fails_closed(fake, monkeypatch):
    monkeypatch.setattr(recheck, "_detector", Mock(side_effect=RuntimeError("model down")))
    _search(monkeypatch, [{"doc_id": "H0001", "masked_text": "담당 김철수"}])
    _llm(monkeypatch, "담당 김철수")
    with pytest.raises(RuntimeError) as ei:           # 검사 못 한 답변은 내보내지 않는다 → /api/chat 이 error 로 기록
        chat.answer("질문")
    assert ei.value.masked_query == f"{TOK} 한도"


# ---- query_log 기록 ----
def test_build_row_maps_recheck(fake, monkeypatch):
    fake({"김철수": "person"})
    _search(monkeypatch, [{"doc_id": "H0001", "masked_text": "담당 김철수"}])
    _llm(monkeypatch, "담당 김철수")
    row = audit.build_row(audit.new_request_id(), USER, result=chat.answer("질문"), latency_ms=5)
    assert row["recheck_action"] == "masked" and row["recheck_labels"] == {"person": 2}
    assert row["recheck_detector"] == "fake_detector" and "김철수" not in str(row)


def test_build_row_without_recheck():
    row = audit.build_row(audit.new_request_id(), USER, result={
        "masked_query": "q", "query_tokens": [], "docs": [], "state": "no_result", "recheck": None})
    assert (row["recheck_action"], row["recheck_labels"], row["recheck_detector"]) == (None, {}, None)


@pytest.fixture
def conn():
    try:
        c = db.rag_conn()
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


@pytest.mark.parametrize("action, labels", [("none", {}), ("masked", {"person": 2}), ("blocked", {"card_number": 1})])
def test_db_accepts_recheck_values(conn, action, labels):
    result = {"masked_query": f"{TOK} 한도", "query_tokens": [TOK], "docs": [{"doc_id": "H0001"}],
              "state": "withheld" if action == "blocked" else "answered",
              "recheck": {"action": action, "labels": labels, "detector": "koelectra_post_kiwi_split_v3"}}
    row = audit.build_row(audit.new_request_id(), USER, result=result, latency_ms=5)
    audit.write_query_log(row, conn)
    with conn.cursor() as cur:
        cur.execute("SELECT recheck_action, recheck_labels, recheck_detector FROM audit.query_log WHERE request_id = %s",
                    (row["request_id"],))
        assert cur.fetchone() == (action, labels, "koelectra_post_kiwi_split_v3")
