"""문서/개체 CSV를 검사하고 원래 split 그대로 평가용 JSONL로 변환한다.

실행: python -m eval.prepare_generated_data --source data/generated_v1
정답 의미나 split을 변경하지 않으며, 기존 출력은 덮어쓰지 않는다.
"""
import argparse
import csv
import hashlib
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

from eval.run_compare import LABELS


def prepare(source):
    def read(name):
        with (source / name).open(encoding="utf-8-sig", newline="") as stream:
            return list(csv.DictReader(stream))

    rows = read("generated_documents.csv")
    annotations = read("generated_entities.csv")
    docs = {}
    by_split = defaultdict(list)
    for row in rows:
        identifier, split = row["doc_id"], row["split"]
        if not identifier or identifier in docs:
            raise ValueError(f"문서 ID 중복 또는 누락: {identifier}")
        if not re.fullmatch(r"[A-Za-z0-9_-]+", split):
            raise ValueError(f"잘못된 split: {split}")
        doc = dict(id=identifier, text=row["text"], entities=[],
                   split=split, source_set=row["set"],
                   category=row["category"], style=row["style"])
        docs[identifier] = doc
        by_split[split].append(doc)
    for row in annotations:
        doc = docs.get(row["doc_id"])
        if doc is None:
            raise ValueError(f"문서 없는 정답: {row['doc_id']}")
        if (doc["split"], doc["source_set"], doc["category"], doc["style"]) != tuple(
                row[k] for k in ("split", "set", "category", "style")):
            raise ValueError(f"개체/문서 메타데이터 불일치: {doc['id']}")
        start, end = int(row["start"]), int(row["end"])
        if row["label"] not in LABELS or not 0 <= start < end <= len(doc["text"]):
            raise ValueError(f"잘못된 라벨/위치: {doc['id']}")
        if doc["text"][start:end] != row["value"]:
            raise ValueError(f"정답 문자열 불일치: {doc['id']}")
        entity = dict(label=row["label"], start=start, end=end, text=row["value"])
        if entity in doc["entities"]:
            raise ValueError(f"중복 정답: {doc['id']}")
        doc["entities"].append(entity)
    for row in rows:
        doc = docs[row["doc_id"]]
        plain, marked_entities, cursor = "", [], 0
        for match in re.finditer(r"\[\[(.*?)\|([^\[\]]+)\]\]", row["text_with_labels"], re.S):
            plain += row["text_with_labels"][cursor:match.start()]
            start = len(plain)
            plain += match[1]
            marked_entities.append(dict(label=match[2], start=start, end=len(plain), text=match[1]))
            cursor = match.end()
        plain += row["text_with_labels"][cursor:]
        order = lambda e: (e["start"], e["end"], e["label"], e["text"])
        doc["entities"].sort(key=order)
        if (plain != doc["text"] or sorted(marked_entities, key=order) != doc["entities"]
                or int(row["pii_count"]) != len(doc["entities"])):
            raise ValueError(f"라벨 표시/개체 수 불일치: {doc['id']}")
    return by_split


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path("data/generated_v1"))
    args = parser.parse_args()
    splits = prepare(args.source)
    outputs = [args.source / f"{split}.jsonl" for split in splits]
    manifest_path = args.source / "manifest.json"
    if any(path.exists() for path in [*outputs, manifest_path]):
        raise SystemExit("변환 출력이 이미 있습니다. 새 버전 폴더를 사용하세요.")
    groups = defaultdict(set)
    texts = defaultdict(set)
    manifest = dict(source_sha256={}, splits={})
    for name in ("generated_documents.csv", "generated_entities.csv"):
        manifest["source_sha256"][name] = hashlib.sha256((args.source / name).read_bytes()).hexdigest()
    for split, docs in splits.items():
        payload = "".join(json.dumps(doc, ensure_ascii=False) + "\n" for doc in docs)
        (args.source / f"{split}.jsonl").write_text(payload, encoding="utf-8")
        manifest["splits"][split] = dict(documents=len(docs),
            entities=sum(len(d["entities"]) for d in docs),
            labels=dict(Counter(e["label"] for d in docs for e in d["entities"])),
            sha256=hashlib.sha256(payload.encode()).hexdigest())
        for doc in docs:
            groups[doc["id"].split("-")[0]].add(split)
            texts[doc["text"]].add(split)
    manifest["cross_split_id_prefix_groups"] = sum(len(v) > 1 for v in groups.values())
    manifest["cross_split_exact_text_groups"] = sum(len(v) > 1 for v in texts.values())
    manifest["notes"] = ["원본 split 유지. ID 접두어 공유는 템플릿 공유 가능성으로, 생성 방식 확인 필요.",
                         "문자 위치 일치는 검증했으나 라벨 정책의 적절성을 보장하지 않음.",
                         "hard_cases 및 person form 태그를 추정해 추가하지 않음."]
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
