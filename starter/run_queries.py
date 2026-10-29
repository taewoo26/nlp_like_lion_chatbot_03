"""검색 질의 파일 전체를 돌려 평가용 run 파일을 만듭니다.

    python -m starter.run_queries --out outputs/run_v1.jsonl
    python eval/eval_holdout_retrieval.py --gold data/holdout_retrieval_queries_gold.jsonl --run outputs/run_v1.jsonl

검색 설계를 바꿀 때마다 --out 이름을 바꿔 저장해 두면 버전별로 비교할 수 있습니다.
"""
import argparse
import json
import time

from . import config, db
from .search import search


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--queries", default=str(config.DATA_DIR / "holdout_retrieval_queries.jsonl"))
    ap.add_argument("-k", type=int, default=20)
    ap.add_argument("--out")
    a = ap.parse_args()
    out = a.out or str(config.OUT_DIR / "run.jsonl")
    config.OUT_DIR.mkdir(exist_ok=True)
    with open(a.queries, encoding="utf-8") as f:
        queries = [json.loads(l) for l in f if l.strip()]
    t0 = time.perf_counter()
    with db.rag_conn() as conn, open(out, "w", encoding="utf-8") as f:
        for q in queries:
            masked_q, q_tokens, docs = search(q["query"], a.k, conn)
            f.write(json.dumps({"qid": q["qid"], "masked_query": masked_q, "query_tokens": q_tokens,
                                "ranked_doc_ids": [d["doc_id"] for d in docs]}, ensure_ascii=False) + "\n")
    ms = (time.perf_counter() - t0) * 1000 / len(queries)
    print(f"질의 {len(queries)}개 → {out}  ({ms:.1f} ms/질의)")


if __name__ == "__main__":
    main()
