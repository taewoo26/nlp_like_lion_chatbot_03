"""질의의 모든 토큰을 포함한 문서에서 의미 검색한다.

    python -m starter.search "보이스피싱 피해 접수 기록 찾아줘"

지금 하는 일
  1. 질의를 문서와 같은 탐지기·가명처리로 마스킹합니다 (API로는 마스킹된 질의만 전송).
  2. 질의 토큰이 있으면 문서 전체에서 모두 포함한 문서만 후보로 삼습니다.
     토큰이 서로 다른 청크에 있어도 같은 문서이면 후보에 포함합니다.
  3. 문서별로 가장 가까운 청크 하나만 남겨 순위를 매깁니다.
  일치 후보가 없으면 빈 결과를 반환합니다. 토큰 없는 질의는 전체에서 의미 검색합니다.

추후 비교할 것
  - 토큰 필터와 의미 점수의 조합별 검색 품질
  - 동명이인 문서가 섞였을 때 문맥에 따른 순위
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
            sql = """SELECT doc_id, chunk_id, masked_text, tokens, embedding <=> %s AS dist
                     FROM rag.chunks AS chunks"""
            params = [vec]
            if q_tokens:
                sql += """ WHERE NOT EXISTS (
                    SELECT 1 FROM unnest(%s::text[]) AS q(token)
                    WHERE NOT EXISTS (
                        SELECT 1 FROM rag.chunks AS matched
                        WHERE matched.doc_id = chunks.doc_id
                          AND matched.tokens @> ARRAY[q.token]
                    )
                )"""
                params.append(q_tokens)
            sql += " ORDER BY dist LIMIT %s"
            params.append(k * 3)
            cur.execute(sql, tuple(params))
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
