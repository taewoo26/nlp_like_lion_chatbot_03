"""통합 테스트 데이터 정합성 검사 (설계서 03 시트 14번 '정합성 검사').

    python eval/integration/validate_data.py --root data/integration_v1 --repo .

검사 (오류가 하나라도 있으면 종료 코드 1)
  1 ID·참조       문서·인물 ID 형식, docs.csv 등장 인물 = 본문 표시 인물 = 노출 설계표 인물
  2 노출 일치     노출 설계표의 값이 본문에 같은 라벨·소유 인물로 표시됨, 본문의 개인정보 표시가 노출 설계표에 있음
  3 넣지 않을 정보 그 문서에 노출하지 않기로 한 마스터 값(다른 인물 값 포함)이 본문에 없음
  4 이름          표시하지 않은 이름 언급 없음, 다른 문서 인물의 이름이 섞이지 않음
  5 표시 밖 값    개인정보처럼 보이는 표시 밖 값은 과잉 탐지 값 목록에 있는 것만
  6 doc_type      등록 유형만 (예외: 미등록 '예금상담' IT0049·IT0080)
  7 문서별 제약   설계 제약(길이, 위치, 금지 표현, 과잉 탐지 값 포함, 청크 위치)
--repo 를 주면 저장소의 pii_pipeline.pseudonymize(정답 span 사용)로 청크 위치를 확인한다 (저장소 코드는 읽기만).
"""
import argparse
import collections
import json
import os
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from build_data import load_csv, load_docs  # noqa: E402

KIND_LABEL = {"전화": "phone_number", "이메일": "email", "계좌": "account_number", "주민번호": "resident_registration_number",
              "카드": "card_number", "주소": "address", "운전면허": "driver_license", "여권번호": "passport_number",
              "고객번호": "customer_id", "사번": "employee_id"}
LOOKALIKE = {"02-2026-1124": "IT0016", "351-260917-44": "IT0028", "30-571204-88": "IT0028", "02-3150-7000": "IT0042",
             "1588-7240": "IT0042", "2026-1101-0042-7788": "IT0044", "214-86-12345": "IT0044", "010-****-5521": "IT0083"}
NAME_LIKE = {"IT0043": ["윤슬 적금", "도담 체크카드", "하랑지점"]}
UNREGISTERED_OK = {"예금상담": {"IT0049", "IT0080"}}
NO_OWNER_OK = {("IT0028", "457-203918-62"), ("IT0028", "228-610473-05")}   # 사기 이용 계좌 (인물 소유 아님)
PII_LIKE = [
    ("전화형", re.compile(r"(?<![\d*])0\d{1,2}[-. ]?[\d*]{3,4}[-. ]?\d{4}(?!\d)")),
    ("대표번호형", re.compile(r"(?<!\d)1[5-9]\d{2}-\d{4}(?!\d)")),
    ("주민번호형", re.compile(r"(?<!\d)\d{6}\s?-\s?\d{7}(?!\d)")),
    ("카드형", re.compile(r"(?<!\d)\d{4}-\d{4}-\d{4}-\d{4}(?!\d)")),
    ("계좌형", re.compile(r"(?<![\d-])\d{2,6}-\d{2,6}-\d{2,6}(?![\d-])")),
    ("이메일", re.compile(r"[A-Za-z0-9._-]+@[A-Za-z0-9.-]+")),
]
DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
ADDR = re.compile(r"(?:서울특별시|부산광역시|대구광역시|인천광역시|광주광역시|대전광역시|경기도) [가-힣]+ [가-힣0-9]+ \d+(?:, \d+(?:호|층))?")
TOKEN_LEN = {"person": 21, "phone_number": 27, "email": 20, "address": 22, "resident_registration_number": 43,
             "account_number": 29, "card_number": 26}


def digits_regex(value):
    ds = re.sub(r"\D", "", value)
    return re.compile(r"(?<!\d)" + r"[-. ]?".join(ds) + r"(?!\d)") if len(ds) >= 7 else None


def master_values(p):
    """인물 마스터에서 (종류, 값) 목록. 가린 값은 제외."""
    cols = " ".join(p[k] for k in ("phone", "email", "account", "customer_or_employee_no", "other_pii"))
    out = []
    for kind, rx in (("전화", r"0\d{1,2}-\d{3,4}-\d{4}"), ("이메일", r"[\w.]+@[\w.]+"), ("주민번호", r"\d{6}-\d{7}"),
                     ("카드", r"9410-\d{4}-\d{4}-\d{4}"), ("운전면허", r"\d{2}-\d{2}-\d{6}-\d{2}"),
                     ("고객번호", r"C-\d{4}-\d{4}"), ("사번", r"SB\d{6}"), ("여권번호", r"\b[A-Z]\d{8}\b")):
        out += [(kind, v) for v in re.findall(rx, cols)]
    out += [("계좌", v) for v in re.findall(r"(?<![\d-])\d{3}-\d{6}-\d{2}(?![\d-])", p["account"])]
    out += [("계좌", v) for v in re.findall(r"DE\d{2}(?: \d{4}){4} \d{2}", p["account"])]
    out += [("주소", v) for v in ADDR.findall(cols)]
    return out


def present(text, kind, value):
    if kind in ("이메일", "주소", "고객번호", "사번", "여권번호"):
        return value in text
    rx = digits_regex(value)
    return bool(rx and rx.search(text))


def chunk(text, max_chars=600):
    parts, cur = [], ""
    for line in text.split("\n"):
        if cur and len(cur) + len(line) + 1 > max_chars:
            parts.append(cur)
            cur = line
        else:
            cur = f"{cur}\n{line}" if cur else line
    if cur.strip():
        parts.append(cur)
    return parts or [text]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default="data/integration_v1")
    ap.add_argument("--repo", help="저장소 경로 (청크 위치 확인에 pii_pipeline 사용)")
    a = ap.parse_args()
    root = pathlib.Path(a.root)
    src = root / "it_source"
    persons = {r["person_id"]: r for r in load_csv(src / "persons.csv")}
    expo = load_csv(src / "exposure.csv")
    docs = load_docs(root)
    errs, warns = [], []

    pipeline = None
    if a.repo:
        os.environ.setdefault("HMAC_KEY", "validate-only-key")
        sys.path.insert(0, a.repo)
        from starter import pii_pipeline as pipeline   # noqa: E402

    # 1 ID·참조
    id_rx = {"doc": r"IT\d{4}", "person": r"(?:PS\d{2}[AB]?|DS\d{2}|ST\d{2}|AMB\d{2})"}
    expo_by_doc = collections.defaultdict(list)
    for e in expo:
        expo_by_doc[e["doc_id"]].append(e)
    for pid in persons:
        if not re.fullmatch(id_rx["person"], pid):
            errs.append(f"인물 ID 형식 {pid}")
    seen_names = collections.defaultdict(set)
    for d in docs:
        did = d["id"]
        if not re.fullmatch(id_rx["doc"], did):
            errs.append(f"문서 ID 형식 {did}")
        listed = {x.strip() for x in d["meta"]["persons"].split(",") if x.strip() and x.strip() != "없음"}
        marked = {e["owner"] for e in d["entities"] if e["label"] == "person"}
        in_expo = {e["person_id"] for e in expo_by_doc[did]}
        if not (listed == marked == in_expo):
            errs.append(f"{did} 등장 인물 불일치: docs.csv {sorted(listed)} / 본문 {sorted(marked)} / 노출표 {sorted(in_expo)}")
        for e in d["entities"]:
            if e["label"] == "person":
                seen_names[e["owner"]].add(e["text"])
            for o in (e["owner"] or "").split("+"):
                if o and o != "-" and o not in persons:
                    errs.append(f"{did} 마스터에 없는 소유 인물 {o}")
            if d["text"][e["start"]:e["end"]] != e["text"]:
                errs.append(f"{did} span 불일치 {e}")

    # 2 노출 일치
    for d in docs:
        did, text = d["id"], d["text"]
        ents = [e for e in d["entities"] if e["label"] != "person"]
        used = set()
        for x in expo_by_doc[did]:
            if x["pii_kind"] in ("없음", ""):
                continue
            label, val = KIND_LABEL[x["pii_kind"]], x["value"].strip("()")
            if "*" in val:
                if val not in text:
                    errs.append(f"{did} 가린 값 {val} 없음")
                continue
            hit = [i for i, e in enumerate(ents) if e["label"] == label and e["text"] == val
                   and x["person_id"] in (e["owner"] or "").split("+")]
            if not hit:
                errs.append(f"{did} 노출표 값이 본문 표시에 없음: {x['person_id']} {x['pii_kind']} {val}")
            used.update(hit)
        for i, e in enumerate(ents):
            if i in used:
                continue
            if e["owner"] == "-" and (did, e["text"]) in NO_OWNER_OK:
                continue
            errs.append(f"{did} 노출표에 없는 표시 {e['label']} {e['text']} (소유 {e['owner']})")

    # 3 넣지 않을 정보 (마스터 값 중 이 문서 노출표에 없는 것)
    exposed = collections.defaultdict(set)
    for x in expo:
        if x["pii_kind"] not in ("없음", ""):
            exposed[x["doc_id"]].add(re.sub(r"\D", "", x["value"]) or x["value"])
    for d in docs:
        did, text = d["id"], d["text"]
        allowed = exposed[did] | {x["value"] for x in expo_by_doc[did]}
        for pid, p in persons.items():
            for kind, val in master_values(p):
                key = re.sub(r"\D", "", val) if kind not in ("이메일", "주소") else val
                if key in allowed or val in allowed:
                    continue
                if present(text, kind, val):
                    errs.append(f"{did} 넣지 않을 값이 본문에 있음: {pid} {kind} {val}")

    # 4 이름
    full = {pid: p["name"] for pid, p in persons.items() if p["group"] != "미확정 언급"}
    for d in docs:
        did, text = d["id"], d["text"]
        spans = [(e["start"], e["end"]) for e in d["entities"] if e["label"] == "person"]
        here = {e["owner"] for e in d["entities"] if e["label"] == "person"}
        for pid, name in full.items():
            for m in re.finditer(re.escape(name), text):
                if not any(s <= m.start() and m.end() <= t for s, t in spans):
                    errs.append(f"{did} 표시하지 않은 이름 '{name}' ({pid}, 위치 {m.start()})")
        for e in d["entities"]:
            if e["label"] != "person" or e["owner"].startswith("AMB"):
                continue
            name = persons[e["owner"]]["name"]
            if e["text"] != name and not name.endswith(e["text"]) and not re.fullmatch(r"[A-Z ]+", e["text"]):
                errs.append(f"{did} 이름 표기 '{e['text']}'가 {e['owner']} '{name}'와 맞지 않음")
        names_here = {}
        for pid in here:
            names_here.setdefault(persons[pid]["name"], set()).add(pid)
        for n, ps in names_here.items():
            if len(ps) > 1 and ps not in ({"PS34", "PS35"},):
                errs.append(f"{did} 한 문서에 의도하지 않은 동명이인 {n} {sorted(ps)}")

    # 5 표시 밖 값
    for d in docs:
        did, text = d["id"], d["text"]
        spans = [(e["start"], e["end"]) for e in d["entities"]]
        for kind, rx in PII_LIKE:
            for m in rx.finditer(text):
                v = m.group()
                if DATE.match(v) or any(s <= m.start() < t for s, t in spans):
                    continue
                if LOOKALIKE.get(v) == did:
                    continue
                errs.append(f"{did} 표시 밖 {kind} 값 '{v}'")
    for v, did in LOOKALIKE.items():
        if v not in next(x["text"] for x in docs if x["id"] == did):
            errs.append(f"{did} 과잉 탐지 값 '{v}' 없음")
    for did, vs in NAME_LIKE.items():
        t = next(x["text"] for x in docs if x["id"] == did)
        errs += [f"{did} 이름처럼 보이는 값 '{v}' 없음" for v in vs if v not in t]

    # 6 doc_type
    if a.repo:
        pol = pathlib.Path(a.repo) / "policy" / "access_policy.yaml"
        registered = set(re.findall(r"^\s{2}([^\s#:][^:]*):\s*\{", pol.read_text(encoding="utf-8"), re.M))
        for d in docs:
            t = d["meta"]["doc_type"]
            if t not in registered and d["id"] not in UNREGISTERED_OK.get(t, set()):
                errs.append(f"{d['id']} 미등록 doc_type {t}")

    # 7 문서별 제약
    T = {d["id"]: d for d in docs}
    def text(i): return T[i]["text"]
    def need(cond, msg):
        if not cond:
            errs.append(msg)
    need("금리" not in text("IT0001"), "IT0001 금리 문장 금지")
    need("만 원" not in text("IT0014"), "IT0014 한도 금액 금지")
    need("잃어버" not in text("IT0017"), "IT0017 '잃어버' 표현 금지")
    need("도난" in text("IT0032") and "발송 완료" not in text("IT0032"), "IT0032 도난·재발급 미완료")
    need("보이스피싱" not in text("IT0037"), "IT0037 '보이스피싱' 단어 금지")
    need(text("IT0026").count("서소담") == 1, "IT0026 성명은 1회만")
    need(text("IT0081").count("염다빈") == 1, "IT0081 성명은 처음 1회만")
    for line in text("IT0027").split("\n"):
        if "시우 님" in line:
            need("장시우" not in line and "엄시우" not in line, "IT0027 '시우 님' 줄에 성명 금지")
    need("동명이인" in text("IT0070"), "IT0070 '동명이인' 표기")
    hard46 = [e for e in T["IT0046"]["entities"] if e["flag"] == "hard"]
    need(len(hard46) == 2 and all(e["end"] <= 150 for e in hard46), "IT0046 어려운 표기 2개가 앞 150자 안에")
    n47 = len(text("IT0047"))
    rrn47 = [e for e in T["IT0047"]["entities"] if e["label"] == "resident_registration_number"]
    need(450 <= n47 <= 580, f"IT0047 길이 450~580자 (현재 {n47})")
    need(rrn47 and rrn47[0]["start"] >= 250, "IT0047 주민번호는 250자 이후")
    if pipeline:
        def masked_chunks(d):
            spans = [{"label": e["label"], "start": e["start"], "end": e["end"]} for e in d["entities"]
                     if e["label"] in TOKEN_LEN or e["label"] == "driver_license"]
            spans = [s for s, e in zip(spans, [e for e in d["entities"] if e["label"] in TOKEN_LEN or e["label"] == "driver_license"])
                     if not e["flag"]]
            masked, _ = pipeline.pseudonymize(d["text"], spans)
            return chunk(masked)
        for did, front, back in (("IT0045", ["담당"], ["상담일", "면제", "<ACCOUNT_NUMBER_"]),
                                 ("IT0081", [], ["<ACCOUNT_NUMBER_", "우대금리는"])):
            parts = masked_chunks(T[did])
            need(len(parts) >= 2, f"{did} 가명처리 후 2청크 이상 (현재 {len(parts)})")
            if len(parts) >= 2:
                need(all(w in parts[0] for w in front), f"{did} 앞 청크 내용")
                need(all(any(w in p for p in parts[1:]) and w not in parts[0] for w in back), f"{did} 뒤 청크에만 있어야 할 내용 {back}")

    # 8 질문·정답지 (it_cases.jsonl 이 있으면)
    cases_path = root / "it_cases.jsonl"
    if cases_path.exists():
        cases = [json.loads(l) for l in open(cases_path, encoding="utf-8")]
        dtext = {d["id"]: d["text"] for d in docs}
        dsplit = {d["id"]: d["meta"]["split"] for d in docs}
        ents_by_doc = {d["id"]: {(e["label"], e["text"]) for e in d["entities"]} for d in docs}
        groups = {r["group_id"]: r for r in load_csv(src / "question_groups.csv")}
        qtexts = collections.Counter(c["query"] for c in cases)
        errs += [f"중복 질문 '{q}'" for q, n in qtexts.items() if n > 1]
        dev_docs = {x for c in cases if c["split"] == "dev" for x in c["relevant_doc_ids"]}
        dev_ps = {x for c in cases if c["split"] == "dev" for x in c["expected_person_ids"]}
        for c in cases:
            qid, sp = c["qid"], c["split"]
            if c["split"] != groups[c["group_id"]]["split"]:
                errs.append(f"{qid} split이 그룹과 다름")
            for x in c["relevant_doc_ids"]:
                if dsplit[x] != sp:
                    errs.append(f"{qid} ({sp}) 정답 문서 {x}가 {dsplit[x]} 문서")
            if sp == "check":
                errs += [f"{qid} check 정답 문서 {x}가 dev 질문 정답에도 있음" for x in c["relevant_doc_ids"] if x in dev_docs]
                errs += [f"{qid} check 대상 인물 {x}가 dev 질문 대상에도 있음" for x in c["expected_person_ids"] if x in dev_ps]
            for lv, u in c["users"].items():
                rel_text = "\n".join(dtext[x] for x in u["relevant_doc_ids"])
                for f in u["required_facts"]:
                    if f["match"] and not any(m in rel_text for m in f["match"]):
                        warns.append(f"{qid} {lv} 사실 {f['fact_id']} '{f['desc']}'의 표기가 정답 문서에 없음 {f['match']}")
                    for r in f.get("pii_refs", []):
                        if not any((r["label"], r["value"]) in ents_by_doc[x] for x in u["relevant_doc_ids"]):
                            errs.append(f"{qid} {lv} 사실 {f['fact_id']} 개인정보 값이 정답 문서 표시에 없음 {r}")
                for f in u["forbidden_facts"]:
                    for r in f.get("pii_refs", []):
                        if not any((r["label"], r["value"]) in e for e in ents_by_doc.values()):
                            errs.append(f"{qid} 금지 사실 {f['fact_id']} 개인정보 값이 문서에 없음 {r}")
            for e in c["expected_query_entities"]:
                if e["text"] not in c["query"]:
                    errs.append(f"{qid} 질문 개인정보 '{e['text']}'가 질문에 없음")

    print(f"문서 {len(docs)}건, 인물 {len(persons)}명, 노출 설계 {len(expo)}행")
    c = collections.Counter(e["label"] for d in docs for e in d["entities"])
    print("표시 개수:", dict(c))
    print(f"오류 {len(errs)}건")
    for e in errs:
        print(" -", e)
    for w in warns:
        print(" ~", w)
    sys.exit(1 if errs else 0)


if __name__ == "__main__":
    main()
