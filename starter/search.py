"""검색.  ★ 검색 로직은 팀에서 설계합니다. 지금은 의미 검색만 하는 최소 예제입니다. ★

    python -m starter.search "보이스피싱 피해 접수 기록 찾아줘"

지금 하는 일
  1. 질의를 문서와 같은 탐지기·가명처리로 마스킹합니다 (API로는 마스킹된 질의만 전송).
  2. 마스킹된 질의를 임베딩해 rag.chunks 에서 코사인 거리가 가까운 청크를 찾습니다.
  3. 문서별로 가장 가까운 청크 하나만 남겨 순위를 매깁니다.
  질의 토큰(q_tokens)은 뽑아 두기만 하고 검색에는 쓰지 않습니다.

설계해 볼 것
  - 질의 토큰을 검색에 어떻게 쓸까요? (rag.chunks.tokens 에는 GIN 인덱스가 걸려 있습니다)
  - 토큰이 여러 개면 모두 가진 문서만? 하나라도 가진 문서도?
  - 토큰으로 거른 결과가 모자라거나 없으면 어떻게 할까요?
  - 토큰 점수와 의미 점수를 어떻게 합칠까요? 동명이인 문서가 섞이면 어떻게 구분할까요?
  - eval_holdout_retrieval.py 의 질의 유형별 점수로 설계를 비교해 보세요.

다른 코드(run_queries.py, chat.py)가 쓰는 약속
  search(query, k, conn=None) -> (masked_query, query_tokens, docs)
  docs: [{"doc_id", "chunk_id", "masked_text", "tokens", "score"}, ...]  순위 순서
검색 단계는 rag_user 계정만 씁니다 (매핑 테이블 접근 없음).
"""
import argparse

from . import db, pii_pipeline
from .embed import embed


def mask_query(query):
    masked, records = pii_pipeline.pseudonymize(query, pii_pipeline.detect(query))
    return masked, sorted({r["token"] for r in records})


def search(query, k=10, conn=None):
    masked_q, q_tokens = mask_query(query)
    vec = embed([masked_q])[0]
    own = conn is None
    conn = conn or db.rag_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""SELECT doc_id, chunk_id, masked_text, tokens, embedding <=> %s AS dist
                           FROM rag.chunks ORDER BY dist LIMIT %s""", (vec, k * 3))
            hits = cur.fetchall()
    finally:
        if own:
            conn.close()
    best = {}
    for doc_id, chunk_id, text, tokens, dist in hits:  # 문서별 가장 가까운 청크 하나
        if doc_id not in best:
            best[doc_id] = {"doc_id": doc_id, "chunk_id": chunk_id, "masked_text": text,
                            "tokens": tokens, "score": 1 - float(dist)}
    return masked_q, q_tokens, list(best.values())[:k]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("query")
    ap.add_argument("-k", type=int, default=5)
    a = ap.parse_args()
    masked_q, q_tokens, docs = search(a.query, a.k)
    print(f"질의(가명처리): {masked_q}\n질의 토큰: {q_tokens or '없음'}\n")
    for i, d in enumerate(docs, 1):
        print(f"{i}. {d['doc_id']}  score={d['score']:.3f}  tokens={d['tokens'][:4]}")
        print("   " + d["masked_text"][:120].replace("\n", " ") + " …")


if __name__ == "__main__":
    main()
