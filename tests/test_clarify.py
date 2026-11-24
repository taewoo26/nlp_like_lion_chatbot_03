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


def test_clr02_shows_candidate_count_not_names(monkeypatch):
    res, post = _answer(monkeypatch, DOCS, tokens=[TOK], ask_person=True, person_candidates={TOK: 3})
    assert res["state"] == "clarify" and res["clarify_code"] == "ask_person"
    assert "3명" in res["answer"] and TOK not in res["answer"]   # 후보 수만, 토큰·이름은 없음
    post.assert_not_called()


def test_priority_vague_before_person(monkeypatch):
    res, _ = _answer(monkeypatch, DOCS, too_vague=True, ask_person=True)
    assert res["clarify_code"] == "too_vague"       # 우선순위 1번이 먼저


# ---------------------------------------------------------------- 0건 원인별 문구 (CLR-03~06)

@pytest.mark.parametrize("meta, code, phrase", [
    ({"empty_reason": "person"}, "person", "등록된 인물을 찾지 못했습니다"),
    ({"empty_reason": "role", "roles": ["직원"]}, "role", "'직원' 역할로는"),
    ({"empty_reason": "region", "regions": ["제주특별자치도"]}, "region", "제주특별자치도 지역 문서에서는"),
    ({"empty_reason": "token", "empty_label": ["address"]}, "token", "동·호수까지 전체 주소"),
])
def test_no_result_reasons(monkeypatch, meta, code, phrase):
    res, post = _answer(monkeypatch, [], **meta)
    assert res["state"] == "no_result" and res["clarify_code"] == code   # state 는 그대로 no_result
    assert phrase in res["answer"]
    post.assert_not_called()


def test_no_result_without_reason_keeps_old_message(monkeypatch):
    res, _ = _answer(monkeypatch, [])
    assert res["answer"] == "일치하는 문서를 찾지 못했습니다." and res["clarify_code"] is None


def test_token_message_never_repeats_value(monkeypatch):
    res, _ = _answer(monkeypatch, [], tokens=[ADDR], empty_reason="token", empty_label=["address"])
    assert ADDR not in res["answer"] and "주소" in res["answer"]   # 종류 이름만 쓴다


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
    s = {"rows": [], "own": True}
    monkeypatch.setattr(app_module.auth, "get_active_user", lambda uid, conn=None: dict(USER))
    monkeypatch.setattr(app_module.auth, "authenticate",
                        lambda u, p, conn=None: app_module.auth.LoginResult(True, app_module.auth.OK, user=dict(USER)))
    monkeypatch.setattr(app_module.audit, "record", lambda row, conn=None: s["rows"].append(row) or "db")
    monkeypatch.setattr(app_module, "_own_request", lambda rid, uid: rid if (rid and s["own"]) else None)   # 본인 질문 확인 대역
    monkeypatch.setattr(app_module.chat, "answer", lambda q, k: dict(s["result"]))
    client = TestClient(app_module.app)
    client.post("/api/login", json={"user_id": "SB260003", "password": "x"})
    s["client"] = client
    return s


def test_api_need_more_and_follow_up(api):
    api["result"] = {"masked_query": "알려줘", "query_tokens": [], "docs": [], "answer": "어떤 문서를…",
                     "notice": None, "state": "clarify", "clarify_code": "too_vague"}
    first = api["client"].post("/api/chat", json={"query": "알려줘"}).json()
    assert first["need_more"] is True and first["clarify_code"] == "too_vague"
    api["result"] = {"masked_query": "보이스피싱 피해 접수", "query_tokens": [], "docs": [], "answer": "네",
                     "notice": None, "state": "answered", "clarify_code": None}
    second = api["client"].post("/api/chat", json={"query": "보이스피싱 피해 접수", "follow_up_of": first["request_id"]}).json()
    assert second["need_more"] is False
    assert api["rows"][1]["follow_up_of"] == first["request_id"] and api["rows"][0]["follow_up_of"] is None


def test_api_drops_follow_up_of_other_user(api):
    api["own"] = False                              # 남의 질문 id 라고 가정
    api["result"] = {"masked_query": "q", "query_tokens": [], "docs": [], "answer": "네",
                     "notice": None, "state": "answered", "clarify_code": None}
    api["client"].post("/api/chat", json={"query": "q", "follow_up_of": "req_20261123_0123456789ab"})
    assert api["rows"][0]["follow_up_of"] is None   # 연결하지 않는다


def test_api_rejects_bad_follow_up_format(api):
    api["result"] = {"masked_query": "q", "query_tokens": [], "docs": [], "answer": "네",
                     "notice": None, "state": "answered", "clarify_code": None}
    r = api["client"].post("/api/chat", json={"query": "q", "follow_up_of": "DROP TABLE"})
    assert r.status_code == 422                     # 형식이 틀리면 요청 자체를 거절
