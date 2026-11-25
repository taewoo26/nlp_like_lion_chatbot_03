"""간단한 대화 예제: 질의 가명처리 → 검색 → LLM 답변 (마스킹 텍스트만 전달, 토큰 그대로 표시).

    python -m starter.chat "윤선영 고객 대출 상담 내역 알려줘"

출력 재검사는 starter/recheck.py 를 LLM 응답 직후(실명 복원 전)에 부릅니다. 실명 복원·권한 확인은 service/app.py 가 합니다.
이 파일은 vault 에 접근하지 않습니다.
LLM_API_KEY 가 없으면 검색 결과까지만 보여줍니다.

answer() 결과의 state (audit.query_log.state, 코드값 시트 기준)
  clarify     : 질문의 지역 이름이 여러 시·도에 겹쳐 되물음
  no_result   : 조건에 맞는 문서가 없음
  search_only : LLM_API_KEY 가 없어 검색 결과만
  withheld    : LLM이 문서로 확인할 수 없다고 답함 (답변 문구로 판정. 오판 가능)
                또는 출력 재검사가 차단함 (recheck action=blocked, search_only 에서도)
  answered    : 문서를 근거로 LLM이 답함
  재질문(CLR, starter/clarify.py): state 값은 늘리지 않고 clarify_code 로 원인을 따로 남긴다.
    사람·식별자가 걸린 재질문(B 그룹)은 원인과 관계없이 같은 문장, 문서 번호·조각은 화면에 보내지 않는다.
    clarify   : too_vague(CLR-08) / ask_region(01) / ask_doc_type(10) / ask_person(02)
    no_result : person(03) / role(04) / region(05) / token(06) / keyword(07),
                원인 불명이면 None (토큰이 있는 질문이면 unknown — 통일 응답)
    withheld  : withheld(09) — LLM 답 뒤에 보충 안내를 덧붙인다 (출력 재검사 차단은 제외)
  error 는 예외로 올라가므로 호출하는 쪽이 정한다. 가명처리 뒤에 난 예외에는
  masked_query, query_tokens 속성이 붙어 있다 (search.tag_masked).

answer() 결과의 recheck: {"action", "labels", "detector"} (audit.query_log.recheck_*). 재검사를 하지 않았으면 None.
answer() 결과의 docs[*].snippet: 화면·API 로 내보낼 문서 조각(앞 180자, 재검사를 거침). masked_text(전체)는 내보내지 않는다.
"""
import argparse
import re
import time

import requests

from . import clarify, config, recheck
from .bm25 import extract_terms
from .search import search, tag_masked

SYSTEM = """당신은 금융사 사내 문서 검색 도우미입니다. 아래 규칙을 지켜 한국어로 답하세요.

[가명 토큰]
- <PERSON_…>, <PHONE_NUMBER_…>, <ACCOUNT_NUMBER_…> 같은 꺾쇠 표기는 가명처리된 개인정보입니다.
- 토큰이 같으면 같은 값이지만, 이름 토큰만 같다고 같은 사람이라고 단정하지 마세요. 인물의 연결은 시스템 검색 결과를 따르세요.
- 토큰은 그대로 옮겨 쓰고, 원래 이름·번호를 추측하거나 만들어 내지 마세요.

[답하는 방법]
1. 첫 문장에서 질문에 바로 답하세요. (예: "네, 1건 있습니다." / "확인되는 문서가 없습니다.")
2. 근거가 된 문서를 [문서ID]와 문서 종류·날짜·핵심 항목으로 짧게 밝히세요.
3. 질문에 토큰이 있으면, 같은 토큰이 나오는 문서 또는 시스템이 동일인 연결을 확인한 문서만 근거로 쓰세요.
4. 문서 위에 "(시스템 확인)" 줄이 있으면 그 토큰이 문서에 실제로 있다는 뜻입니다. 그 줄의 항목명
   (연락처·신청인·출금 계좌 등)을 근거로, 그 값이 어떤 문서에 어떤 용도로 나오는지 답하세요.
   예) "네, 1건 있습니다. [H0000] OO 신청서(YYYY-MM-DD 접수)에 신청인의 연락처로 등록돼 있습니다."

[정확도 원칙: 틀린 답보다 무응답]
- 주어진 문서에 근거가 없으면 "제공된 문서에서는 확인되지 않습니다"라고 답하세요.
- 질문의 역할(예: 고객/직원)과 문서 속 같은 토큰 인물의 역할이 다르면, 다른 사람일 수 있으므로 답을 보류하고 그 이유를 말하세요.
- 문서에 없는 내용을 짐작해서 덧붙이지 마세요."""


TOKEN = re.compile(r"<[A-Za-z_]+_[0-9A-Za-z]+>")
# 이 말이 들어간 답변은 '답변 보류(withheld)'로 본다
WITHHELD_WORDS = ("확인되지 않", "찾지 못", "보류", "확인할 수 없", "알 수 없")


def _doc_block(doc, q_tokens, q_keywords=None):
    """문서 본문 앞에, 코드가 직접 확인한 '질문 토큰이 나오는 줄'과 '키워드가 나오는 줄'을 붙인다."""
    token_lines = [ln.strip() for ln in doc["masked_text"].splitlines()
                   if any(t in ln for t in q_tokens)]
    head = f"[{doc['doc_id']}]"
    if token_lines:
        head += "\n(시스템 확인) 질문의 토큰이 이 문서의 다음 줄에 그대로 나옵니다:\n" + \
                "\n".join(f"  - {ln}" for ln in token_lines)
    if q_keywords:
        doc_terms = extract_terms(doc["masked_text"])
        matched = q_keywords & doc_terms
        if matched:
            kw_lines = [ln.strip() for ln in doc["masked_text"].splitlines()
                        if any(kw in ln for kw in matched)]
            if kw_lines:
                head += "\n(키워드 확인) 질문의 핵심 단어가 이 문서의 다음 줄에 나옵니다:\n" + \
                        "\n".join(f"  - {ln}" for ln in kw_lines)
    return f"{head}\n본문:\n{doc['masked_text']}"


def _restore_tokens(text, known):
    """LLM이 토큰의 대소문자를 바꿔 쓴 경우 원래 표기로 되돌린다 (<PERSON_BD52…> → <PERSON_bd52…>)."""
    canon = {t.lower(): t for t in known}
    return TOKEN.sub(lambda m: canon.get(m.group().lower(), m.group()), text)


def _llm_options():
    """HCX-007(추론 모델)용 설정. .env 에 값이 있을 때만 요청에 넣는다 (HCX-005 는 비워 두면 그대로).
    LLM_REASONING : none | low | medium | high  (none = 추론 없이 바로 답변)
    LLM_MAX_TOKENS: 추론 + 답변을 합친 최대 토큰. 호환 API 기본값이 512 라 추론을 켜면 답이 잘릴 수 있다."""
    opts = {}
    effort = (config.env("LLM_REASONING", "") or "").strip().lower()
    if effort:
        if effort not in ("none", "low", "medium", "high"):
            raise ValueError(f"LLM_REASONING 은 none/low/medium/high 중 하나여야 합니다: {effort}")
        opts["reasoning_effort"] = effort
    max_tokens = (config.env("LLM_MAX_TOKENS", "") or "").strip()
    if max_tokens:
        opts["max_completion_tokens"] = int(max_tokens)
    return opts


def _post_llm(payload, retries=5):
    """LLM 호출. 429(요청 많음)·5xx 는 잠깐 기다렸다 다시 시도한다 (2, 4, 8, 16, 32초)."""
    url = config.LLM_API_BASE.rstrip("/") + "/chat/completions"
    headers = {"Authorization": f"Bearer {config.LLM_API_KEY}"}
    for attempt in range(retries + 1):
        r = requests.post(url, headers=headers, json=payload, timeout=120)
        if r.status_code in (429, 500, 502, 503, 504) and attempt < retries:
            wait = min(2 ** (attempt + 1), 60)
            print(f"  LLM API {r.status_code} → {wait}초 후 재시도 ({attempt + 1}/{retries})", flush=True)
            time.sleep(wait)
            continue
        r.raise_for_status()
        return r


def answer(query, k=3, context=None):
    """context: 직전 재질문 (service/app.py). 지금 질문이 조각이면 직전 질문과 합쳐서 찾는다."""
    masked_q, q_tokens, docs, meta = search(query, k, with_meta=True, **({"context": context} if context else {}))
    try:
        res = _answer(masked_q, q_tokens, docs, meta)
        res["merged"] = bool(meta.get("merged"))   # 이전 질문과 합쳐서 찾았는지 (화면 안내용)
        return res
    except Exception as exc:
        tag_masked(exc, masked_q, q_tokens)
        raise


def _answer(masked_q, q_tokens, docs, meta):
    q_keywords = extract_terms(masked_q)
    context = "\n\n".join(_doc_block(d, q_tokens, q_keywords) for d in docs)
    token_note = f"\n질문에 포함된 토큰: {', '.join(q_tokens)}" if q_tokens else ""
    result = {"masked_query": masked_q, "query_tokens": q_tokens, "docs": docs, "answer": None,
              "notice": None, "state": None, "recheck": None,
              "clarify_code": None}                # 재질문 코드 (starter/clarify.py). 화면 표시·감사 로그용
    if meta.get("person_candidates") and not meta.get("ask_person") and docs:
        token_note += "\n(시스템 확인) 인물 연결표의 이름·역할 조건으로 찾은 문서입니다. 이름 토큰 일치만으로 서로 다른 문서의 인물을 동일인이라고 단정하지 마세요."
    asked = clarify.pre_answer(meta, q_tokens)     # 검색 결과보다 먼저 되물을 것 (CLR-08 → 01 → 10 → 02)
    if asked:                                      # 되물을 것이 있으면
        result["clarify_code"], result["answer"] = asked   # 코드와 문구를 담고
        if result["clarify_code"] in ("too_vague", "ask_person"):   # 막연한 질문·동명이인은 문서를 보여 주지 않는다
            result["docs"] = []                    # 동명이인: 문서 번호·조각만으로 후보가 드러나므로 숨김 (B 그룹)
        return _rechecked(result, "clarify", check_answer=False)   # 고정 문구라 답변 재검사는 생략
    has_person = any(t.startswith("<PERSON_") for t in q_tokens)   # 질문에 이름이 있는지
    if meta["excluded"] and not has_person:        # ROLE-43 실명 없이 건수만 안내 (이름이 있으면 다른 역할의 존재가 드러나므로 생략)
        result["notice"] = f"요청한 역할과 다른 문서 {meta['excluded']}건은 제외했습니다."
    if meta["regions"]:                            # 문서 주소는 토큰이라 LLM이 지역을 확인할 수 없다
        token_note += ("\n(시스템 확인) 질문의 지역 조건은 검색 단계에서 이미 확인했고, 그 지역 문서만 드렸습니다. "
                       "문서의 주소는 <ADDRESS_…> 토큰으로 가려져 있으니 지역은 다시 따지지 말고 나머지 질문에 답하세요.")
    if not docs:
        result['answer'] = clarify.no_result_message(meta, q_tokens)   # 사람·식별자가 걸리면 통일 응답, 아니면 원인별 문구
        result["clarify_code"] = meta.get("empty_reason")     # CLR-03~06 원인은 감사 로그에만 (없으면 None)
        if result["clarify_code"] is None and clarify.is_person_group(None, q_tokens):   # 토큰 질문의 원인 불명 0건도
            result["clarify_code"] = "unknown"                # 원인 불명이지만 통일 응답이라 화면 배지를 다른 B 그룹과 맞춘다
        result["state"] = "no_result"              # state 는 그대로 no_result (원인은 clarify_code 로 구분)
        return result
    if meta.get("keyword_miss"):                   # CLR-07 질문 단어가 문서 어디에도 없음 → 엉뚱한 문서로 답하지 않는다
        result["answer"] = clarify.MESSAGES["keyword"]        # 문서에 쓰는 이름으로 다시 물어 달라고 안내
        result["clarify_code"] = "keyword"                    # CLR-07
        result["docs"] = []                                   # 관련 없는 문서는 보여 주지 않는다
        result["state"] = "no_result"                         # 문서를 근거로 답하지 않았으므로 no_result
        return result
    if not config.LLM_API_KEY:
        return _rechecked(result, "search_only")
    payload = {"model": config.LLM_MODEL, "temperature": 0,
               "messages": [{"role": "system", "content": SYSTEM},
                            {"role": "user", "content": f"문서:\n{context}\n\n질문: {masked_q}{token_note}"}]}
    payload.update(_llm_options())
    r = _post_llm(payload)
    known = set(q_tokens) | {t for d in docs for t in TOKEN.findall(d["masked_text"])}
    result["answer"] = _restore_tokens(r.json()["choices"][0]["message"]["content"], known)
    withheld = any(w in result["answer"] for w in WITHHELD_WORDS)
    result = _rechecked(result, "withheld" if withheld else "answered")
    blocked = (result["recheck"] or {}).get("action") == "blocked"   # 출력 재검사로 막힌 경우는 재질문 대상이 아님
    if withheld and not blocked:                   # CLR-09 문서는 찾았지만 요청한 정보가 없음
        result["answer"] += "\n\n" + clarify.withheld_hint(len(result["docs"]))   # 무엇을 보충할지 덧붙인다
        result["clarify_code"] = "withheld"        # 화면에서 '조건 확인 필요'로 표시
    return result


def _rechecked(result, state, check_answer=True):
    """화면으로 나갈 답변·문서 조각을 재검사한 뒤 state 를 정한다. 차단되면 withheld.
    check_answer=False: 답변이 시스템 고정 문구(되묻기)라 문서 조각만 검사한다. 문서도 없으면 재검사하지 않는다(None)."""
    if not check_answer and not result["docs"]:
        result["state"] = state
        return result
    result["recheck"] = recheck.check(result, check_answer=check_answer)
    recheck.default_snippets(result)              # 재검사를 끈 경우에도 화면용 조각(snippet)은 항상 만든다
    blocked = result["recheck"] is not None and result["recheck"]["action"] == "blocked"
    result["state"] = "withheld" if blocked else state
    return result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("query")
    ap.add_argument("-k", type=int, default=3)
    a = ap.parse_args()
    res = answer(a.query, a.k)
    print(f"질의(가명처리): {res['masked_query']}")
    print("참고 문서:", ", ".join(d["doc_id"] for d in res["docs"]))
    if res["notice"]:
        print("안내:", res["notice"])
    print("\n답변:", res["answer"] or "(LLM_API_KEY 없음 — 검색 결과만 표시)")
    print("상태:", res["state"])
    if res["recheck"]:
        print("재검사:", res["recheck"]["action"], res["recheck"]["labels"] or "", f'({res["recheck"]["ms"]}ms)')


if __name__ == "__main__":
    main()
