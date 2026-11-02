"""Holdout 검색 평가 스크립트.

사용법:
    python eval/eval_holdout_retrieval.py --gold data/holdout_retrieval_queries_gold.jsonl --run outputs/run_hybrid.jsonl

run 파일 형식 (한 줄에 질의 하나, starter.run_queries 가 만들어 줌):
    {"qid": "Q001", "ranked_doc_ids": ["H0064", "H0012", ...]}

지표 (질의 유형별 + 전체):
    Hit@k     : 상위 k개 안에 정답 문서가 하나라도 있으면 1
    Recall@k  : 정답 문서 중 상위 k개 안에 들어온 비율
    MRR       : 첫 번째 정답 문서 순위의 역수 평균
    Confuse@k : 상위 k개 중 동명이인 등 혼동 문서(confusable_doc_ids)의 비율 (낮을수록 좋음)
검색 방법은 알려주지 않고 결과만 채점합니다.
"""
import argparse
import collections
import json


def load(path):
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def score(gold, run, ks=(1, 5, 10)):
    runs = {r["qid"]: r["ranked_doc_ids"] for r in run}
    rows = collections.defaultdict(lambda: collections.defaultdict(list))
    for q in gold:
        neutral = set(q.get("neutral_doc_ids", []))
        ranked = [d for d in runs.get(q["qid"], []) if d not in neutral]
        rel, conf = set(q["relevant_doc_ids"]), set(q.get("confusable_doc_ids", []))
        rr = next((1 / (i + 1) for i, d in enumerate(ranked) if d in rel), 0.0)
        for group in ("ALL", q["type"]):
            m = rows[group]
            m["MRR"].append(rr)
            for k in ks:
                top = set(ranked[:k])
                m[f"Hit@{k}"].append(1.0 if rel & top else 0.0)
                m[f"Recall@{k}"].append(len(rel & top) / len(rel))
                if conf:
                    m[f"Confuse@{k}"].append(len(conf & top) / max(len(ranked[:k]), 1))
            m["n"].append(1)
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gold", required=True)
    ap.add_argument("--run", required=True)
    args = ap.parse_args()
    rows = score(load(args.gold), load(args.run))
    cols = ["Hit@1", "Hit@5", "Recall@5", "Recall@10", "MRR", "Confuse@10"]
    print(f"{'type':<26}{'n':>5}" + "".join(f"{c:>11}" for c in cols))
    for group in ["ALL"] + sorted(g for g in rows if g != "ALL"):
        m = rows[group]
        cells = "".join(f"{(sum(m[c]) / len(m[c])):>11.3f}" if m[c] else f"{'-':>11}" for c in cols)
        print(f"{group:<26}{len(m['n']):>5}" + cells)


if __name__ == "__main__":
    main()
