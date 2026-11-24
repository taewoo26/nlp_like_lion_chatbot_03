"""재질문(CLR) — 원인 판정과 되묻기 문구, 감사 로그·API 연결 (DB·모델 없이 실행).

재질문 정의서의 CLR-01~10 이 각각 의도한 문구·state·clarify_code 로 끝나는지 본다.
"""
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from service import app as app_module
from service import audit
from starter import chat, clarify, search

TOK = "<PERSON_bd52a13d0d35>"                       # 가상의 이름 토큰
ADDR = "<ADDRESS_0a1b2c3d4e5f>"                     # 가상의 주소 토큰
DOCS = [{"doc_id": "H0001", "masked_text": f"{TOK} VOC요약 처리 완료"}]   # 가상의 문서 1건
BASE = {"regions": [], "ask_region": None, "roles": [], "excluded": 0, "person_candidates": {},
        "ask_person": False, "too_vague": False, "empty_reason": None, "empty_label": [],
        "keyword_miss": False}                      # search.py 가 돌려주는 meta 기본값


def _answer(monkeypatch, docs, tokens=(), llm=None, **meta):
    """search 를 가짜로 바꾸고 chat.answer 를 부른다. llm 이 있으면 그 문장을 LLM 답으로 쓴다."""
    m = dict(BASE, **meta)                          # 시험할 꼬리표만 덮어쓴다
    monkeypatch.setattr(chat, "search", lambda *a, **kw: ("가명 질문", list(tokens), [dict(d) for d in docs], m))
    post = Mock()                                   # LLM 호출 기록용
    if llm is not None:                             # LLM 답을 정해 주면
        post.return_value.json.return_value = {"choices": [{"message": {"content": llm}}]}
    monkeypatch.setattr(chat.config, "LLM_API_KEY", "synthetic-test-key")
    monkeypatch.setattr(chat.requests, "post", post)
    monkeypatch.setattr(chat.config, "env", lambda name, default=None: "off" if name == "RECHECK" else default)   # 출력 재검사 끔
    return chat.answer("입력"), post


# ---------------------------------------------------------------- 판정 보조 (clarify.py)

@pytest.mark.parametrize("terms, tokens, regions, vague", [
    ({"알리"}, [], [], True),                       # 알려줘
    ({"사람"}, [], [], True),                       # 그 사람 거 보여줘
    (set(), [], [], True),                          # 보여줘
    ({"보이스피싱", "알리"}, [], [], False),          # 보이스피싱 알려줘 → 찾을 단서 있음
    ({"알리"}, [TOK], [], False),                   # 이름이 있으면 막연하지 않음
    ({"사람"}, [], ["서울특별시"], False),            # 지역이 있으면 막연하지 않음
])
def test_is_vague(terms, tokens, regions, vague):
    assert clarify.is_vague(terms, tokens, regions) is vague


def test_identifier_kinds_skip_person():
    assert clarify.identifier_kinds([TOK, ADDR, "<PHONE_NUMBER_aa11>"]) == ["address", "phone_number"]


# ---------------------------------------------------------------- 검색 전 재질문 (CLR-08 → 01 → 10 → 02)

def test_clr08_vague_question_does_not_call_llm(monkeypatch):
    res, post = _answer(monkeypatch, DOCS, too_vague=True)
    assert res["state"] == "clarify" and res["clarify_code"] == "too_vague"
    assert res["docs"] == [] and "어떤 문서를 찾으시나요" in res["answer"]   # 우연히 걸린 문서는 보여 주지 않음
    post.assert_not_called()


def test_clr01_region_message_unchanged(monkeypatch):
    res, _ = _answer(monkeypatch, [], ask_region="중구")
    assert res["state"] == "clarify" and res["clarify_code"] == "ask_region"
    assert res["answer"] == "어느 지역의 중구인가요? 시·도를 함께 알려 주세요."   # 기존 문구 그대로


def test_clr10_doc_type_choices(monkeypatch):
    res, _ = _answer(monkeypatch, DOCS, ask_doc_type=["민원현황", "민원처리", "분쟁조정"])
    assert res["clarify_code"] == "ask_doc_type" and "민원현황 / 민원처리 / 분쟁조정" in res["answer"]


def test_clr02_unified_and_hides_docs(monkeypatch):
    res, post = _answer(monkeypatch, DOCS, tokens=[TOK], ask_person=True, person_candidates={TOK: 7})
    assert res["state"] == "clarify" and res["clarify_code"] == "ask_person"
    assert res["answer"] == clarify.UNIFIED                     # 통일 문장 그대로
    assert "7" not in res["answer"] and res["docs"] == []        # 후보 수·문서 번호·조각 숨김
    post.assert_not_called()


def test_priority_vague_before_person(monkeypatch):
    res, _ = _answer(monkeypatch, DOCS, too_vague=True, ask_person=True)   # 둘 다 걸리면
    assert res["clarify_code"] == "too_vague"       # 우선순위 1번이 먼저


# ---------------------------------------------------------------- 0건: B 그룹 통일 응답 / A 그룹 구체 안내

@pytest.mark.parametrize("tokens, meta, code", [
    ([TOK], {"empty_reason": "person"}, "person"),                                           # CLR-03
    ([TOK], {"empty_reason": "role", "roles": ["employee"]}, "role"),                         # CLR-04 (이름 있음)
    ([TOK], {"empty_reason": "region", "regions": ["제주특별자치도"]}, "region"),               # CLR-05 (이름 있음)
    (["<PHONE_NUMBER_aa11>"], {"empty_reason": "token", "empty_label": ["phone_number"]}, "token"),   # CLR-06
    ([TOK], {}, "unknown"),                                                                   # 토큰 질문의 원인 불명
])
def test_person_group_is_unified(monkeypatch, tokens, meta, code):
    res, post = _answer(monkeypatch, [], tokens=tokens, **meta)
    assert res["state"] == "no_result" and res["clarify_code"] == code   # 원인은 감사 로그용으로 남는다
    assert res["answer"] == clarify.UNIFIED                              # 화면 문장은 모두 같다
    post.assert_not_called()


def test_unified_answers_are_identical_across_causes(monkeypatch):
    answers = set()
    for meta in ({"ask_person": True}, {"empty_reason": "person"}, {"empty_reason": "role"},
                 {"empty_reason": "region", "regions": ["제주특별자치도"]}, {"empty_reason": "token"}):
        docs = DOCS if meta.get("ask_person") else []
        res, _ = _answer(monkeypatch, docs, tokens=[TOK], **meta)
        answers.add((res["answer"], bool(res["clarify_code"]), tuple(d["doc_id"] for d in res["docs"])))
    assert len(answers) == 1                        # 문장·배지·문서 노출이 원인과 관계없이 같다 (노출 점검 시트)


def test_address_hint_depends_on_input_not_cause(monkeypatch):
    for meta in ({"empty_reason": "token"}, {"empty_reason": "person"}):   # 원인이 달라도
        res, _ = _answer(monkeypatch, [], tokens=[TOK, ADDR], **meta)
        assert res["answer"] == clarify.UNIFIED + "\n" + clarify.ADDRESS_HINT   # 주소가 있으면 같은 안내
    res, _ = _answer(monkeypatch, [], tokens=[TOK], empty_reason="token")
    assert clarify.ADDRESS_HINT not in res["answer"]                       # 주소가 없으면 안 붙음


@pytest.mark.parametrize("meta, phrase", [
    ({"empty_reason": "role", "roles": ["employee", "agent"]}, "요청한 역할로는"),               # 토큰 없는 역할 질문
    ({"empty_reason": "region", "regions": ["제주특별자치도"]}, "제주특별자치도 지역 문서에서는"),   # 토큰 없는 지역 질문
])
def test_no_token_questions_get_specific_message(monkeypatch, meta, phrase):
    res, _ = _answer(monkeypatch, [], **meta)
    assert phrase in res["answer"] and "employee" not in res["answer"]     # 내부 역할 코드는 보이지 않음


def test_no_result_without_reason_keeps_old_message(monkeypatch):
    res, _ = _answer(monkeypatch, [])
    assert res["answer"] == "일치하는 문서를 찾지 못했습니다." and res["clarify_code"] is None


def test_role_notice_hidden_when_name_in_question(monkeypatch):
    res, _ = _answer(monkeypatch, DOCS, tokens=[TOK], llm="네, 1건 있습니다. [H0001]", excluded=2)
    assert res["notice"] is None                    # 이름 질문에서 다른 역할 문서 건수를 알리지 않는다
    res, _ = _answer(monkeypatch, DOCS, llm="네, 1건 있습니다. [H0001]", excluded=2)
    assert "2건" in res["notice"]                    # 이름 없는 질문은 기존대로 안내


# ---------------------------------------------------------------- CLR-07 / CLR-09

def test_clr07_keyword_miss_skips_llm_and_hides_docs(monkeypatch):
    res, post = _answer(monkeypatch, DOCS, keyword_miss=True)
    assert res["state"] == "no_result" and res["clarify_code"] == "keyword" and res["docs"] == []
    assert "문서에 쓰는 이름으로" in res["answer"]
    post.assert_not_called()


def test_clr09_withheld_gets_hint(monkeypatch):
    res, _ = _answer(monkeypatch, DOCS, llm="제공된 문서에서는 확인되지 않습니다.")
    assert res["state"] == "withheld" and res["clarify_code"] == "withheld"
    assert res["answer"].startswith("제공된 문서에서는") and "찾은 문서 1건에는" in res["answer"]


def test_answered_has_no_clarify_code(monkeypatch):
    res, _ = _answer(monkeypatch, DOCS, llm="네, 1건 있습니다. [H0001]")
    assert res["state"] == "answered" and res["clarify_code"] is None


# ---------------------------------------------------------------- search.py 원인 진단

class FakeCursor:
    """execute 한 SQL 을 기록하고, 정해 둔 순서대로 fetchone 값을 돌려준다."""
    def __init__(self, answers):
        self.answers, self.sql = list(answers), []  # 돌려줄 값들, 실행한 SQL
    def execute(self, sql, params=None):
        self.sql.append(sql)                        # 무슨 쿼리를 했는지 남김
    def fetchone(self):
        return self.answers.pop(0)                  # 앞에서부터 하나씩


def test_name_reason():
    assert search._name_reason(FakeCursor([]), [TOK], set()) == "person"            # 역할 조건 없음 → 조회 안 함
    assert search._name_reason(FakeCursor([(1,)]), [TOK], {"employee"}) == "role"   # 이름은 있음 → 역할 탓
    assert search._name_reason(FakeCursor([None]), [TOK], {"employee"}) == "person"  # 이름도 없음


def test_filter_reason():
    assert search._filter_reason(FakeCursor([(1,)]), ["tok"], [[ADDR]], ["제주특별자치도"]) == "region"   # 지역 빼면 있음
    assert search._filter_reason(FakeCursor([None]), ["tok"], [[ADDR]], ["제주특별자치도"]) == "token"    # 지역 빼도 없음
    assert search._filter_reason(FakeCursor([]), [], [], ["제주특별자치도"]) == "region"                  # 지역만 걸림
    assert search._filter_reason(FakeCursor([]), ["tok"], [[ADDR]], []) == "token"                        # 토큰만 걸림


def test_keyword_miss_ignores_region_and_role_words(monkeypatch):
    monkeypatch.setattr(search, "extract_terms", lambda text: set())      # 문서 본문: 주소는 토큰이라 지역 이름이 없음
    docs = [{"masked_text": "<ADDRESS_aa> 거주 고객 상담"}]
    assert search._keyword_miss({"서울특별시", "강서구", "고객", "알리"}, docs) is False   # 지역·역할·막연한 말만 → 판정 안 함
    assert search._keyword_miss({"강서구", "클레임"}, docs) is True                       # 주제 키워드(클레임)는 그대로 본다


def test_narrow_candidates_needs_other_condition():
    cand, meta = {TOK: ["c1", "c2"]}, {"person_candidates": {TOK: 2}, "ask_person": True}
    cur = FakeCursor([])
    search._narrow_candidates(cur, cand, [TOK], [], {"customer"}, meta)   # 이름뿐이면
    assert cur.sql == [] and meta["ask_person"] is True                   # 조회도 안 하고 그대로 되묻는다


def test_narrow_candidates_to_one(monkeypatch):
    cand, meta = {TOK: ["c1", "c2"]}, {"person_candidates": {TOK: 2}, "ask_person": True}
    cur = FakeCursor([]); cur.fetchall = lambda: [("R1", "c1")]            # 번호가 있는 문서의 후보는 한 명
    search._narrow_candidates(cur, cand, [TOK, "<PHONE_NUMBER_aa11>"], [], {"customer"}, meta)
    assert cand[TOK] == ["c1"] and meta["ask_person"] is False and meta["person_candidates"][TOK] == 1


def test_narrow_candidates_still_many():
    cand, meta = {TOK: ["c1", "c2"]}, {"person_candidates": {TOK: 2}, "ask_person": True}
    cur = FakeCursor([]); cur.fetchall = lambda: [("R1", "c1"), ("R2", "c2")]   # 지역을 넣어도 두 명
    search._narrow_candidates(cur, cand, [TOK], ["서울특별시"], {"customer"}, meta)
    assert cand[TOK] == ["c1", "c2"] and meta["ask_person"] is True


def test_keyword_miss(monkeypatch):
    terms = {"고객의소리": {"고객의소리"}, "VOC요약 처리": {"VOC요약", "처리"}, "보이스피싱": {"보이스피싱"}}
    monkeypatch.setattr(search, "extract_terms", lambda text: terms.get(text, set()))   # Kiwi 없이 시험
    docs = [{"masked_text": "VOC요약 처리"}]
    assert search._keyword_miss({"고객의소리"}, docs) is True         # 어느 문서에도 없음
    assert search._keyword_miss({"처리", "알리"}, docs) is False      # 하나라도 겹치면 False
    assert search._keyword_miss({"알리"}, docs) is False              # 막연한 말뿐이면 판단 안 함 (CLR-08 몫)


# ---------------------------------------------------------------- 감사 로그 · API

USER = {"user_id": "SB260003", "user_name": "가상", "team_cd": "DEPT_CREDIT", "team_name": "여신팀",
        "position": "팀원", "grade": "대리", "access_level": 1, "is_admin": False}


def test_audit_row_has_clarify_fields():
    res = {"masked_query": "알려줘", "query_tokens": [], "docs": [], "state": "clarify", "clarify_code": "too_vague"}
    row = audit.build_row("req_x", USER, result=res, follow_up_of="req_20261123_0123456789ab")
    assert row["clarify_code"] == "too_vague" and row["follow_up_of"] == "req_20261123_0123456789ab"
    err = audit.build_row("req_y", USER, error=RuntimeError("x"))
    assert err["clarify_code"] is None and err["follow_up_of"] is None


@pytest.fixture
def api(monkeypatch):
    s = {"rows": [], "own": True, "prev_code": "ask_person", "age": 1.0, "contexts": []}
    monkeypatch.setattr(app_module.auth, "get_active_user", lambda uid, conn=None: dict(USER))
    monkeypatch.setattr(app_module.auth, "authenticate",
                        lambda u, p, conn=None: app_module.auth.LoginResult(True, app_module.auth.OK, user=dict(USER)))
    monkeypatch.setattr(app_module.audit, "record", lambda row, conn=None: s["rows"].append(row) or "db")

    def fake_prev(rid, uid):                       # 감사 로그 조회 대역
        if not rid or not s["own"]:
            return None
        return {"request_id": rid, "masked_query": f"{TOK} 고객 상담 내역 알려줘", "query_tokens": [TOK],
                "clarify_code": s["prev_code"], "age_min": s["age"]}
    monkeypatch.setattr(app_module, "_previous_request", fake_prev)

    def fake_answer(q, k, context=None):           # chat.answer 대역: 받은 context 를 기록
        s["contexts"].append(context)
        return dict(s["result"])
    monkeypatch.setattr(app_module.chat, "answer", fake_answer)
    client = TestClient(app_module.app)
    client.post("/api/login", json={"user_id": "SB260003", "password": "x"})
    s["client"] = client
    return s


def _ok(state="answered", code=None, merged=False):
    return {"masked_query": "q", "query_tokens": [], "docs": [], "answer": "네", "notice": None,
            "state": state, "clarify_code": code, "merged": merged}


def test_api_need_more_and_follow_up(api):
    api["result"] = _ok("clarify", "too_vague")
    first = api["client"].post("/api/chat", json={"query": "알려줘"}).json()
    assert first["need_more"] is True and first["clarify_code"] == "too_vague"
    api["result"] = _ok(merged=True)
    second = api["client"].post("/api/chat", json={"query": "010-1234-4123", "follow_up_of": first["request_id"]}).json()
    assert second["need_more"] is False and second["merged"] is True
    assert api["rows"][1]["follow_up_of"] == first["request_id"] and api["rows"][0]["follow_up_of"] is None
    assert api["contexts"][1]["masked_query"].startswith(TOK)      # 직전 가명 질문이 chat.answer 로 넘어감


@pytest.mark.parametrize("prev_code, age, passed", [
    ("ask_person", 1.0, True),        # 동명이인 재질문 직후 → 이어받기
    ("too_vague", 1.0, False),        # 막연한 질문은 이어받을 조건이 없음
    ("keyword", 1.0, False),          # 표현 불일치는 새 질문으로
    (None, 1.0, False),               # 직전이 정상 답변
    ("ask_person", 30.0, False),      # 시간이 지남
])
def test_api_context_rules(api, prev_code, age, passed):
    api["prev_code"], api["age"], api["result"] = prev_code, age, _ok()
    api["client"].post("/api/chat", json={"query": "010-1234-4123", "follow_up_of": "req_20261124_0123456789ab"})
    assert (api["contexts"][0] is not None) is passed


def test_api_drops_follow_up_of_other_user(api):
    api["own"] = False                              # 남의 질문 id 라고 가정
    api["result"] = _ok()
    api["client"].post("/api/chat", json={"query": "q", "follow_up_of": "req_20261123_0123456789ab"})
    assert api["rows"][0]["follow_up_of"] is None and api["contexts"][0] is None   # 연결도 이어받기도 안 함


def test_api_rejects_bad_follow_up_format(api):
    api["result"] = _ok()
    r = api["client"].post("/api/chat", json={"query": "q", "follow_up_of": "DROP TABLE"})
    assert r.status_code == 422                     # 형식이 틀리면 요청 자체를 거절


# ---------------------------------------------------------------- 이전 질문 이어받기 (merge_query)

PHONE1, PHONE2 = "<PHONE_NUMBER_aaaa11112222>", "<PHONE_NUMBER_bbbb33334444>"   # 가상의 전화번호 토큰


def _merge(prev_masked, prev_tokens, cur_masked, cur_tokens, code="ask_person"):
    from starter.bm25 import extract_terms         # 실제 Kiwi 로 키워드를 뽑는다
    return clarify.merge_query(prev_masked, prev_tokens, cur_masked, cur_tokens, extract_terms(cur_masked), code)


def test_merge_phone_only():
    merged = _merge(f"{TOK} 고객 상담 내역 알려줘", [TOK], PHONE1, [PHONE1])
    assert merged == (f"{TOK} 고객 상담 내역 알려줘 {PHONE1}", sorted([TOK, PHONE1]))


def test_merge_replaces_same_kind_token():
    merged = _merge(f"{TOK} 고객 {PHONE1} 상담 내역", [TOK, PHONE1], PHONE2, [PHONE2])
    assert PHONE1 not in merged[0] and PHONE1 not in merged[1] and PHONE2 in merged[1]   # 새 번호로 교체


def test_merge_role_only_replaces_role():
    merged = _merge(f"직원 {TOK} 기록 보여줘", [TOK], "고객이요", [], code="role")
    assert merged[0] == f"{TOK} 기록 보여줘 고객이요"   # 이전 역할 '직원'을 빼고 새 역할


def test_merge_region_only_replaces_region():
    merged = _merge(f"제주 사는 {TOK} 고객 상담 내역", [TOK], "서울", [], code="region")
    assert "제주" not in merged[0] and merged[0].endswith("서울")


def test_full_new_question_is_not_merged():
    assert _merge(f"{TOK} 고객 상담 내역 알려줘", [TOK], "법인카드 사용 보고서 찾아줘", []) is None


def test_doc_type_answer_after_ask_doc_type():
    merged = _merge("민원 내역 보여줘", [], "민원처리", [], code="ask_doc_type")
    assert merged == ("민원 내역 보여줘 민원처리", [])


def test_not_merged_after_non_mergeable_code():
    assert _merge("알려줘", [], PHONE1, [PHONE1], code="too_vague") is None


def test_search_uses_merged_query(monkeypatch):
    calls = {}
    monkeypatch.setattr(search.config, "meta_on", lambda name: name == "PERSON_DICT")
    monkeypatch.setattr(search, "mask_query", lambda q, with_records=False: (PHONE1, [PHONE1], [{"token": PHONE1, "label": "phone_number"}]))

    def fake_masked(query, k, conn, with_meta, dict_on, masked_q, q_tokens, q_records, reg_id=None):
        calls.update(masked_q=masked_q, tokens=q_tokens, records=q_records, query=query)
        return masked_q, q_tokens, [], {}
    monkeypatch.setattr(search, "_search_masked", fake_masked)
    ctx = {"masked_query": f"{TOK} 고객 상담 내역 알려줘", "query_tokens": [TOK], "clarify_code": "ask_person"}
    _, _, _, meta = search.search("010-1234-4123", 3, with_meta=True, context=ctx)
    assert calls["masked_q"].startswith(TOK) and calls["tokens"] == sorted([TOK, PHONE1]) and meta["merged"] is True
    assert {"token": TOK, "label": "person"} in calls["records"]      # 이름 토큰이 인물 후보 조회에 쓰이게
    assert "010" not in calls["query"]                                # 원문은 다시 쓰지 않는다
