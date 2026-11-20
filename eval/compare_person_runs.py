"""인물사전 설정별 실제 검색 결과 비교: 순위 지표 + 되묻기 + 사용자가 실제로 받는 결과.

starter.run_queries 가 만든 run 파일(설정마다 하나)을 나란히 채점한다. run 파일의 meta 에 검색의
되묻기 여부(ask_person)가 기록되어 있다.

지표 (질의 그룹별: 전체 / 인물 질의 / 인물 없는 질의 / 질의 유형별)
  R@3, R@10   : 정답 문서 중 상위 k 안에 든 비율 (되묻기 여부와 무관한 검색 순위 기준).
                챗봇 답변은 chat.answer() 기본 k=3 이라 R@3 이 실제 답변 근거에 가깝다
  MRR         : 첫 정답 순위의 역수 평균
  Conf@3      : 상위 3개 중 혼동 문서(confusable_doc_ids, 동명이인 등) 비율 (혼동 문서가 있는 질의만)
  되묻기       : 검색이 '동일인 불분명'으로 되묻기를 표시한 질의 비율 (챗봇은 이때 답하지 않는다)
  답변 R@3     : 되묻기 질의는 0 으로 친 R@3 — 사용자가 실제로 답변 근거로 받는 정답 비율
  혼동 답변    : 되묻지 않고 답했는데 상위 3개에 혼동 문서(confusable_doc_ids)가 들어간 질의 수.
                holdout 의 혼동 문서는 '같은 사람의 다른 주제 문서'도 포함하므로 다른 사람 문서 지표가 아니다
  동명이인 반환: (--persons 필요) 되묻지 않고 답했는데 상위 3개에, 질문 인물과 이름이 같은 '다른 사람'만 나오고
                질문 인물은 나오지 않는 문서가 들어간 질의 수 = 다른 사람의 문서가 반환된 질의

사용법 (레포 루트)
    python eval/compare_person_runs.py --gold data/holdout/holdout_retrieval_queries_gold.jsonl \\
        --run 끔=outputs/run_dict_off.jsonl v1=outputs/run_dict_v1.jsonl \\
              v3=outputs/run_person_v3.jsonl --persons data/holdout/holdout_persons_gold.jsonl [--by-type]
"""
import argparse
import collections
import json
import os
import re


def load(path):
    with open(path, encoding="utf-8") as f:
        return [json.loads(l) for l in f if l.strip()]


def has_person(q):
    return any(isinstance(e, dict) and e.get("label") == "person" for e in q.get("query_entities") or [])


def norm_name(t):
    return re.sub(r"[\s.\-]+", "", t)


def load_doc_people(path):
    """인물 정답(persons_gold) → {doc_id: {(정규화 이름, person_id)}}"""
    out = {}
    for d in load(path):
        out[d["id"]] = {(norm_name(m["text"]), m["person_id"]) for m in d["persons"]}
    return out


def other_person_doc(q, doc_people, doc_id):
    """doc 에 질문 인물과 이름이 같은 다른 사람만 있고 질문 인물은 없으면 True."""
    qp = {(norm_name(e["text"]), e["person_id"]) for e in q.get("query_entities") or []
          if isinstance(e, dict) and e.get("label") == "person"}
    if not qp:
        return False
    names, ids = {n for n, _ in qp}, {p for _, p in qp}
    ppl = doc_people.get(doc_id, set())
    return any(n in names and p not in ids for n, p in ppl) and not any(p in ids for _, p in ppl)


def score(gold, run, doc_people=None):
    runs = {r["qid"]: r for r in run}
    groups = collections.defaultdict(lambda: collections.defaultdict(list))
    missing = 0
    for q in gold:
        r = runs.get(q["qid"])
        if r is None:
            missing += 1
            continue
        neutral = set(q.get("neutral_doc_ids", []))
        ranked = [d for d in r.get("ranked_doc_ids", []) if d not in neutral]
        meta = r.get("meta") or {}
        ask = bool(meta.get("ask_person"))
        rel, conf = set(q["relevant_doc_ids"]), set(q.get("confusable_doc_ids", []))
        rr = next((1 / (i + 1) for i, d in enumerate(ranked) if d in rel), 0.0)
        r3 = len(rel & set(ranked[:3])) / len(rel)
        r10 = len(rel & set(ranked[:10])) / len(rel)
        names = ["전체", "인물 질의" if has_person(q) else "인물 없는 질의", "유형:" + q["type"]]
        for g in names:
            m = groups[g]
            m["n"].append(1)
            m["R@3"].append(r3)
            m["R@10"].append(r10)
            m["MRR"].append(rr)
            m["되묻기"].append(1.0 if ask else 0.0)
            m["답변 R@3"].append(0.0 if ask else r3)
            if conf:
                m["Conf@3"].append(len(conf & set(ranked[:3])) / max(len(ranked[:3]), 1))
                m["혼동 답변"].append(1 if (not ask and conf & set(ranked[:3])) else 0)
            if doc_people is not None:
                m["동명이인 반환"].append(1 if (not ask and any(other_person_doc(q, doc_people, d) for d in ranked[:3])) else 0)
    out = {}
    for g, m in groups.items():
        out[g] = {"n": len(m["n"])}
        for key in ("R@3", "R@10", "MRR", "Conf@3", "되묻기", "답변 R@3"):
            out[g][key] = round(sum(m[key]) / len(m[key]), 3) if m[key] else None
        for key in ("혼동 답변", "동명이인 반환"):
            out[g][key] = sum(m[key])
    return out, missing


def main():
    ap = argparse.ArgumentParser(description="인물사전 설정별 실제 검색 결과 비교")
    ap.add_argument("--gold", required=True)
    ap.add_argument("--run", nargs="+", required=True, help="이름=run파일 (여러 개)")
    ap.add_argument("--by-type", action="store_true", help="질의 유형별 표도 출력")
    ap.add_argument("--persons", help="인물 정답 jsonl (동명이인 반환 지표용)")
    ap.add_argument("--out", help="결과 JSON 저장 경로")
    a = ap.parse_args()
    gold = load(a.gold)
    doc_people = load_doc_people(a.persons) if a.persons else None
    results = {}
    for spec in a.run:
        name, path = spec.split("=", 1)
        results[name], missing = score(gold, load(path), doc_people)
        if missing:
            print(f"주의: {name} run 에 없는 질의 {missing}개")
        if not any((r.get("meta") or {}) for r in load(path)):
            print(f"주의: {name} run 에 meta 가 없습니다 (예전 run 파일). 되묻기는 0 으로 표시됩니다.")
    groups = ["전체", "인물 질의", "인물 없는 질의"]
    if a.by_type:
        groups += sorted({g for r in results.values() for g in r if g.startswith("유형:")})
    keys = ["R@3", "R@10", "MRR", "되묻기", "답변 R@3", "혼동 답변", "동명이인 반환"]
    for g in groups:
        n = next((r[g]["n"] for r in results.values() if g in r), 0)
        print(f"\n## {g} (질의 {n}개)")
        print("설정".ljust(10) + "".join(k.rjust(10) for k in keys))
        for name, r in results.items():
            row = r.get(g, {})
            cells = []
            for k in keys:
                v = row.get(k)
                cells.append(("-" if v is None else (f"{v:.3f}" if isinstance(v, float) else str(v))).rjust(10))
            print(name.ljust(10) + "".join(cells))
    if a.out:
        os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
        with open(a.out, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2)
        print(f"\n저장: {a.out}")


if __name__ == "__main__":
    main()
