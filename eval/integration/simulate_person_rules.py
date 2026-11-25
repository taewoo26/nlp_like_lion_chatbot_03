"""인물사전 v3 판정 시뮬레이션: 문서 원본의 정답 표시를 탐지 결과 대신 넣어 판정·귀속을 돌린다.

    python eval/integration/simulate_person_rules.py --root data/integration_v1 --repo .

- 저장소의 person_candidates · person_resolve · pii_pipeline 을 읽기만 한다 (DB·모델 없음, PERSON_RULES=v3).
- 탐지는 정답 span 으로 대신한다. 'hard' 표시(탐지 어려운 표기)와 탐지 라벨 밖 값(여권번호·고객번호·사번)은 미탐지로 둔다.
- 결과를 설계서 '인물 판별 정답과 v3 예상'과 비교한다.
    · 규칙 격리 문서: 정답과 다른 쌍이 EXPECTED_DIFF 와 같아야 하고, 귀속은 노출 설계표의 v3 예상 귀속과 같아야 함 (다르면 오류)
    · 현실 작성 문서: 결과를 기록만 함 (설계서의 '작성 후 확인' 칸에 옮겨 적음)
출력: reports/simulate_person_rules.json, reports/attribution.csv
"""
import argparse
import collections
import csv
import itertools
import json
import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from build_data import load_csv, load_docs  # noqa: E402

DETECT = ("person", "phone_number", "email", "address", "resident_registration_number",
          "account_number", "card_number", "driver_license")
# 설계서 03 시트 4번 표: 정답과 다른 쌍 (인물 기준). 이 밖의 차이는 문서 작성 오류로 본다.
EXPECTED_DIFF = {
    ("PS03", "PS04", "오병합"),                    # C07 M7 오병합
    ("PS26", "PS26", "미연결"),                    # C11 '하늘' 미연결
    ("PS29", "PS29", "미연결"),                    # C15 복수 역할 N4
    ("PS31", "PS31", "미연결"),                    # C17 역할 변경 N4
    ("PS32", "PS32", "미연결"), ("PS33", "PS33", "미연결"),   # C18
    ("PS38", "PS38", "미연결"),                    # C21 영문 성명 (현실 작성)
}
REALISTIC_ONLY = {("PS38", "PS38", "미연결")}      # 현실 작성 문서에서 나오는 차이 (기록만)


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
    ap.add_argument("--repo", required=True)
    a = ap.parse_args()
    os.environ.setdefault("HMAC_KEY", "simulate-only-key")
    os.environ["PERSON_RULES"] = "v3"
    sys.path.insert(0, a.repo)
    from starter import pii_pipeline
    from starter.person_candidates import build_doc_candidates
    from starter.person_resolve import PersonResolver, mid_matched
    from starter.roles import default_rules

    root = pathlib.Path(a.root)
    docs = load_docs(root)
    expo = load_csv(root / "it_source" / "exposure.csv")
    track = {d["id"]: d["meta"]["track"] for d in docs}
    split = {d["id"]: d["meta"]["split"] for d in docs}
    rules = default_rules()

    rows, links, token_info, tokens_by_doc, chunks_by_doc = [], [], {}, {}, {}
    counter = itertools.count(1)
    for d in docs:
        ents = [e for e in d["entities"] if e["label"] in DETECT and not e["flag"]]
        spans = [{"label": e["label"], "start": e["start"], "end": e["end"]} for e in ents]
        masked, records = pii_pipeline.pseudonymize(d["text"], spans)
        tokens_by_doc[d["id"]] = {(r["start"], r["end"]): r["token"] for r in records}
        for r in records:
            token_info[r["token"]] = {"label": r["label"], "normalized": r["normalized"]}
        parts = chunk(masked)
        chunks_by_doc[d["id"]] = len(parts)
        gold = {(e["start"]): e["owner"] for e in d["entities"] if e["label"] == "person"}

        def idf(prefix, _d=d["id"]):
            return f"{prefix}{_d}_{next(counter)}"
        rr, ll = build_doc_candidates(d["id"], d["text"], masked, records, parts, rules, id_factory=idf)
        for r in rr:
            s0 = r["evidence"]["name_spans"][0]
            r["_gold"] = gold.get(s0[0], "?")
            r["_name"] = d["text"][s0[0]:s0[1]]
        rows += rr
        links += ll
    res = PersonResolver(rows, [], token_info=token_info, role_group=rules.role_group, links=links,
                         version="v3", id_factory=lambda p: f"{p}{next(counter):04d}").resolve()

    parent = {}
    def find(x):
        while parent.setdefault(x, x) != x:
            x = parent[x]
        return x
    for r in rows:
        if r["reg_id"]:
            parent[find(r["person_id"])] = find("reg:" + r["reg_id"])
        for p in mid_matched(r):
            parent[find(r["person_id"])] = find(p)
    key = lambda r: find(r["person_id"])

    rs = [r for r in rows if not r["_gold"].startswith("AMB")]
    diff = set()
    for i, x in enumerate(rs):
        for y in rs[i + 1:]:
            same_gold, same_pred = x["_gold"] == y["_gold"], key(x) == key(y)
            if same_gold != same_pred:
                g = tuple(sorted((x["_gold"], y["_gold"])))
                diff.add((g[0], g[1], "오병합" if same_pred else "미연결"))

    def bcubed(sub):
        if not sub:
            return None
        P = R = 0.0
        for r in sub:
            sp = [x for x in sub if key(x) == key(r)]
            sg = [x for x in sub if x["_gold"] == r["_gold"]]
            P += sum(x["_gold"] == r["_gold"] for x in sp) / len(sp)
            R += sum(key(x) == key(r) for x in sg) / len(sg)
        return round(P / len(sub), 3), round(R / len(sub), 3)

    # 귀속: 노출 설계표 행마다 그 인물 행의 단서에 토큰이 들어갔는가
    attr_rows, attr_err = [], []
    for x in expo:
        if x["pii_kind"] in ("없음", ""):
            continue
        d = next(dd for dd in docs if dd["id"] == x["doc_id"])
        val = x["value"].strip("()")
        ent = next((e for e in d["entities"] if e["text"] == val and x["person_id"] in (e["owner"] or "").split("+")), None)
        tok = tokens_by_doc[d["id"]].get((ent["start"], ent["end"])) if ent else None
        if tok is None:
            got = "해당 없음 (토큰 아님)"
        else:
            mine = any(tok in r["info_label"] for r in rows if r["doc_id"] == d["id"] and r["_gold"] == x["person_id"])
            other = sorted({r["_gold"] for r in rows if r["doc_id"] == d["id"] and r["_gold"] != x["person_id"]
                            and tok in r["info_label"]})
            got = "귀속됨" if mine else (f"다른 인물({','.join(other)})에 귀속" if other else "귀속 안 됨")
        exp = x["v3_expected_attribution"]
        if exp.endswith(" (작성 문서로 확인)"):           # v4.1부터: 현실 작성 문서도 기록된 결과를 회귀 기준으로 비교
            exp = exp[: -len(" (작성 문서로 확인)")]
        if exp.startswith("작성 후 확인"):
            status = "기록"
        else:
            status = "일치" if got == exp else "불일치"
            if status == "불일치":
                attr_err.append(f"{x['doc_id']} {x['person_id']} {x['pii_kind']} {val}: 예상 '{exp}' / 결과 '{got}'")
        attr_rows.append({"doc_id": x["doc_id"], "person_id": x["person_id"], "pii_kind": x["pii_kind"], "value": val,
                          "track": track[x["doc_id"]], "expected": exp, "result": got, "status": status})

    groups = collections.defaultdict(list)
    for r in rows:
        groups[key(r)].append(r)
    clusters = []
    for k, g in groups.items():
        clusters.append({"gold": sorted({r["_gold"] for r in g}),
                         "rows": [f'{r["doc_id"]}:{r["_name"]}:{r["role"]}:{r["evidence"].get("rule") or "-"}' for r in g]})
    cand = collections.defaultdict(set)
    nm = {r["token"]: r["_name"] for r in rows}
    for l in res["look_up"]:
        cand[nm.get(l["token"], "?")].add(l["reg_id"] or l["person_id"])

    unexpected = sorted(diff - EXPECTED_DIFF)
    missing = sorted(EXPECTED_DIFF - diff)
    report = {
        "persons_rows": len(rows),
        "bcubed_all": bcubed(rs),
        "bcubed_dev": bcubed([r for r in rs if split[r["doc_id"]] == "dev"]),
        "bcubed_check": bcubed([r for r in rs if split[r["doc_id"]] == "check"]),
        "diff_pairs": sorted(diff), "unexpected_diff": unexpected, "expected_but_absent": missing,
        "attribution_mismatch": attr_err,
        "attribution_counts": dict(collections.Counter(r["result"] for r in attr_rows)),
        "name_candidates_gt1": {k: len(v) for k, v in cand.items() if len(v) > 1},
        "chunks": {k: v for k, v in chunks_by_doc.items() if v > 1},
        "clusters": clusters,
    }
    out = root / "reports"
    out.mkdir(exist_ok=True)
    (out / "simulate_person_rules.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    with open(out / "attribution.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(attr_rows[0]))
        w.writeheader()
        w.writerows(attr_rows)

    print("B-cubed 전체/dev/check:", report["bcubed_all"], report["bcubed_dev"], report["bcubed_check"])
    print("정답과 다른 쌍:", sorted(diff))
    print("예상 밖 차이:", unexpected)
    print("예상했으나 없는 차이:", missing)
    print("귀속 결과:", report["attribution_counts"])
    print("귀속 불일치 (설계표 기준):", len(attr_err))
    for e in attr_err:
        print(" -", e)
    print("이름 질문 후보 2개 이상:", report["name_candidates_gt1"])
    print("2청크 이상 문서:", report["chunks"])
    realistic = [r for r in attr_rows if r["status"] == "기록"]
    print("현실 작성 문서 귀속 결과:")
    for r in realistic:
        print(f"   {r['doc_id']} {r['person_id']} {r['pii_kind']} {r['value']} → {r['result']}")
    bad = [u for u in unexpected if u not in REALISTIC_ONLY] or ([m for m in missing if m not in REALISTIC_ONLY])
    sys.exit(1 if bad or attr_err else 0)


if __name__ == "__main__":
    main()
