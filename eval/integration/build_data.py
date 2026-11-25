"""통합 테스트 데이터: it_source(원본) → 파생 JSONL.

    python eval/integration/build_data.py --root data/integration_v1

원본 (사람이 작성, 이것만 고친다)
  it_source/docs/IT####.txt   문서 본문. 개인정보·인물은 [[값|라벨|소유 인물|표시]]로 적는다.
                              소유 인물: PS01 · 공유면 PS36+PS37 · 인물에 속하지 않으면 -
                              표시(선택): hard = 탐지가 어려운 표기 (정답에는 넣고 시뮬레이션에서는 미탐지로 둠)
  it_source/docs.csv          문서 메타데이터 단일 원본 (엑셀 문서 설계표에서 내보냄)
  it_source/persons.csv       인물 마스터 (엑셀에서 내보냄)
  it_source/questions.yaml    질문·정답지 (있으면 질문 파생 파일도 만든다)
파생 (직접 고치지 않음)
  it_docs.jsonl               적재 입력 {id, text, doc_type}                 → starter.ingest --data
  it_docs_labeled.jsonl       탐지 정답 {id, text, doc_type, split, entities} → eval/run_compare.py --gold
  it_persons_gold.jsonl       특정 가능한 인물 언급 {id, persons:[...]}      → eval/eval_person_resolution.py --gold
  it_mentions_gold.jsonl      모든 인물 언급과 특정 가능 여부 (미확정 언급 포함)
  it_manifest.json            원본·파생 해시, 문서 수, split 목록
"""
import argparse
import csv
import hashlib
import json
import pathlib
import re

MARK = re.compile(r"\[\[([^|\]]+)\|([a-z_]+)(?:\|([^|\]]+))?(?:\|([a-z_]+))?\]\]")
DETECT_LABELS = ("person", "phone_number", "email", "address", "resident_registration_number",
                 "account_number", "card_number")
OTHER_LABELS = ("driver_license", "passport_number", "customer_id", "employee_id")
VERSION = "integration_v1"


def parse_doc(src):
    """원본 → (평문, 개체 목록). 개체: label, start, end, text, owner, flag"""
    out, ents, pos, length = [], [], 0, 0
    for m in MARK.finditer(src):
        chunk = src[pos:m.start()]
        out.append(chunk)
        length += len(chunk)
        val, label, owner, flag = m.group(1), m.group(2), m.group(3), m.group(4)
        if label not in DETECT_LABELS + OTHER_LABELS:
            raise ValueError(f"알 수 없는 라벨 {label!r}: {m.group(0)}")
        ents.append({"label": label, "start": length, "end": length + len(val), "text": val,
                     "owner": owner, "flag": flag})
        out.append(val)
        length += len(val)
        pos = m.end()
    out.append(src[pos:])
    text = "".join(out)
    if "[[" in text or "]]" in text:
        raise ValueError("닫히지 않은 표시가 있음")
    return text, ents


def owner_value(owner):
    if owner in (None, "-"):
        return None
    return owner.split("+") if "+" in owner else owner


def name_form(text, name):
    if re.fullmatch(r"[A-Za-z .'-]+", text):
        return "latin"
    if text == name:
        return "full"
    if " " in text and text.replace(" ", "") == name:
        return "spaced"
    return "given"


def sha(path):
    return hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()


def load_csv(path):
    with open(path, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def load_docs(root):
    src = root / "it_source"
    meta = {r["doc_id"]: r for r in load_csv(src / "docs.csv")}
    docs = []
    for did in sorted(meta):
        path = src / "docs" / f"{did}.txt"
        text, ents = parse_doc(path.read_text(encoding="utf-8"))
        docs.append({"id": did, "text": text, "entities": ents, "meta": meta[did], "path": path})
    extra = sorted(p.stem for p in (src / "docs").glob("*.txt") if p.stem not in meta)
    if extra:
        raise ValueError(f"docs.csv에 없는 본문 파일: {extra}")
    return docs


def write_jsonl(path, rows):
    with open(path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default="data/integration_v1")
    a = ap.parse_args()
    root = pathlib.Path(a.root)
    persons = {r["person_id"]: r for r in load_csv(root / "it_source" / "persons.csv")}
    docs = load_docs(root)

    plain, labeled, pgold, mentions = [], [], [], []
    for d in docs:
        m = d["meta"]
        plain.append({"id": d["id"], "text": d["text"], "doc_type": m["doc_type"]})
        ents = []
        for e in d["entities"]:
            row = {"label": e["label"], "start": e["start"], "end": e["end"], "text": e["text"],
                   "owner_person_id": owner_value(e["owner"])}
            if e["flag"]:
                row["variant"] = e["flag"]
            ents.append(row)
        labeled.append({"id": d["id"], "text": d["text"], "doc_type": m["doc_type"], "split": m["split"],
                        "track": m["track"], "difficulty": m["difficulty"], "entities": ents})
        ps = []
        for e in d["entities"]:
            if e["label"] != "person":
                continue
            pid = e["owner"]
            p = persons.get(pid)
            if p is None:
                raise ValueError(f"{d['id']}: 인물 마스터에 없는 {pid}")
            ambiguous = p["group"] == "미확정 언급"
            mentions.append({"doc_id": d["id"], "start": e["start"], "end": e["end"], "text": e["text"],
                             "person_id": None if ambiguous else pid,
                             "status": "ambiguous" if ambiguous else "determinable"})
            if not ambiguous:
                ps.append({"person_id": pid, "start": e["start"], "end": e["end"], "text": e["text"],
                           "form": name_form(e["text"], p["name"])})
        pgold.append({"id": d["id"], "persons": ps})

    out = {"it_docs.jsonl": plain, "it_docs_labeled.jsonl": labeled,
           "it_persons_gold.jsonl": pgold, "it_mentions_gold.jsonl": mentions}
    for name, rows in out.items():
        write_jsonl(root / name, rows)

    q_path = root / "it_source" / "questions.yaml"
    if q_path.exists():
        import build_questions as bq          # 질문 파생 파일 (같은 폴더)
        out.update(bq.build(root, docs, persons))

    src_files = sorted((root / "it_source").rglob("*"))
    manifest = {
        "version": VERSION,
        "docs": len(docs),
        "splits": {s: [d["id"] for d in docs if d["meta"]["split"] == s] for s in ("dev", "check")},
        "source_sha256": {str(p.relative_to(root)): sha(p) for p in src_files if p.is_file()},
        "derived_sha256": {name: sha(root / name) for name in out},
        "counts": {name: len(rows) for name, rows in out.items()},
    }
    (root / "it_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    for name, rows in out.items():
        print(f"{name}: {len(rows)}")


if __name__ == "__main__":
    main()
