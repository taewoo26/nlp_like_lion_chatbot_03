"""간단한 대화 예제: 질의 가명처리 → 검색 → LLM 답변 (마스킹 텍스트만 전달, 토큰 그대로 표시).

    python -m starter.chat "윤선영 고객 대출 상담 내역 알려줘"

실명 복원·권한·출력 재검사는 종합에서 다룹니다. 이 파일은 vault 에 접근하지 않습니다.
LLM_API_KEY 가 없으면 검색 결과까지만 보여줍니다.
"""
import argparse
import re
import time

import requests

from . import config
from .search import search

SYSTEM = """당신은 금융사 사내 문서 검색 도우미입니다. 아래 규칙을 지켜 한국어로 답하세요.

[가명 토큰]
- <PERSON_…>, <PHONE_NUMBER_…>, <ACCOUNT_NUMBER_…> 같은 꺾쇠 표기는 가명처리된 개인정보입니다.
- 질문의 토큰과 문서의 토큰이 글자까지 똑같으면 같은 사람·같은 값입니다. 토큰이 다르면 다른 값입니다.
- 토큰은 그대로 옮겨 쓰고, 원래 이름·번호를 추측하거나 만들어 내지 마세요.

[답하는 방법]
1. 첫 문장에서 질문에 바로 답하세요. (예: "네, 1건 있습니다." / "확인되는 문서가 없습니다.")
2. 근거가 된 문서를 [문서ID]와 문서 종류·날짜·핵심 항목으로 짧게 밝히세요.
3. 질문에 토큰이 있으면, 같은 토큰이 나오는 문서만 근거로 쓰세요.
4. 문서 위에 "(시스템 확인)" 줄이 있으면 그 토큰이 문서에 실제로 있다는 뜻입니다. 그 줄의 항목명
   (연락처·신청인·출금 계좌 등)을 근거로, 그 값이 어떤 문서에 어떤 용도로 나오는지 답하세요.
   예) "네, 1건 있습니다. [H0000] OO 신청서(YYYY-MM-DD 접수)에 신청인의 연락처로 등록돼 있습니다."

[정확도 원칙: 틀린 답보다 무응답]
- 주어진 문서에 근거가 없으면 "제공된 문서에서는 확인되지 않습니다"라고 답하세요.
- 질문의 역할(예: 고객/직원)과 문서 속 같은 토큰 인물의 역할이 다르면, 다른 사람일 수 있으므로 답을 보류하고 그 이유를 말하세요.
- 문서에 없는 내용을 짐작해서 덧붙이지 마세요."""


TOKEN = re.compile(r"<[A-Za-z_]+_[0-9A-Za-z]+>")


def _doc_block(doc, q_tokens):
    """문서 본문 앞에, 코드가 직접 확인한 '질문 토큰이 나오는 줄'을 붙인다.
    긴 무작위 토큰을 LLM이 눈으로 대조하지 않아도 되게 하기 위함."""
    lines = [ln.strip() for ln in doc["masked_text"].splitlines()
             if any(t in ln for t in q_tokens)]
    head = f"[{doc['doc_id']}]"
    if lines:
        head += "\n(시스템 확인) 질문의 토큰이 이 문서의 다음 줄에 그대로 나옵니다:\n" + \
                "\n".join(f"  - {ln}" for ln in lines)
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


def answer(query, k=3):
    masked_q, q_tokens, docs = search(query, k)
    info = getattr(docs, "info", None) or {}            # 검색 안내 (역할 제외·지역 되묻기 등)
    notices = info.get("notices", [])
    context = "\n\n".join(_doc_block(d, q_tokens) for d in docs)
    token_note = f"\n질문에 포함된 토큰: {', '.join(q_tokens)}" if q_tokens else ""
    result = {"masked_query": masked_q, "query_tokens": q_tokens, "docs": docs, "answer": None,
              "notices": notices}
    if info.get("clarify"):                             # REG-33: 검색하지 않고 되묻기
        result["answer"] = info["clarify"]
        return result
    if not docs:
        result['answer'] = " ".join(['일치하는 문서를 찾지 못했습니다.', *notices])
        return result
    if not config.LLM_API_KEY:
        return result
    payload = {"model": config.LLM_MODEL, "temperature": 0,
               "messages": [{"role": "system", "content": SYSTEM},
                            {"role": "user", "content": f"문서:\n{context}\n\n질문: {masked_q}{token_note}"}]}
    payload.update(_llm_options())
    r = _post_llm(payload)
    known = set(q_tokens) | {t for d in docs for t in TOKEN.findall(d["masked_text"])}
    result["answer"] = _restore_tokens(r.json()["choices"][0]["message"]["content"], known)
    if notices:
        result["answer"] += "\n\n(" + " ".join(notices) + ")"
    return result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("query")
    ap.add_argument("-k", type=int, default=3)
    a = ap.parse_args()
    res = answer(a.query, a.k)
    print(f"질의(가명처리): {res['masked_query']}")
    print("참고 문서:", ", ".join(d["doc_id"] for d in res["docs"]))
    print("\n답변:", res["answer"] or "(LLM_API_KEY 없음 — 검색 결과만 표시)")


if __name__ == "__main__":
    main()
