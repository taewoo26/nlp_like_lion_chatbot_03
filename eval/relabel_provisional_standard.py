"""새 기준 AD-05에 맞춰 주소 앞 우편번호만 제외한 정답 사본을 만든다.

원본 JSONL은 보존한다. 외국인등록번호·분절값은 기존 자료에 새 정답 사례가
없으므로 이 스크립트에서 생성하거나 추정하지 않는다.
"""
import argparse
import hashlib
import json
import re
from pathlib import Path


ZIP_PREFIX = re.compile(r"\(\d{5}\)[ \t]*")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    if args.out.exists():
        parser.error(f"출력 파일이 이미 있습니다: {args.out}")

    source_bytes = args.source.read_bytes()
    docs = [json.loads(line) for line in source_bytes.splitlines() if line.strip()]
    changes = []
    for doc in docs:
        for entity in doc["entities"]:
            if entity["label"] != "address":
                continue
            prefix = ZIP_PREFIX.match(entity["text"])
            if not prefix:
                continue
            old_start, end = entity["start"], entity["end"]
            assert doc["text"][old_start:end] == entity["text"]
            entity["start"] += prefix.end()
            entity["text"] = doc["text"][entity["start"]:end]
            changes.append({"doc_id": doc["id"], "old_start": old_start,
                            "new_start": entity["start"], "end": end})

    args.out.parent.mkdir(parents=True, exist_ok=True)
    payload = "".join(json.dumps(doc, ensure_ascii=False) + "\n" for doc in docs)
    args.out.write_text(payload, encoding="utf-8", errors="strict")
    manifest = {
        "policy": "user_provisional_2026-10-28_AD-05",
        "source": str(args.source),
        "source_sha256": hashlib.sha256(source_bytes).hexdigest(),
        "output_sha256": hashlib.sha256(payload.encode()).hexdigest(),
        "documents": len(docs),
        "changes": changes,
    }
    args.out.with_suffix(".manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"{len(docs)}문서, 주소 경계 {len(changes)}건 변경: {args.out}")


if __name__ == "__main__":
    main()
