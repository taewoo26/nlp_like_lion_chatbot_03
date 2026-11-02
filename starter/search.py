"""질의 토큰과 의미 검색을 함께 쓰는 검색.

    python -m starter.search "보이스피싱 피해 접수 기록 찾아줘"
    SEARCH_MODE=boost python -m starter.run_queries --out outputs/run_boost.jsonl   # 비교 실험

검색 방식 (.env 또는 환경변수 SEARCH_MODE, 기본 and)
  and   : 질의 토큰을 '모두' 포함한 문서만 후보로 삼고 그 안에서 의미 검색 (팀 기본안)
          토큰이 서로 다른 청크에 있어도 같은 문서이면 후보. 일치 후보가 없으면 빈 결과.
  boost : 의미 검색 후보 + 질의 토큰을 '하나라도' 가진 문서 후보를 모은 뒤
          점수 = 의미 유사도 + TOKEN_WEIGHT × (질의 토큰 중 문서가 가진 비율)
          질의 탐지가 하나 틀려도 정답이 사라지지 않는다. (비교 실험용)
  토큰 없는 질의는 두 방식 모두 전체 의미 검색.

임베딩에는 pii_pipeline.embedding_text()로 토큰을 라벨 이름으로 바꾼 질의를 넣는다
(문서도 ingest에서 같은 처리를 하므로 둘이 같은 공간에서 비교된다).

다른 코드(run_queries.py, chat.py)가 쓰는 약속
  search(query, k, conn=None) -> (masked_query, query_tokens, docs)
  docs: [{"doc_id", "chunk_id", "masked_text", "tokens", "score"}, ...]  순위 순서
검색 단계는 rag_user 계정만 씁니다 (매핑 테이블 접근 없음).
"""
import argparse
import re

from . import config, db, pii_pipeline
from .embed import embed

TOKEN_POOL = 200   # boost: 토큰 후보 최대 청크 수


def mask_query(query):
    # 질의는 strict=False: 처리할 수 없는 표기가 있어도 챗봇이 멈추지 않고 그 값만 [개인정보]로 가린다
    masked, records = pii_pipeline.pseudonymize(query, pii_pipeline.detect(query), strict=False)
    return masked, sorted({r["token"] for r in records})


def _mode():
    mode = (config.env("SEARCH_MODE", "and") or "and").strip().lower()
    if mode not in ("and", "boost"):
        raise ValueError(f"SEARCH_MODE 는 and 또는 boost 여야 합니다: {mode}")
    return mode


def search(query, k=10, conn=None):
    masked_q, q_tokens = mask_query(query)
    vec = embed([pii_pipeline.embedding_text(masked_q)], kind="query")[0]
    mode = _mode()
    own = conn is None
    conn = conn or db.rag_conn()
    try:
        with conn.cursor() as cur:
            if mode == "boost" and q_tokens:
                return masked_q, q_tokens, rerank_top(masked_q, _search_boost(cur, vec, q_tokens, k))
            if q_tokens:
                # 토큰 후보를 먼저 확정(MATERIALIZED)한 뒤 그 안에서만 거리 순으로 정렬한다.
                # 필터를 바깥 쿼리에 두면 k가 작을 때 DB가 HNSW 근사 인덱스를 골라
                # '가까운 약 40개'만 꺼낸 뒤 필터를 걸어, 정답이 그 밖에 있으면 빈 결과가 된다.
                sql = """WITH cand AS MATERIALIZED (
                    SELECT doc_id, chunk_id, masked_text, tokens, embedding <=> %s AS dist
                    FROM rag.chunks AS chunks
                    WHERE NOT EXISTS (
                        SELECT 1 FROM unnest(%s::text[]) AS q(token)
                        WHERE NOT EXISTS (
                            SELECT 1 FROM rag.chunks AS matched
                            WHERE matched.doc_id = chunks.doc_id
                              AND matched.tokens @> ARRAY[q.token]
                        )
                    )
                )
                SELECT doc_id, chunk_id, masked_text, tokens, dist FROM cand ORDER BY dist LIMIT %s"""
                params = [vec, q_tokens, k * 3]
            else:
                sql = """SELECT doc_id, chunk_id, masked_text, tokens, embedding <=> %s AS dist
                         FROM rag.chunks AS chunks ORDER BY dist LIMIT %s"""
                params = [vec, k * 3]
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
    return masked_q, q_tokens, rerank_top(masked_q, list(best.values())[:k])


# ---------------------------------------------------------------- 상위 문서 재정렬 (단어 겹침)
_TOKEN = re.compile(r"<[A-Z_]+_[0-9A-Za-z]+>")
_KEEP_TAGS = ("NNG", "NNP", "VV", "VA", "XR", "SL", "SN")


def _terms(text):
    """토큰을 지운 뒤 Kiwi로 내용어(명사·동사·형용사 등)만 뽑는다."""
    from detection.rule.kiwi_filter import _get_kiwi   # 탐지기가 이미 불러 둔 Kiwi 재사용
    return {t.form for t in _get_kiwi().tokenize(_TOKEN.sub(" ", text))
            if t.tag in ("NNG", "NNP") or (t.tag.startswith(_KEEP_TAGS) and len(t.form) > 1)}


def rerank_top(masked_q, docs):
    """상위 RERANK_TOP(기본 5)개 문서만 '의미 점수 + 가중치 × 질문 단어 겹침'으로 다시 줄 세운다.

    상위 5개에 드는 문서 구성은 바꾸지 않고 순서만 바꾸므로 Hit@5·Recall@5 는 그대로이고,
    '완료/진행 중', '포인트', '감사 지적'처럼 같은 인물 문서끼리 갈리는 핵심 단어로 1등을 고른다.
    holdout 재현 실험(bge-m3, AND): Hit@1 .852 → .898, MRR .904 → .934, 다른 지표 변화 없음.
    RERANK_TOP=0 이면 끈다.
    """
    top = int(config.env("RERANK_TOP", "5"))
    weight = float(config.env("RERANK_WEIGHT", "0.2"))
    if top <= 1 or len(docs) <= 1 or weight <= 0:
        return docs
    q_terms = _terms(masked_q)
    if not q_terms:
        return docs
    head, tail = docs[:top], docs[top:]
    overlap = {d["doc_id"]: len(q_terms & _terms(d["masked_text"])) / len(q_terms) for d in head}
    best = max(overlap.values()) or 1.0
    for d in head:
        d["score"] = d["score"] + weight * overlap[d["doc_id"]] / best
    return sorted(head, key=lambda d: -d["score"]) + tail


def _search_boost(cur, vec, q_tokens, k):
    weight = float(config.env("TOKEN_WEIGHT", "1.0"))
    cur.execute(
        """(SELECT doc_id, chunk_id, masked_text, tokens, embedding <=> %s AS dist
              FROM rag.chunks ORDER BY dist LIMIT %s)
           UNION
           (SELECT doc_id, chunk_id, masked_text, tokens, dist FROM (
              SELECT doc_id, chunk_id, masked_text, tokens, embedding <=> %s AS dist
              FROM rag.chunks WHERE tokens && %s::text[] OFFSET 0) AS tok
            ORDER BY dist LIMIT %s)""",
        (vec, k * 3, vec, q_tokens, TOKEN_POOL))
    hits = cur.fetchall()
    doc_ids = sorted({h[0] for h in hits})
    cur.execute(
        """SELECT doc_id, array_agg(DISTINCT t) FROM rag.chunks, unnest(tokens) AS t
           WHERE doc_id = ANY(%s) AND t = ANY(%s::text[]) GROUP BY doc_id""",
        (doc_ids, q_tokens))
    doc_hits = {doc_id: set(tokens) for doc_id, tokens in cur.fetchall()}  # 문서 단위 토큰 일치
    q_set = set(q_tokens)
    best = {}
    for doc_id, chunk_id, text, tokens, dist in hits:
        overlap = len(q_set & doc_hits.get(doc_id, set())) / len(q_set)
        score = (1 - float(dist)) + weight * overlap
        if doc_id not in best or score > best[doc_id]["score"]:
            best[doc_id] = {"doc_id": doc_id, "chunk_id": chunk_id, "masked_text": text,
                            "tokens": tokens, "score": score}
    return sorted(best.values(), key=lambda d: -d["score"])[:k]


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
