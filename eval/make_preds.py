"""탐지기를 정답 문서에 돌려서 run_compare.py가 읽는 예측 jsonl을 만든다.

실행 (레포 루트에서)
    python -m eval.make_preds --detector rule
    python -m eval.make_preds --detector llm --limit 5          # LLM은 5건으로 먼저 시험
    python -m eval.make_preds --detector llm --runs 3           # 일관성 측정용 3회 반복

결과: results/preds/<탐지기>_<tag>.jsonl  (반복 실행이면 _run1, _run2 …)
주의: holdout 파일은 최종 평가 전까지 --data 로 넣지 않는다.
"""
import argparse
import json
import time
from pathlib import Path


def load_detector(name: str):
    if name == "rule":
        from detection.rule import RuleDetector
        return RuleDetector()
    if name == "rule_kiwi":
        from detection.rule import RuleDetector
        return RuleDetector(use_kiwi=True)
    if name == "llm":
        from detection.llm import LLMDetector
        return LLMDetector()
    raise SystemExit(f"알 수 없는 탐지기: {name}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--detector", default="rule", choices=["rule", "rule_kiwi", "llm"])
    ap.add_argument("--data", default="data/dev/pii_synth_finance_dev.jsonl")
    ap.add_argument("--tag", default="dev", help="출력 파일 이름에 붙일 구분 (dev/test)")
    ap.add_argument("--out-dir", default="results/preds")
    ap.add_argument("--runs", type=int, default=1, help="같은 문서를 몇 번 돌릴지 (LLM 일관성용)")
    ap.add_argument("--limit", type=int, default=None, help="앞에서 N건만")
    a = ap.parse_args()

    docs = [json.loads(l) for l in Path(a.data).open(encoding="utf-8") if l.strip()]
    if a.limit:
        docs = docs[:a.limit]
    det = load_detector(a.detector)
    out_dir = Path(a.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    paths = []
    for r in range(a.runs):
        suffix = f"_run{r + 1}" if a.runs > 1 else ""
        path = out_dir / f"{det.name}_{a.tag}{suffix}.jsonl"
        with path.open("w", encoding="utf-8") as f:
            for d in docs:
                t0 = time.perf_counter()
                spans = det.detect(d["text"])
                ms = (time.perf_counter() - t0) * 1000
                f.write(json.dumps({
                    "id": d["id"],
                    "entities": [{"label": s.label, "start": s.start, "end": s.end} for s in spans],
                    "ms": round(ms, 2),
                }, ensure_ascii=False) + "\n")
        paths.append(str(path))
        print(f"저장: {path} ({len(docs)}건)")

    print("\n채점 명령:")
    print(f"  python -m eval.run_compare --gold {a.data} --pred {det.name}={','.join(paths)}")


if __name__ == "__main__":
    main()
