"""질문·정답지 파생 파일 (build_data.py 가 불러 쓴다).

원본: it_source/questions.yaml, question_groups.csv (엑셀 질문 그룹 표), scenarios.csv (엑셀 시나리오 표),
      access_cases.yaml, dialogs.yaml
파생:
  it_queries.jsonl / it_queries_dev.jsonl / it_queries_check.jsonl   {qid, query}            → starter.run_queries --queries
  it_retrieval_gold.jsonl   정답 문서가 있는 질문만 {qid, query, type, relevant_doc_ids, ...} → eval/eval_holdout_retrieval.py --gold
  it_cases.jsonl            질문별 종합 정답지 (설정·사용자·상태·사실·시나리오 판정)
  it_access_cases.jsonl, it_dialogs.jsonl
qid 는 questions.yaml 순서대로 ITQ001부터 붙인다 (dev 그룹 다음 check 그룹).
"""
import csv
import json
import re

import yaml

PLAN_TO_JUDGE = {"실행 대상": "run", "기준선 측정": "baseline", "판정 보류": "hold", "실행 대기": "wait"}
STATES = {"answered", "clarify", "no_result", "withheld", "search_only", "error"}
DEFAULT_OUTPUT = {"Lv0": "token", "Lv1": "token", "Lv2": "token"}   # chat(restore=false)의 허용 출력
LIST_KEYS = ("p", "rel", "conf", "neutral", "types", "kw", "rm", "cons", "facts", "forbid", "sens")


def _csv(path):
    with open(path, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _split(s):
    return [x.strip() for x in re.split(r"[·,]", s or "") if x.strip()]


def _fact(f):
    out = {"fact_id": f["id"], "desc": f.get("d", ""), "match": f.get("m", [])}
    if "pii" in f:
        out["pii_refs"] = [{"label": f["pii"][0], "value": f["pii"][1]}]
    if "raw" in f:
        out["raw"] = f["raw"]
    return out


def _check(sid, judge, scen, configs):
    """시나리오 판정. judge 가 {설정: 판정} 이면 그 설정만 덮어쓴다 (나머지는 시나리오 표의 테스트 구분)."""
    base = PLAN_TO_JUDGE[scen[sid]["plan"]] if sid in scen else "run"
    by_cfg = {c: (judge.get(c, base) if isinstance(judge, dict) else judge) for c in configs}
    return {"check_id": sid, "expected": scen[sid]["expected"] if sid in scen else "04 시트 공통 평가 항목",
            "configs": configs, "judgement": by_cfg}


def _states(spec, configs):
    """state 표기 → {설정: [허용 상태]}"""
    def norm(v):
        vs = v if isinstance(v, list) else [v]
        bad = set(vs) - STATES
        if bad:
            raise ValueError(f"알 수 없는 상태 {bad}")
        return vs
    if spec is None:
        return {}
    if isinstance(spec, dict):
        default = spec.get("*")
        out = {}
        for c in configs:
            v = spec.get(c, default)
            if v is None:
                raise ValueError(f"설정 {c}의 상태가 없음: {spec}")
            out[c] = norm(v)
        return out
    return {c: norm(spec) for c in configs}


class EntityFinder:
    """질문에서 이름·개인정보 값을 찾는다 (인물 마스터 이름 + 문서에 표시된 값)."""

    def __init__(self, docs, persons):
        self.names = {}                                   # 표기 → {인물 ID}
        for pid, p in persons.items():
            if p["group"] != "미확정 언급":
                self.names.setdefault(p["name"], set()).add(pid)
        for d in docs:
            for e in d["entities"]:
                if e["label"] == "person":
                    self.names.setdefault(e["text"], set()).add(e["owner"])
        self.values = []                                  # (정규식, 라벨, 원문 값, 소유)
        seen = set()
        for d in docs:
            for e in d["entities"]:
                if e["label"] == "person" or e["flag"] or (e["text"], e["label"]) in seen:
                    continue
                seen.add((e["text"], e["label"]))
                ds = re.sub(r"\D", "", e["text"])
                if e["label"] in ("email", "address", "passport_number", "customer_id", "employee_id") or len(ds) < 7:
                    rx = re.compile(re.escape(e["text"]))
                else:
                    rx = re.compile(r"(?<!\d)" + r"[-. ]?".join(ds) + r"(?!\d)")
                self.values.append((rx, e["label"], e["text"], e["owner"]))

    def find(self, query, expected):
        found, taken = [], []
        for name in sorted(self.names, key=len, reverse=True):
            for m in re.finditer(re.escape(name), query):
                if any(s < m.end() and m.start() < t for s, t in taken):
                    continue
                cands = sorted(self.names[name])
                hit = sorted(set(cands) & set(expected)) or cands
                found.append({"text": name, "label": "person", "person_id": hit[0] if len(hit) == 1 else hit,
                              "start": m.start()})
                taken.append((m.start(), m.end()))
        for rx, label, value, owner in self.values:
            for m in rx.finditer(query):
                if any(s < m.end() and m.start() < t for s, t in taken):
                    continue
                own = None if owner in (None, "-") else (owner.split("+") if "+" in owner else owner)
                found.append({"text": m.group(), "label": label, "value": value, "person_id": own, "start": m.start()})
                taken.append((m.start(), m.end()))
        return [{k: v for k, v in f.items() if k != "start"} for f in sorted(found, key=lambda f: f["start"])]


def build(root, docs, persons):
    src = root / "it_source"
    groups = {g["group_id"]: g for g in _csv(src / "question_groups.csv")}
    scen = {s["scenario_id"]: s for s in _csv(src / "scenarios.csv")}
    spec = yaml.safe_load((src / "questions.yaml").read_text(encoding="utf-8"))
    users = spec["users"]
    doc_ids = {d["id"] for d in docs}
    finder = EntityFinder(docs, persons)

    missing = set(groups) - set(spec["groups"])
    extra = set(spec["groups"]) - set(groups)
    if missing or extra:
        raise ValueError(f"질문 그룹 불일치: yaml에 없음 {sorted(missing)}, 엑셀에 없음 {sorted(extra)}")

    cases, n = [], 0
    for gid, g in spec["groups"].items():
        meta = groups[gid]
        qs = g["questions"]
        if len(qs) != int(meta["n_questions"]):
            raise ValueError(f"{gid} 질문 수 {len(qs)} ≠ 엑셀 {meta['n_questions']}")
        search_cfg = _split(meta["search_configs"]) if "+" not in meta["search_configs"] else [meta["search_configs"]]
        answer_cfg = _split(meta["answer_configs"]) if "+" not in meta["answer_configs"] else [meta["answer_configs"]]
        levels = _split(meta["users"])
        g_scen = _split(meta["scenarios"])
        for q in qs:
            n += 1
            item = dict(g.get("defaults", {}))
            item.update(q)
            for k in ("rel", "conf", "neutral"):
                bad = set(item.get(k, [])) - doc_ids
                if bad:
                    raise ValueError(f"{gid} {q['q']}: 없는 문서 {bad}")
            p = item.get("p", [])
            ents = item["ents"] if "ents" in item else finder.find(item["q"], p)
            ents = [{**e, "person_id": e.get("person_id", e.get("person"))} for e in ents]
            for e in ents:
                e.pop("person", None)
            judge = {s: (item.get("judge") or {}).get(s, {}) for s in g_scen}
            stray = set(item.get("judge") or {}) - set(g_scen)
            if stray:
                raise ValueError(f"{gid} {item['q']}: 그룹 시나리오에 없는 판정 덮어쓰기 {stray}")
            base = {"state": _states(item.get("state"), answer_cfg), "rel": item.get("rel", []),
                    "facts": [_fact(f) for f in item.get("facts", [])], "forbid": [_fact(f) for f in item.get("forbid", [])]}
            per_user = {}
            for lv in levels:
                o = (item.get("by_user") or {}).get(lv, {})
                facts = [f for f in base["facts"] if f["fact_id"] not in o.get("drop_facts", [])]
                per_user[lv] = {
                    "user_id": users[lv],
                    "expected_state": _states(o["state"], answer_cfg) if "state" in o else base["state"],
                    "relevant_doc_ids": o.get("rel", base["rel"]),
                    "required_facts": facts if "state" not in o or "answered" in str(o["state"]) else [],
                    "forbidden_facts": base["forbid"] + [_fact(f) for f in o.get("forbid_add", [])],
                }
            cases.append({
                "qid": f"ITQ{n:03d}", "group_id": gid, "split": meta["split"], "difficulty": meta["difficulty"],
                "intent": meta["intent"], "query": item["q"], "variant_type": item.get("v", "기본"),
                "search_configs": search_cfg, "answer_configs": answer_cfg, "users": per_user,
                "expected_query_entities": ents, "required_keywords": item.get("kw", []),
                "removable_terms": item.get("rm", []), "expected_constraints": item.get("cons", []),
                "expected_person_ids": p, "expected_role": item.get("role"),
                "relevant_doc_ids": base["rel"], "confusable_doc_ids": item.get("conf", []),
                "neutral_doc_ids": item.get("neutral", []), "expected_doc_types": item.get("types", []),
                "required_facts": base["facts"], "forbidden_facts": base["forbid"],
                "sensitive_query_strings": item.get("sens", []),
                "scenario_ids": g_scen,
                "checks": [_check(s, judge[s], scen, answer_cfg if s[0] in "AQGX" else search_cfg) for s in g_scen],
                "allowed_output": DEFAULT_OUTPUT, "note": item.get("note"),
            })

    def jl(name, rows):
        with open(root / name, "w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        return rows

    out = {}
    out["it_queries.jsonl"] = jl("it_queries.jsonl", [{"qid": c["qid"], "query": c["query"]} for c in cases])
    for sp in ("dev", "check"):
        out[f"it_queries_{sp}.jsonl"] = jl(f"it_queries_{sp}.jsonl",
                                          [{"qid": c["qid"], "query": c["query"]} for c in cases if c["split"] == sp])
    out["it_retrieval_gold.jsonl"] = jl("it_retrieval_gold.jsonl", [
        {"qid": c["qid"], "query": c["query"], "type": c["variant_type"], "group_id": c["group_id"], "split": c["split"],
         "difficulty": c["difficulty"], "relevant_doc_ids": c["relevant_doc_ids"],
         "confusable_doc_ids": c["confusable_doc_ids"], "neutral_doc_ids": c["neutral_doc_ids"]}
        for c in cases if c["relevant_doc_ids"]])
    out["it_cases.jsonl"] = jl("it_cases.jsonl", cases)

    acc = yaml.safe_load((src / "access_cases.yaml").read_text(encoding="utf-8"))
    rows = []
    for c in acc["cases"]:
        c = dict(c)
        c["user_ids"] = [acc["users"][u] for u in c["users"]]
        for st in c["steps"]:
            q = (st.get("args") or {}).get("query")
            if q in acc["queries"]:
                st["args"]["query"] = acc["queries"][q]
        rows.append(c)
    if len(rows) != 22:
        raise ValueError(f"권한 케이스 {len(rows)}개 (22개여야 함)")
    out["it_access_cases.jsonl"] = jl("it_access_cases.jsonl", rows)
    dia = yaml.safe_load((src / "dialogs.yaml").read_text(encoding="utf-8"))
    out["it_dialogs.jsonl"] = jl("it_dialogs.jsonl", [{**d, "user_id": users[dia["user"]]} for d in dia["dialogs"]])
    return out
