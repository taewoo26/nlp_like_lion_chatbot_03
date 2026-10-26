"""run_compare 결과(summary.csv, consistency.csv)를 한 표로 합쳐서 보여 준다.

실행: python -m eval.show_summary --dir results/final_dev_final
출력: strict F1 / partial 재현율 / mask_rec / 문서당 ms / 1만 건 환산 / 일관성
"""
import argparse
import csv
from pathlib import Path


def read(path):
    if not path.exists():
        return []
    with path.open(encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True, help="run_compare 의 --out 폴더")
    a = ap.parse_args()
    d = Path(a.dir)
    summary = read(d / "summary.csv")
    cons = {r["system"]: r for r in read(d / "consistency.csv")}
    if not summary:
        raise SystemExit(f"{d}/summary.csv 가 없어요. run_compare 를 먼저 실행하세요.")

    def pct(v):
        return f"{float(v) * 100:.2f}%"

    rows = []
    for r in summary:
        ms = float(r["ms_per_doc"]) if r["ms_per_doc"] else None
        c = cons.get(r["system"])
        rows.append({
            "시스템": r["system"],
            "strict F1": pct(r["strict_f1"]),
            "partial 재현율": pct(r["partial_r"]),
            "mask_rec": pct(r["mask_rec"]),
            "문서당 ms": f"{ms:.2f}" if ms is not None else "-",
            "1만 건(분)": f"{ms * 10000 / 60000:.2f}" if ms is not None else "-",
            "일관성(Jaccard)": pct(c["mean_jaccard"]) if c else "-",
            "완전 동일 문서": pct(c["identical_doc_ratio"]) if c else "-",
        })

    cols = list(rows[0])
    print("\n| " + " | ".join(cols) + " |")
    print("|" + "|".join("---" for _ in cols) + "|")
    for r in rows:
        print("| " + " | ".join(r[c] for c in cols) + " |")
    out = d / "final_table.csv"
    with out.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)
    print(f"\n저장: {out}")
    print("참고: 문서당 ms는 첫 번째 실행의 평균이에요. LLM은 클라우드 호출 시간(재시도 대기 포함 가능)이라 로컬 CPU 모델과 조건이 달라요.")


if __name__ == "__main__":
    main()