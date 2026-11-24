"""출력 재검사 (starter/recheck.py)와 chat.answer()·query_log 연결.

탐지 모델 없이 실행한다: 가짜 탐지기가 정해 둔 글자를 찾아 준다.
- 이미 가린 구간(토큰·비공개 표시)과 지역만 있는 주소는 적발로 보지 않는다
- masked: 적재와 같은 규칙으로 토큰화(같은 값 = 적재 때와 같은 토큰), 운전면허번호는 비공개 표시
- 문서는 앞 200자만 검사해 snippet(180자)을 만든다. 답변은 전체를 검사한다
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


def test_find_skips_words_that_are_not_names(fake):
    fake({"방문처": "person", "소명자": "person", "조모상": "person", "반송": "person", "인사팀": "person", "김철수": "person"})
    text = "방문처: 협력사 사업장\n소명자 김철수, 조모상 휴가, 우편물 반송, 인사팀 과장"
    assert [text[s["start"]:s["end"]] for s in recheck.find(text)] == ["김철수"]


def test_find_keeps_messenger_speaker_names(fake):
    fake({"김서연": "person"})                                    # '줄 맨 앞 + 콜론' 이어도 실제 이름은 남긴다
    text = "김서연: 승현아, 오늘 회의 자료 공유했어"
    assert [text[s["start"]:s["end"]] for s in recheck.find(text)] == ["김서연"]


def test_find_hides_markdown_bold_from_detector(monkeypatch):
    seen = []

    class Recorder(FakeDetector):
        def detect(self, text):
            seen.append(text)
            return super().detect(text)
    monkeypatch.setenv("RECHECK", "on")
    monkeypatch.setattr(recheck, "_detector", lambda policy: Recorder({"김민준": "person"}))
    text = "- **방문처**: 협력사 사업장\n- **참석자**: 김민준, 010-****-1234"
    found = recheck.find(text)
    assert seen == ["-   방문처  : 협력사 사업장\n-   참석자  : 김민준, 010-****-1234"]   # 길이 같음, '****' 는 그대로
    assert [text[s["start"]:s["end"]] for s in found] == ["김민준"]                  # 위치는 원래 글 기준


def test_find_empty_text(fake):
    fake({"김철수": "person"})
    assert recheck.find(None) == [] and recheck.find("") == []


# ---- check ----
def _result(answer, *doc_texts):
    return {"answer": answer, "docs": [{"doc_id": f"H{i:04d}", "masked_text": t} for i, t in enumerate(doc_texts, 1)]}


def test_check_none(fake):
    fake({"김철수": "person"})
    res = _result(f"네, {TOK} 고객 1건입니다.", f"{TOK} 한도 상향")
    rc = recheck.check(res)
    assert {k: rc[k] for k in ("action", "labels", "detector")} == {"action": "none", "labels": {}, "detector": "fake_detector"}
    assert isinstance(rc["ms"], int) and rc["ms"] >= 0
    assert res["answer"] == f"네, {TOK} 고객 1건입니다."


def test_check_masked_uses_same_token_as_ingest(fake):
    fake({"김철수": "person", "010-1234-5678": "phone_number", "12-34-567890-12": "driver_license"})
    res = _result("담당 김철수, 연락처 010-1234-5678", "김철수 면허 12-34-567890-12", f"{TOK} 정상 문서")
    rc = recheck.check(res)
    assert rc["action"] == "masked"
    assert rc["labels"] == {"person": 2, "phone_number": 1, "driver_license": 1}   # 답변 + 문서 합계
    person = _token("person", "김철수")
    assert res["answer"] == f"담당 {person}, 연락처 {_token('phone_number', '010-1234-5678')}"
    assert res["docs"][0]["snippet"] == f"{person} 면허 {pii_pipeline.DELETED_TOKEN}"
    assert res["docs"][1]["snippet"] == f"{TOK} 정상 문서"
    out = str([res["answer"]] + [d["snippet"] for d in res["docs"]])   # 밖으로 나가는 것: 답변 + 조각
    assert "김철수" not in out and "5678" not in out


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
    assert [d["snippet"] for d in res["docs"]] == ["", ""] and [d["masked_text"] for d in res["docs"]] == ["", ""]
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


# ---- 문서 조각: 앞 200자만 검사 ----
def test_snippet_checks_only_window(fake):
    fake({"김철수": "person", "이영희": "person"})
    text = "가" * 100 + " 김철수 " + "나" * 200 + " 이영희"            # 이영희는 200자 밖 → 화면에 나가지 않음
    res = _result("정상 답변", text)
    rc = recheck.check(res)
    assert rc["labels"] == {"person": 1}                               # 이영희는 검사하지 않음
    snip = res["docs"][0]["snippet"]
    assert len(snip) == recheck.SNIPPET_CHARS and "김철수" not in snip and _token("person", "김철수") in snip
    assert res["docs"][0]["masked_text"] == text                        # 전체 글은 건드리지 않음 (내보내지 않음)


def test_name_across_snippet_edge_is_masked(fake):
    fake({"김철수": "person"})
    text = "가" * 178 + "김철수" + "나" * 50                             # 178~181: 180자 경계에 걸침
    res = _result(None, text)
    recheck.check(res, check_answer=False)
    snip = res["docs"][0]["snippet"]
    assert snip == "가" * 178 + _token("person", "김철수")             # 토큰을 반으로 자르지 않음


def test_window_does_not_cut_token():
    text = "가" * 190 + TOK + "나" * 30                                   # 토큰이 190~211: 200자 경계에 걸침
    assert recheck.window(text) == "가" * 190 + TOK
    assert recheck.window("짧은 글") == "짧은 글" and recheck.window(None) == ""


def test_cut_keeps_whole_token():
    assert recheck.cut("가" * 175 + TOK + "나", 180) == "가" * 175 + TOK
    assert recheck.cut("가" * 300, 180) == "가" * 180


def test_snippet_when_recheck_off(monkeypatch):
    monkeypatch.setattr(chat, "search", lambda *a, **kw: ("q", [], [{"doc_id": "H0001", "masked_text": "가" * 300}], NO_META))
    monkeypatch.setattr(chat.config, "LLM_API_KEY", "")
    res = chat.answer("질문")
    assert res["recheck"] is None and res["docs"][0]["snippet"] == "가" * recheck.SNIPPET_CHARS


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
    assert "김철수" not in res["answer"] and "김철수" not in res["docs"][0]["snippet"]


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
    assert res["state"] == "withheld" and res["docs"][0]["snippet"] == ""


def test_no_result_is_not_rechecked(fake, monkeypatch):
    fake({"김철수": "person"})
    _search(monkeypatch, [])
    assert chat.answer("질문")["recheck"] is None


def test_clarify_rechecks_snippets_only(fake, monkeypatch):
    fake({"김철수": "person", "후보": "person"})
    docs = [{"doc_id": "H0001", "masked_text": "담당 김철수"}]
    monkeypatch.setattr(chat, "search", lambda *a, **kw: (f"{TOK} 한도", [TOK], docs, dict(NO_META, ask_person=True)))
    res = chat.answer("질문")                                   # 인물 되묻기는 문서를 숨긴다 (재질문 B 그룹)
    assert res["state"] == "clarify" and res["docs"] == [] and res["recheck"] is None   # 보낼 문서가 없어 재검사할 것도 없음
    assert "김철수" not in res["answer"] and res["answer"].startswith("이 질문만으로는")


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


def test_recheck_time_is_logged_not_stored(fake, monkeypatch):
    from fastapi.testclient import TestClient
    from service import app as app_module
    fake({"김철수": "person"})
    monkeypatch.setattr(app_module.auth, "get_active_user", lambda uid, conn=None: dict(USER, user_name="가상"))
    monkeypatch.setattr(app_module.auth, "authenticate", lambda u, p, conn=None: app_module.auth.LoginResult(
        True, app_module.auth.OK, user=dict(USER, user_name="가상")))
    rows, logs = [], []
    monkeypatch.setattr(app_module.audit, "record", lambda row, conn=None: rows.append(row) or "db")
    monkeypatch.setattr(app_module, "_log_recheck", lambda rid, rc, ms: logs.append((rid, rc, ms)))
    _search(monkeypatch, [{"doc_id": "H0001", "masked_text": "담당 김철수"}])
    _llm(monkeypatch, "담당 김철수")
    client = TestClient(app_module.app)
    client.post("/api/login", json={"user_id": "SB260001", "password": "x"})
    body = client.post("/api/chat", json={"query": "질문"}).json()
    assert "김철수" not in str(body) and body["docs"][0]["snippet"].startswith("담당 <PERSON_")
    assert logs[0][0] == body["request_id"] and logs[0][1]["action"] == "masked" and isinstance(logs[0][1]["ms"], int)
    assert "ms" not in str({k: v for k, v in rows[0].items() if k.startswith("recheck")})   # query_log 에는 시간 없음


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
