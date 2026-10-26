"""탐지기를 실행해 run_compare.py 형식의 예측 파일을 만든다.

사용법 (프로젝트 루트에서)
    python -m detection.run_detect --detector rule --split dev
    python -m detection.run_detect --detector llm  --split dev --runs 3   # 일관성용 3회
    python -m detection.run_detect --detector moai --split dev --limit 5  # 앞 5건만 빠르게

결과: preds/<탐지기>_<split>.jsonl  (--runs N 이면 _r1 … _rN)
채점: python eval/run_compare.py --gold data/pii_synth_finance_dev.jsonl --pred rule=preds/rule_dev.jsonl
"""
import argparse
import time
from pathlib import Path

from detection.io import gold_path, load_docs, save_preds
from detection.postprocess import finalize
from detection.registry import get_detector


def run_once(detector, docs, raw: bool = False) -> list[dict]:
    rows = []
    for d in docs:
        t0 = time.perf_counter()
        spans = detector.detect(d["text"])
        if not raw:
            spans = finalize(d["text"], spans)
        # 속도 제한 대기 시간은 처리 시간에서 제외 (LLM)
        ms = max(0.0, (time.perf_counter() - t0 - getattr(detector, "waited", 0.0)) * 1000)
        rows.append({
            "id": d["id"],
            "entities": [{"label": s.label, "start": s.start, "end": s.end,
                          **({"confidence": s.confidence} if s.confidence != "" else {})}
                         for s in spans],
            "ms": round(ms, 2),
        })
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--detector", required=True)
    ap.add_argument("--split", default="dev",
                    help="dev | test | holdout | custom_A, custom_E 처럼 자체 검증셋 이름")
    ap.add_argument("--runs", type=int, default=1, help="반복 실행 횟수 (LLM 일관성)")
    ap.add_argument("--limit", type=int, default=0, help="앞 N건만 (빠른 확인용)")
    ap.add_argument("--raw", action="store_true", help="경계 후처리 끄기 (전후 비교용)")
    ap.add_argument("--tag", default="", help="파일 이름 꼬리표 (예: v2)")
    ap.add_argument("--out", default="preds")
    a = ap.parse_args()

    docs = load_docs(a.split)
    if a.limit:
        docs = docs[: a.limit]
    detector = get_detector(a.detector)
    name = a.detector + (f"_{a.tag}" if a.tag else "") + ("_raw" if a.raw else "")

    paths = []
    for i in range(1, a.runs + 1):
        if hasattr(detector, "failures"):
            detector.failures = 0
        rows = run_once(detector, docs, raw=a.raw)
        fails = getattr(detector, "failures", 0)
        if fails:
            print(f"⚠ {fails}건 호출 실패 → 빈 결과로 저장됨. 채점에 쓰지 말고 다시 돌리세요 "
                  f"(.env의 LLM_MIN_INTERVAL을 2~3으로 늘려 보기)")
        suffix = f"_r{i}" if a.runs > 1 else ""
        path = Path(a.out) / f"{name}_{a.split}{suffix}.jsonl"
        save_preds(path, rows)
        paths.append(str(path))
        avg = sum(r["ms"] for r in rows) / len(rows)
        n = sum(len(r["entities"]) for r in rows)
        print(f"[{i}/{a.runs}] {path}  문서 {len(rows)}건 · 탐지 {n}개 · 평균 {avg:.1f}ms/문서 "
              f"(1만 건 환산 {avg * 10000 / 1000 / 60:.1f}분)")

    gold = gold_path(a.split)
    print("\n채점:")
    print(f"python eval/run_compare.py --gold {gold} --pred {name}={','.join(paths)} --out results/{name}_{a.split}")


if __name__ == "__main__":
    main()
