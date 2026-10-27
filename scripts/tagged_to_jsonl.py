"""태그 달린 문서(txt) → 정답 jsonl (run_compare.py 형식).

LLM은 글자 위치(start/end)를 정확히 세지 못한다. 그래서 LLM에게는 개인정보를
[[라벨:값]] 태그로 감싸게만 하고, 위치 계산은 이 스크립트가 한다.

입력 형식 (txt)
    ### DOC 카톡상담
    [[person:윤 선영]] 고객님, 등록하신 [[phone:010.1234.5678]]로 문자 드렸어요.
    담당 정 부장이 확인 예정입니다.
    ### DOC 공지
    ...

라벨 줄임말: person, phone, email, address, rrn, account, card

사용법
    python scripts/tagged_to_jsonl.py data/custom/e_1.txt data/custom/e_2.txt --name E
    → data/custom/pii_custom_E.jsonl 생성 → 탐지: --split custom_E
    (--name 없이 쓰면 예전처럼 data/custom/pii_custom.jsonl)
"""
import json
import re
import sys
from pathlib import Path

LABELS = {
    "person": "person",
    "phone": "phone_number",
    "email": "email",
    "address": "address",
    "rrn": "resident_registration_number",
    "account": "account_number",
    "card": "card_number",
}
TAG = re.compile(r"\[\[([a-z_]+):(.+?)\]\]")
OUT_DIR = Path("data/custom")


def parse_doc(raw: str, doc_id: str) -> tuple[str, list[dict]]:
    text, ents, pos = [], [], 0
    for m in TAG.finditer(raw):
        text.append(raw[pos:m.start()])
        label, value = m.group(1), m.group(2)
        if label not in LABELS:
            raise SystemExit(f"{doc_id}: 모르는 라벨 [[{label}:...]] (가능: {', '.join(LABELS)})")
        if "[[" in value or value != value.strip():
            raise SystemExit(f"{doc_id}: 태그 안에 태그/앞뒤 공백 → [[{label}:{value}]]")
        start = sum(len(t) for t in text)
        text.append(value)
        ents.append({"label": LABELS[label], "start": start, "end": start + len(value), "text": value})
        pos = m.end()
    text.append(raw[pos:])
    full = "".join(text)
    if "[[" in full or "]]" in full:
        raise SystemExit(f"{doc_id}: 닫히지 않은 태그가 있음 → 원문 확인")
    for e in ents:   # 자기 검증
        assert full[e["start"]:e["end"]] == e["text"]
    return full, ents


def main(paths: list[str], name: str = "") -> None:
    OUT = OUT_DIR / (f"pii_custom_{name}.jsonl" if name else "pii_custom.jsonl")
    docs, n = [], 0
    for p in paths:
        raw = Path(p).read_text(encoding="utf-8")
        raw = re.sub(r"^```.*$", "", raw, flags=re.M)          # 코드블록 표시 제거
        # '### DOC 유형' 외에 복사 과정에서 바뀐 형태도 허용: 'DOC 유형', '**DOC 유형**', '## DOC: 유형'
        chunks = re.split(r"^[ \t]*(?:#{1,6}[ \t]*)?\**[ \t]*DOC\b[ \t:.-]*(.*?)[ \t*]*$", raw, flags=re.M)
        # chunks = [앞부분, 유형1, 본문1, 유형2, 본문2, ...]
        for doc_type, body in zip(chunks[1::2], chunks[2::2]):
            body = body.strip("\n")
            if not body.strip():
                continue
            n += 1
            doc_id = f"C{n:04d}"
            text, ents = parse_doc(body, doc_id)
            docs.append({"id": doc_id, "split": "custom", "doc_type": doc_type.strip() or "기타",
                         "source": Path(p).name, "text": text, "entities": ents,
                         "hard_cases": [] if ents else ["no_pii"]})
    if not docs:
        raise SystemExit("문서를 하나도 못 찾았어요. 각 문서 앞에 '### DOC 유형' 줄이 있는지 확인하세요.\n"
                         "(기존 data/custom/pii_custom.jsonl은 바뀌지 않았어요 → 이 상태로 채점하면 이전 셋 점수가 나와요)")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", encoding="utf-8") as f:
        for d in docs:
            f.write(json.dumps(d, ensure_ascii=False) + "\n")
    cnt = {}
    for d in docs:
        for e in d["entities"]:
            cnt[e["label"]] = cnt.get(e["label"], 0) + 1
    print(f"{OUT}  문서 {len(docs)}건 · 개체 {sum(cnt.values())}개")
    for k, v in sorted(cnt.items(), key=lambda x: -x[1]):
        print(f"  {k:30s} {v}")


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="태그 문서(txt) → 정답 jsonl")
    ap.add_argument("files", nargs="+", help="data/custom/e_1.txt e_2.txt …")
    ap.add_argument("--name", default="", help="셋 이름 (E → data/custom/pii_custom_E.jsonl)")
    a = ap.parse_args()
    main(a.files, a.name)
