"""02 영역: 인물 판별 채점 (ITL02). 실제 적재 결과(vault.person_info, 기존 문서 + 통합 테스트 84건)를 정답과 비교한다.

입력: <실행 폴더>/person_rows.json (run.py 가 인물사전 ON 적재 뒤 저장)

두 가지를 나눠 평가한다.
A. 실제 인물 관계 기준 (성능)
   - 언급 연결: 인물 후보 행의 name_spans '전부'를 정답 언급과 겹침으로 맞춘다.
     한 행에 서로 다른 정답 인물의 언급이 섞이면 MIXED(오류), 정답 언급과 안 겹치면 '?'(정답에 없는 이름 탐지).
   - 평가 단위는 정답 이름 언급 하나하나다 (후보 행 단위가 아님). 언급마다 그 언급이 들어간 후보 행의 묶음에 놓는다.
   - 탐지가 놓친 정답 언급은 혼자인 묶음으로 넣어 재현율에 반영한다 (빼지 않는다).
   - 묶음: 확정 연결(reg_id)과 확정+추정 연결(mid_matched)을 따로 계산한다.
   - B-cubed P·R: 통합 테스트 정답 언급 기준. 같은 묶음에 기존 문서 인물·MIXED·'?' 행이 있으면 정밀도에서 틀린 것으로 센다.
   - 기존 문서 인물과의 오병합: 통합 테스트 인물이 기존 문서(홀드아웃) 인물 후보와 같은 묶음이 된 경우.
   - 정답과 다른 쌍(오병합·미연결)은 v3 규칙이 예상한 한계여도 '실패'로 기록한다 (설계서 05 시트 결정).
   - I 시나리오: 인물표(persons.csv scenarios)로 시나리오 ↔ 인물을 잇고, 그 인물에 오병합·미연결·기존 문서 병합·
     이름 미탐·후보 혼합이 하나도 없으면 통과.
     I12: 특정 불가 언급(AMB)이 어떤 인물과도 묶이지 않아야 통과. I20 은 아래 귀속.
   - 문서 내 귀속(I20): 노출 설계표의 (문서, 소유 인물, 값) 토큰이 그 인물 행의 단서(info_label)에 들어갔는지 → 정밀도·재현율
B. v3 규칙 회귀 검사 (규칙이 의도대로 동작하는지)
   - 정답과 다른 쌍이 설계서 예상(simulate_person_rules.EXPECTED_DIFF)과 같은지, 귀속이 'v3 예상 귀속'과 같은지
"""
import collections
import json

from .common import Gold, csv_read, DATA, result_row, set_row, token_of

KIND_LABEL = {"전화": "phone_number", "이메일": "email", "계좌": "account_number", "주민번호": "resident_registration_number",
              "카드": "card_number", "주소": "address"}


def _scen_persons():
    out = collections.defaultdict(set)
    for p in csv_read(DATA / "it_source" / "persons.csv"):
        for s in (p.get("scenarios") or "").replace(" ", "").split(","):
            if s.startswith("I"):
                out[s].add(p["person_id"])
    return out


class UF:
    def __init__(self):
        self.p = {}

    def find(self, x):
        while self.p.setdefault(x, x) != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a, b):
        self.p[self.find(a)] = self.find(b)


def label_rows(rows, mentions):
    """행마다 정답 인물: 정답 언급과 겹치는 name_spans 전부를 보고 정한다.
    → covered: {(문서, 언급 시작): 그 언급을 가진 후보 행 person_id}"""
    by_doc = collections.defaultdict(list)
    for m in mentions:
        by_doc[m["doc_id"]].append(m)
    covered = {}
    for r in rows:
        if not r["doc_id"].startswith("IT"):
            r["_gold"], r["_golds"] = "HO", set()      # 기존 문서(홀드아웃)의 인물 후보
            continue
        golds = set()
        for s, e in ((r.get("evidence") or {}).get("name_spans") or []):
            for m in by_doc.get(r["doc_id"], []):
                if m["start"] < e and s < m["end"]:
                    golds.add(m["person_id"] or "AMB")
                    covered.setdefault((m["doc_id"], m["start"]), r["person_id"])
        r["_golds"] = set(golds)
        r["_gold"] = golds.pop() if len(golds) == 1 else ("MIXED" if golds else "?")
    return covered


def clusters(rows, with_mid):
    uf = UF()
    for r in rows:
        uf.find(r["person_id"])
        if r.get("reg_id"):
            uf.union(r["person_id"], "reg:" + r["reg_id"])
        if with_mid:
            for other in ((r.get("evidence") or {}).get("mid_matched") or {}):
                uf.union(r["person_id"], other)
    return uf


def bcubed(items):
    """items: [(묶음, 정답)] — 정답이 PS/ST 등 실제 인물인 항목 기준. 다른 정답(HO·MIXED·?·AMB)은 정밀도에서 틀린 것."""
    by_c = collections.defaultdict(list)
    for c, gd in items:
        by_c[c].append(gd)
    real = [(c, gd) for c, gd in items if gd not in ("HO", "MIXED", "?", "AMB")]
    if not real:
        return None
    by_g = collections.Counter(gd for _, gd in real)
    P = R = 0.0
    for c, gd in real:
        members = by_c[c]
        same = sum(1 for x in members if x == gd)
        P += same / len(members)
        R += same / by_g[gd]
    return round(P / len(real), 3), round(R / len(real), 3)


def score(run_dir, run_id):
    path = run_dir / "person_rows.json"
    if not path.exists():
        return [], [], {"skipped": "person_rows.json 없음 (인물사전 ON 적재 실행이 없었음)"}
    from simulate_person_rules import EXPECTED_DIFF
    g = Gold()
    rows = json.loads(path.read_text(encoding="utf-8"))
    covered = label_rows(rows, g.mentions)
    split = {d: x["split"] for d, x in g.docs.items()}
    missed = [m for m in g.mentions if (m["doc_id"], m["start"]) not in covered]
    it_rows = [r for r in rows if r["_gold"] != "HO"]

    views = {}
    for name, with_mid in (("확정", False), ("확정+추정", True)):
        uf = clusters(rows, with_mid)
        key = lambda r: uf.find(r["person_id"])
        # 평가 단위 = 정답 이름 언급 하나하나 (후보 행이 아니라). 언급이 들어간 후보 행의 묶음에 놓는다.
        # 놓친 언급은 혼자인 묶음. 같은 묶음의 기존 문서 인물·정답에 없는 이름 후보는 정밀도에서 틀린 것으로 넣는다.
        def items_for(keep_doc):
            items, touched = [], set()
            for m in g.mentions:
                if not keep_doc(m["doc_id"]):
                    continue
                pid = covered.get((m["doc_id"], m["start"]))
                c = uf.find(pid) if pid else f"miss:{m['doc_id']}@{m['start']}"
                items.append((c, m["person_id"] or "AMB"))
                touched.add(c)
            for r in rows:
                if r["_gold"] in ("HO", "?") and key(r) in touched:
                    items.append((key(r), r["_gold"]))
            return items
        res = {"all": bcubed(items_for(lambda d: True))}
        for sp in ("dev", "check"):
            res[sp] = bcubed(items_for(lambda d, sp=sp: split.get(d) == sp))
        ho_c = {key(x) for x in rows if x["_gold"] == "HO"}
        ho_merge = sorted({r["_gold"] for r in it_rows if r["_gold"] not in ("MIXED", "?", "AMB") and key(r) in ho_c})
        # 정답과 다른 쌍 (통합 테스트 인물끼리)
        real = [r for r in it_rows if r["_gold"] not in ("MIXED", "?")]
        diff = set()
        for i, x in enumerate(real):
            for y in real[i + 1:]:
                if "AMB" in (x["_gold"], y["_gold"]):
                    if key(x) == key(y) and x["_gold"] != y["_gold"]:
                        diff.add(tuple(sorted((x["_gold"], y["_gold"]))) + ("오병합",))
                    continue
                if (x["_gold"] == y["_gold"]) != (key(x) == key(y)):
                    a, b = sorted((x["_gold"], y["_gold"]))
                    diff.add((a, b, "오병합" if key(x) == key(y) else "미연결"))
        views[name] = {"bcubed": res, "holdout_merge": ho_merge, "diff": diff}

    # 문서 내 귀속 (I20)
    att, v3_diff = collections.Counter(), []
    own_tokens = collections.defaultdict(set)
    for r in it_rows:
        own_tokens[(r["doc_id"], r["_gold"])].update(r.get("info_label") or [])
    expected_tokens = collections.defaultdict(set)
    for x in g.exposure:
        lab = KIND_LABEL.get(x["pii_kind"])
        if not lab or "*" in x["value"]:
            continue
        try:
            tok = token_of(lab, x["value"].strip("()"))
        except ValueError:
            continue
        expected_tokens[(x["doc_id"], x["person_id"])].add(tok)
        got = tok in own_tokens[(x["doc_id"], x["person_id"])]
        att["gold"] += 1
        att["hit"] += got
        exp = x["v3_expected_attribution"].replace(" (작성 문서로 확인)", "")
        if exp in ("귀속됨", "귀속 안 됨") and (exp == "귀속됨") != got:
            v3_diff.append(f"{x['doc_id']} {x['person_id']} {x['pii_kind']}: 예상 {exp} / 결과 {'귀속됨' if got else '귀속 안 됨'}")
    wrong = sum(len(toks - expected_tokens[k]) for k, toks in own_tokens.items() if k[1] not in ("AMB", "?", "MIXED"))
    att_p = att["hit"] / (att["hit"] + wrong) if att["hit"] + wrong else 0.0
    att_r = att["hit"] / att["gold"] if att["gold"] else 0.0

    main = views["확정+추정"]
    diff = main["diff"]
    mixed = [r["person_id"] for r in it_rows if r["_gold"] == "MIXED"]
    spurious = [r["person_id"] for r in it_rows if r["_gold"] == "?"]
    summary = {"rows": len(rows), "it_rows": len(it_rows), "holdout_rows": len(rows) - len(it_rows),
               "missed_mentions": [f"{m['doc_id']}@{m['start']}" for m in missed],
               "mixed_rows": len(mixed), "unlabeled_rows": len(spurious),
               "bcubed": {k: v["bcubed"] for k, v in views.items()},
               "holdout_merge": {k: v["holdout_merge"] for k, v in views.items()},
               "diff_pairs": sorted(diff), "v3_unexpected_diff": sorted(diff - EXPECTED_DIFF),
               "v3_expected_but_absent": sorted(EXPECTED_DIFF - diff),
               "attribution": {"precision": round(att_p, 3), "recall": round(att_r, 3), "v3_diff": v3_diff}}

    out, sets = [], []
    unit = dict(unit="적재", run_id="ITL02", diff="", cfg="R1·R3 적재")
    for name, v in views.items():
        for sp in ("all", "dev", "check"):
            b = v["bcubed"][sp]
            if b:
                spn = "전체" if sp == "all" else sp
                sets += [set_row(f"문서 간 판별 ({name} 연결)", spn, "R1·R3 적재", "I01", "B-cubed P", b[0], "", "기준선 기록"),
                         set_row(f"문서 간 판별 ({name} 연결)", spn, "R1·R3 적재", "I01", "B-cubed R", b[1], "", "기준선 기록")]
        sets.append(set_row(f"기존 문서 인물과 오병합 ({name} 연결)", "전체", "R1·R3 적재", "I01", "인물 수",
                            len(v["holdout_merge"]), "0", "통과" if not v["holdout_merge"] else "실패"))
    sets += [set_row("문서 내 귀속", "전체", "R1·R3 적재", "I20", "귀속 정밀도", att_p, "", "기준선 기록"),
             set_row("문서 내 귀속", "전체", "R1·R3 적재", "I20", "귀속 재현율", att_r, "", "기준선 기록")]

    # A. 실제 관계: 다른 쌍은 모두 실패 (v3 예상 여부는 비고)
    for a, b, kind in sorted(diff):
        exp = (a, b, kind) in EXPECTED_DIFF
        out.append(result_row(**unit, split="", item_id="I01", content=f"{a}·{b} {kind} (실제 관계 기준)",
                              expected="정답: " + ("다른 사람" if kind == "오병합" else "같은 사람"), actual=f"v3: {kind}",
                              result="실패", stage="인물 판별",
                              note="v3 규칙이 예상한 한계" if exp else "v3 예상에도 없는 차이"))
    for name, v in views.items():
        if v["holdout_merge"]:
            out.append(result_row(**unit, split="", item_id="I01", content=f"기존 문서 인물과 오병합 ({name} 연결)",
                                  expected="0명", actual=f"{len(v['holdout_merge'])}명: {', '.join(v['holdout_merge'][:8])}",
                                  result="실패", stage="인물 판별"))
    if mixed:
        out.append(result_row(**unit, split="", item_id="I19", content="한 후보 행에 다른 인물 언급이 섞임",
                              expected="0행", actual=f"{len(mixed)}행", result="실패", stage="인물 판별"))
    # I 시나리오별
    sp_of = _scen_persons()
    bad_persons = collections.defaultdict(list)
    for a, b, kind in diff:
        for p in {a, b}:
            bad_persons[p].append(f"{a}·{b} {kind}")
    for name, v in views.items():
        for p in v["holdout_merge"]:
            bad_persons[p].append(f"기존 문서 인물과 병합({name})")
    for m in missed:                                   # 이름을 못 찾은 언급도 그 인물의 시나리오 실패
        if m["person_id"]:
            bad_persons[m["person_id"]].append(f"이름 미탐 {m['doc_id']}@{m['start']}")
    for r in it_rows:                                  # 한 후보에 여러 인물 언급이 섞임
        if r["_gold"] == "MIXED":
            for p in r["_golds"] - {"AMB"}:
                bad_persons[p].append(f"후보 혼합 {r['doc_id']}({'·'.join(sorted(r['_golds']))})")
    for sid in sorted(g.scenarios):
        if not sid.startswith("I") or sid == "I20":
            continue
        persons = sp_of.get(sid, set())
        if sid == "I12":
            amb_bad = [d for d in diff if "AMB" in d[:2]]
            out.append(result_row(**unit, split="", item_id="I12", content="특정 불가 언급은 어느 인물에도 연결 안 함",
                                  expected="AMB 묶임 0건", actual=f"{len(amb_bad)}건", result="통과" if not amb_bad else "실패",
                                  stage="인물 판별"))
            continue
        if not persons:
            out.append(result_row(**unit, split="", item_id=sid, content=g.scenarios[sid]["title"], expected="",
                                  actual="인물표에 연결된 인물 없음", result="채점 미구현"))
            continue
        errs = sorted({e for p in persons for e in bad_persons.get(p, [])})
        out.append(result_row(**unit, split="", item_id=sid, content=g.scenarios[sid]["title"],
                              expected=g.scenarios[sid]["expected"][:60], actual=("; ".join(errs[:4]) or "오류 쌍 없음"),
                              result="통과" if not errs else "실패", stage="인물 판별",
                              note=f"인물 {','.join(sorted(persons))}"))
    # B. v3 규칙 회귀
    unexpected, absent = sorted(diff - EXPECTED_DIFF), sorted(EXPECTED_DIFF - diff)
    out.append(result_row(**unit, split="", item_id="I01", content="v3 규칙 회귀: 정답과 다른 쌍 = 설계서 예상",
                          expected=f"예상 {len(EXPECTED_DIFF)}쌍과 같음",
                          actual=f"예상에 없던 차이 {len(unexpected)}, 예상했는데 없음 {len(absent)}",
                          result="통과" if not unexpected and not absent else "실패", stage="인물 판별",
                          note="; ".join(" ".join(x) for x in (unexpected + absent)[:6])))
    out.append(result_row(**unit, split="", item_id="I20", content="v3 규칙 회귀: 문서 내 귀속 = v3 예상", expected="차이 0건",
                          actual=f"{len(v3_diff)}건", result="통과" if not v3_diff else "실패", stage="문서 내 귀속",
                          note="; ".join(v3_diff[:5])))
    out.append(result_row(**unit, split="", item_id="I20", content="문서 내 귀속 (실제 소유 인물 기준)", expected="기록",
                          actual=f"정밀도 {att_p:.3f}, 재현율 {att_r:.3f}", result="기준선 기록"))
    if missed:
        out.append(result_row(**unit, split="", item_id="P01", content="이름 미탐으로 인물 후보가 안 생긴 언급",
                              expected="0건", actual=f"{len(missed)}건 (재현율에 반영)", result="기준선 기록",
                              note=", ".join(summary["missed_mentions"][:10])))
    return out, sets, summary
