"""간단한 대화 예제: 질의 가명처리 → 검색 → LLM 답변 (마스킹 텍스트만 전달, 토큰 그대로 표시).

    python -m starter.chat "윤선영 고객 대출 상담 내역 알려줘"

실명 복원·권한·출력 재검사는 종합에서 다룹니다. 이 파일은 vault 에 접근하지 않습니다.
LLM_API_KEY 가 없으면 검색 결과까지만 보여줍니다.
"""
import argparse

import requests

from . import config
from .search import search

SYSTEM = ("당신은 금융사 사내 문서 검색 도우미입니다. 주어진 문서 내용만 근거로 한국어로 짧게 답하세요. "
          "<PERSON_...> 같은 토큰은 가명처리된 개인정보이므로 그대로 쓰고, 원래 값을 추측하지 마세요.")


def answer(query, k=3):
    masked_q, q_tokens, docs = search(query, k)
    context = "\n\n".join(f"[{d['doc_id']}]\n{d['masked_text']}" for d in docs)
    result = {"masked_query": masked_q, "query_tokens": q_tokens, "docs": docs, "answer": None}
    if not config.LLM_API_KEY:
        return result
    r = requests.post(
        config.LLM_API_BASE.rstrip("/") + "/chat/completions",
        headers={"Authorization": f"Bearer {config.LLM_API_KEY}"},
        json={"model": config.LLM_MODEL, "temperature": 0,
              "messages": [{"role": "system", "content": SYSTEM},
                           {"role": "user", "content": f"문서:\n{context}\n\n질문: {masked_q}"}]},
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
