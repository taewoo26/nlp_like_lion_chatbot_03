"""간단한 대화 예제: 질의 가명처리 → 검색 → LLM 답변 (마스킹 텍스트만 전달, 토큰 그대로 표시).

    python -m starter.chat "윤선영 고객 대출 상담 내역 알려줘"

실명 복원·권한·출력 재검사는 종합에서 다룹니다. 이 파일은 vault 에 접근하지 않습니다.
LLM_API_KEY 가 없으면 검색 결과까지만 보여줍니다.
"""
import argparse

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

[정확도 원칙: 틀린 답보다 무응답]
- 주어진 문서에 근거가 없으면 "제공된 문서에서는 확인되지 않습니다"라고 답하세요.
- 질문의 역할(예: 고객/직원)과 문서 속 같은 토큰 인물의 역할이 다르면, 다른 사람일 수 있으므로 답을 보류하고 그 이유를 말하세요.
- 문서에 없는 내용을 짐작해서 덧붙이지 마세요."""


def answer(query, k=3):
    masked_q, q_tokens, docs = search(query, k)
    context = "\n\n".join(f"[{d['doc_id']}]\n{d['masked_text']}" for d in docs)
    token_note = f"\n질문에 포함된 토큰: {', '.join(q_tokens)}" if q_tokens else ""
    result = {"masked_query": masked_q, "query_tokens": q_tokens, "docs": docs, "answer": None}
    if not docs:
        result['answer'] = '일치하는 문서를 찾지 못했습니다.'
        return result
    if not config.LLM_API_KEY:
        return result
    r = requests.post(
        config.LLM_API_BASE.rstrip("/") + "/chat/completions",
        headers={"Authorization": f"Bearer {config.LLM_API_KEY}"},
        json={"model": config.LLM_MODEL, "temperature": 0,
              "messages": [{"role": "system", "content": SYSTEM},
                           {"role": "user", "content": f"문서:\n{context}\n\n질문: {masked_q}{token_note}"}]},
        timeout=60)
    r.raise_for_status()
    result["answer"] = r.json()["choices"][0]["message"]["content"]
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