"""팀원이 만든 CSV 두 개(generated_documents.csv, generated_entities.csv) → 채점용 jsonl (split별 파일).

사용법 (레포 루트에서)
    python scripts/csv_to_jsonl.py data/team/generated_documents.csv data/team/generated_entities.csv
    → data/team/synth_dev.jsonl, synth_holdout.jsonl, synth_hard_dev.jsonl, synth_hard_eval.jsonl, synth_fresh.jsonl
"""
import csv
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path


def main(doc_csv, ent_csv, out_dir="data/team"):
    docs = list(csv.DictReader(open(doc_csv, encoding="utf-8-sig")))
    ents = defaultdict(list)
    for e in csv.DictReader(open(ent_csv, encoding="utf-8-sig")):
        ents[(e["split"], e["doc_id"])].append(e)

    by_split = defaultdict(list)
    for d in docs:
        text = d["text"]
        spans = []
        for e in ents.get((d["split"], d["doc_id"]), []):
            s, t = int(e["start"]), int(e["end"])
            if text[s:t] != e["value"]:
                raise SystemExit(f"위치 불일치: {d['split']} {d['doc_id']} {e['value']!r} vs {text[s:t]!r}")
            spans.append({"label": e["label"], "start": s, "end": t, "text": e["value"]})
        spans.sort(key=lambda x: (x["start"], x["end"]))
        by_split[d["split"]].append({
            "id": d["doc_id"], "split": d["split"], "set": d["set"],
            "doc_type": d["category"], "style": d["style"],
            "text": text, "entities": spans,
            "hard_cases": [] if spans else ["no_pii"],
        })

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    for split, rows in by_split.items():
        ids = [r["id"] for r in rows]
        if len(ids) != len(set(ids)):
            raise SystemExit(f"{split}: 문서 id 중복")
        path = out / f"{split}.jsonl"
        with path.open("w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        cnt = Counter(e["label"] for r in rows for e in r["entities"])
        print(f"{path}  문서 {len(rows)}건 · 개체 {sum(cnt.values())}개  ({rows[0]['set']})")
        print("   " + ", ".join(f"{k} {v}" for k, v in cnt.most_common()))


if __name__ == "__main__":
    if len(sys.argv) < 3:
        raise SystemExit("사용법: python scripts/csv_to_jsonl.py <documents.csv> <entities.csv> [출력폴더]")
    main(*sys.argv[1:])
