"""인물사전(person_candidates → PersonResolver) 오프라인 평가: 문서 간 같은 사람이 이어지는가?

DB·임베딩 없이 적재와 같은 순서(가명처리 → 청크 → 문서별 후보 → 판정)를 실행하고,
정답 인물 ID(gold)로 채점한다. 정답 ID는 채점에만 쓰고 판정 입력으로 쓰지 않는다.

입력 span
  기본      : 라벨 jsonl 의 entities 위치를 탐지 결과 대신 넣는다 (탐지 오류를 빼고 규칙만 본다)
  --detector: pii_pipeline.detect() 로 실제 탐지 (모델 필요, 느림)

예측 인물 ID (두 가지 기준으로 함께 채점)
  확정 기준 : reg_id, 없으면 person_id. 검색(search.person_candidates)이 사람 수를 세는 방식과 같다.
  추정 포함 : 확정 reg_id + 추정 연결(evidence.mid_matched)로 이어진 행을 한 사람으로 본다.

지표
  B3 P / R / F1 : 언급 단위 B-cubed. P=섞이지 않음, R=쪼개지지 않음
  여러 문서 인물 : 정답상 2개 이상 문서에 나오는 사람 수, 그중 언급이 모두 한 ID로 묶인 사람 수(하나로),
                   그중 그 묶음에 다른 사람도 없는 사람 수(완전 정답). 인물별 상태: 정답/섞임만/쪼개짐만/쪼개짐+섞임
  섞인 묶음      : 2개 이상 문서에 걸친 예측 묶음 중 정답 인물이 2명 이상 섞인 묶음 수
  되묻기         : 이름 토큰별 예측 인물 수 > 1 인 토큰. 그중 정답 인물이 1명뿐인 토큰은 불필요한 되묻기

사용법 (레포 루트, .env 의 HMAC_KEY 필요)
    python eval/eval_person_dict.py --data data/holdout/holdout_labeled.jsonl \\
        --gold data/holdout/holdout_persons_gold.jsonl
    python eval/eval_person_dict.py --data data/test/pii_synth_finance_test.jsonl   # gold 생략: data 의 entities.person_id 사용
    python eval/eval_person_dict.py --data ... --rules v3        # 판정 규칙 (기본 v1)
    옵션: --rules v1|v3, --detector, --shuffle SEED(문서 순서 섞기), --out <json>, --show N (문제 사례 출력)
"""
import argparse
import collections
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def load(path):
    with open(path, encoding="utf-8") as f:
        return [json.loads(l) for l in f if l.strip()]


def gold_mentions(docs, gold_path=None):
    """{(doc_id, start, end): 정답 인물 ID}. gold 파일이 없으면 data 의 entities.person_id 를 쓴다."""
    out = {}
    if gold_path:
        for g in load(gold_path):
            for m in g["persons"]:
                out[(g["id"], m["start"], m["end"])] = m["person_id"]
    else:
        for d in docs:
            for e in d["entities"]:
                if e["label"] == "person" and e.get("person_id"):
                    out[(d["id"], e["start"], e["end"])] = e["person_id"]
    if not out:
        raise SystemExit("정답 인물 ID가 없습니다. --gold 를 주거나 entities.person_id 가 있는 데이터를 쓰세요.")
    return out


def run_pipeline(docs, max_chars, use_detector, link_unique=False, link_given=False, version=None):
    """적재(person_ingest.run)와 같은 순서로 후보를 만들고 판정한다. DB 쓰기 없음."""
    os.environ["NAME_LINK"] = "off"                     # person_ingest.run 과 같은 조건
    from starter import pii_pipeline
    from starter.ingest import chunk
    from starter.person_candidates import build_doc_candidates
    from starter.person_resolve import PersonResolver, check_rows
    from starter.roles import default_rules

    rules = default_rules()
    rows, links, mapping = [], [], {}
    for d in docs:
        if use_detector:
            spans = pii_pipeline.detect(d["text"])
        else:
            spans = [{"label": e["label"], "start": e["start"], "end": e["end"]} for e in d["entities"]]
        masked, records = pii_pipeline.pseudonymize(d["text"], spans)
        parts = chunk(masked, max_chars)
        doc_rows, doc_links = build_doc_candidates(d["id"], d["text"], masked, records, parts, rules)
        rows.extend(doc_rows)
        links.extend(doc_links)
        for r in records:
            mapping[r["token"]] = {"label": r["label"], "normalized": r["normalized"]}
    # 적재(person_ingest.assign_created_times)처럼 행마다 서로 다른 시각을 순서대로 준다.
    # 같은 초에 만든 행은 시각이 같아 판정 순서가 임의 id 로 정해지므로, 적재와 같은 결정적 순서로 맞춘다.
    from datetime import datetime, timedelta, timezone
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    for i, r in enumerate(rows):
        r["created_at"] = base + timedelta(microseconds=i)
    resolver = PersonResolver(rows, [], token_info=mapping, role_group=rules.role_group, links=links,
                              link_unique=link_unique, link_given=link_given, version=version)
    result = resolver.resolve()
    check_rows(result["rows"], result["registry"], token_info=mapping, mid_reg=resolver.mid_reg)
    return result, mapping


class _UF:
    def __init__(self):
        self.p = {}

    def find(self, x):
        self.p.setdefault(x, x)
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a, b):
        self.p[self.find(a)] = self.find(b)


def identities(rows):
    """행 → (확정 기준 ID, 추정 포함 ID)."""
    uf = _UF()
    for r in rows:
        uf.find(r["person_id"])
        if r["reg_id"]:
            uf.union(r["person_id"], "reg:" + r["reg_id"])
        for peer in (r["evidence"].get("mid_matched") or {}):
            uf.union(r["person_id"], peer)
    strict = {r["person_id"]: r["reg_id"] or r["person_id"] for r in rows}
    loose = {r["person_id"]: uf.find(r["person_id"]) for r in rows}
    return strict, loose


def mention_rows(rows):
    """{(doc_id, start, end): 행} — 행의 name_spans 로 언급 위치를 찾는다."""
    out = {}
    for r in rows:
        for s, e in r["evidence"].get("name_spans", []):
            out[(r["doc_id"], s, e)] = r
    return out


def score(gold, pred):
    """pred: {언급: 예측 ID}. 예측이 없는 언급(미탐·행 없음)은 혼자인 묶음으로 본다."""
    keys = list(gold)
    p_of = {k: pred.get(k, ("missing",) + k) for k in keys}
    by_pred, by_gold = collections.defaultdict(set), collections.defaultdict(set)
    for k in keys:
        by_pred[p_of[k]].add(k)
        by_gold[gold[k]].add(k)
    P = sum(len(by_pred[p_of[k]] & by_gold[gold[k]]) / len(by_pred[p_of[k]]) for k in keys) / len(keys)
    R = sum(len(by_pred[p_of[k]] & by_gold[gold[k]]) / len(by_gold[gold[k]]) for k in keys) / len(keys)
    F = 2 * P * R / (P + R) if P + R else 0.0

    docs_of = lambda ks: {k[0] for k in ks}
    multi = {g: ks for g, ks in by_gold.items() if len(docs_of(ks)) >= 2}
    unified = [g for g, ks in multi.items() if len({p_of[k] for k in ks}) == 1]
    # 완전 정답: 쪼개지지 않았고(언급이 한 ID) 그 ID 묶음에 다른 사람도 없음
    exact = [g for g in unified if by_pred[p_of[next(iter(multi[g]))]] == multi[g]]
    # 인물별 상태: 정답 / 섞임만 / 쪼개짐만 / 쪼개짐+섞임
    status = collections.Counter()
    for g, ks in multi.items():
        split = len({p_of[k] for k in ks}) > 1
        mixed_in = any(by_pred[p_of[k]] - by_gold[g] for k in ks)
        status["정답" if not split and not mixed_in else "섞임만" if not split else
               "쪼개짐만" if not mixed_in else "쪼개짐+섞임"] += 1
    pieces = {g: len({p_of[k] for k in ks}) for g, ks in multi.items()}
    cross = {p: ks for p, ks in by_pred.items() if len(docs_of(ks)) >= 2}
    mixed = {p: ks for p, ks in cross.items() if len({gold[k] for k in ks}) > 1}
    return {"P": round(P, 4), "R": round(R, 4), "F1": round(F, 4),
            "gold_persons": len(by_gold), "multi_doc_persons": len(multi),
            "multi_doc_unified": len(unified), "multi_doc_exact": len(exact),
            "multi_doc_status": dict(status),
            "multi_doc_avg_pieces": round(sum(pieces.values()) / len(pieces), 2) if pieces else 0,
            "cross_doc_clusters": len(cross), "mixed_clusters": len(mixed),
            "_pieces": pieces, "_mixed": mixed}


def ask_person(rows, ident, gold, mrow):
    """이름 토큰별 예측 인물 수(검색의 되묻기 조건)와 정답 인물 수 비교."""
    pred_by_token, gold_by_token = collections.defaultdict(set), collections.defaultdict(set)
    for r in rows:
        pred_by_token[r["token"]].add(ident[r["person_id"]])
    for k, g in gold.items():
        r = mrow.get(k)
        if r:
            gold_by_token[r["token"]].add(g)
    asks = [t for t, s in pred_by_token.items() if len(s) > 1]
    needless = [t for t in asks if len(gold_by_token[t]) == 1]
    missed = [t for t, s in pred_by_token.items() if len(s) == 1 and len(gold_by_token[t]) > 1]
    return {"ask_tokens": len(asks), "needless_ask": len(needless), "namesake_missed": len(missed)}


def query_eval(rows, gold, mrow):
    """이름 질의 시뮬레이션 (이름 토큰 하나로 질의, 역할·부서 조건 없음, 문서 단위).

    검색과 같은 방식: 그 토큰 행의 COALESCE(reg_id, person_id) 수가 2 이상이면 되묻기, 1이면 그 토큰의 문서로 답한다.
    채점 (토큰별, 정답 인물 기준)
      이름이 유일한 토큰(정답 인물 1명): 답했는가, 정답 문서 재현율, 다른 사람 문서 포함 여부
      동명이인 토큰(정답 인물 2명 이상): 되묻기면 정상, 답했는데 둘 이상의 문서가 섞이면 오염
    """
    docs_of = collections.defaultdict(set)              # 정답 인물 → 등장 문서
    for (doc, _s, _e), g in gold.items():
        docs_of[g].add(doc)
    token_people = collections.defaultdict(set)        # 이름 토큰 → 정답 인물 (그 토큰으로 언급된 사람)
    for k, g in gold.items():
        r = mrow.get(k)
        if r:
            token_people[r["token"]].add(g)
    by_token = collections.defaultdict(list)
    for r in rows:
        by_token[r["token"]].append(r)
    doc_people = collections.defaultdict(set)
    for (doc, _s, _e), g in gold.items():
        doc_people[doc].add(g)

    out = collections.Counter()
    recalls = []
    for token, people in token_people.items():
        trs = by_token[token]
        ids = {r["reg_id"] or r["person_id"] for r in trs}
        answered = None if len(ids) > 1 else {r["doc_id"] for r in trs}
        if len(people) == 1:
            p = next(iter(people))
            out["유일 이름 질의"] += 1
            if answered is None:
                out["불필요 되묻기"] += 1
                recalls.append(0.0)
                continue
            out["답함"] += 1
            recalls.append(len(answered & docs_of[p]) / len(docs_of[p]))
            if any(p not in doc_people[d] for d in answered):
                out["다른 사람 문서 섞임"] += 1
        else:
            out["동명이인 질의"] += 1
            if answered is None:
                out["동명이인 되묻기(정상)"] += 1
            elif len({frozenset(doc_people[d] & people) for d in answered}) > 1 or \
                    any(not (doc_people[d] & people) for d in answered):
                out["동명이인 섞인 답"] += 1
            else:
                out["동명이인 한 사람으로 답"] += 1
    out_d = dict(out)
    out_d["정답 문서 재현율(유일 이름 평균)"] = round(sum(recalls) / len(recalls), 3) if recalls else 0
    return out_d


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--data", required=True)
    ap.add_argument("--gold", help="인물 정답 jsonl (holdout_persons_gold). 없으면 data 의 entities.person_id")
    ap.add_argument("--detector", action="store_true", help="라벨 span 대신 실제 탐지기 사용")
    ap.add_argument("--max-chars", type=int, default=600)
    ap.add_argument("--rules", choices=("v1", "v3"), default="v1", help="판정 규칙 (PERSON_RULES)")
    ap.add_argument("--shuffle", type=int, help="문서 순서를 이 시드로 섞어 실행 (판정 순서 영향 확인용)")
    ap.add_argument("--out", help="요약 JSON 저장 경로")
    ap.add_argument("--show", type=int, default=0, help="쪼개진 인물·섞인 묶음 예시 N개 출력")
    a = ap.parse_args()

    os.environ["PERSON_RULES"] = a.rules           # .env 와 무관하게 이 옵션으로 (load_dotenv 는 기존 값을 덮지 않음)
    docs = load(a.data)
    gold = gold_mentions(docs, a.gold)
    if a.shuffle is not None:
        import random
        random.Random(a.shuffle).shuffle(docs)
    t0 = time.time()
    result, mapping = run_pipeline(docs, a.max_chars, a.detector, version=a.rules)
    rows = result["rows"]
    mrow = mention_rows(rows)
    strict, loose = identities(rows)

    levels = collections.Counter(r["evidence"].get("level") for r in rows)
    rules_used = collections.Counter(r["evidence"].get("rule") for r in rows if r["evidence"].get("rule"))
    summary = {"data": a.data, "detector": a.detector, "person_rules": a.rules, "shuffle": a.shuffle,
               "docs": len(docs), "gold_mentions": len(gold),
               "found_mentions": sum(k in mrow for k in gold), "rows": len(rows),
               "registry": len([r for r in result["registry"] if not r.get("merged_into")]),
               "resolved_rows": sum(bool(r["reg_id"]) for r in rows), "review": len(result["review"]),
               "levels": dict(levels), "rule_counts": dict(rules_used), "seconds": round(time.time() - t0, 1)}
    print(f"[{a.data}] 문서 {len(docs)} · 정답 언급 {len(gold)} (행에 잡힘 {summary['found_mentions']}) "
          f"· 후보 행 {len(rows)} · registry {summary['registry']} · 검토 {summary['review']}")
    print(f"  판정 level: {dict(levels)}  규칙: {dict(rules_used)}")
    for name, ident in (("확정 기준", strict), ("추정 포함", loose)):
        pred = {k: ident[r["person_id"]] for k, r in mrow.items() if k in gold}
        s = score(gold, pred)
        s.update(ask_person(rows, ident, gold, mrow))
        summary[name] = {k: v for k, v in s.items() if not k.startswith("_")}
        print(f"  [{name}] B3 P={s['P']:.3f} R={s['R']:.3f} F1={s['F1']:.3f} | 여러 문서 인물 "
              f"{s['multi_doc_unified']}/{s['multi_doc_persons']} 하나로 · 완전 정답 {s['multi_doc_exact']} "
              f"{s['multi_doc_status']} (평균 {s['multi_doc_avg_pieces']}조각) "
              f"| 문서 걸친 묶음 {s['cross_doc_clusters']} (섞임 {s['mixed_clusters']}) "
              f"| 되묻기 토큰 {s['ask_tokens']} (불필요 {s['needless_ask']}, 동명이인 놓침 {s['namesake_missed']})")
        if a.show:
            worst = sorted(s["_pieces"].items(), key=lambda x: -x[1])[:a.show]
            print("    많이 쪼개진 인물:", ", ".join(f"{g}({n}조각)" for g, n in worst))
            for p, ks in list(s["_mixed"].items())[:a.show]:
                print("    섞인 묶음:", p, sorted({gold[k] for k in ks}))
    q = query_eval(rows, gold, mrow)
    summary["이름 질의 시뮬레이션"] = q
    print(f"  [이름 질의] 유일 이름 {q.get('유일 이름 질의', 0)}개: 답함 {q.get('답함', 0)} "
          f"· 불필요 되묻기 {q.get('불필요 되묻기', 0)} · "
          f"다른 사람 섞임 {q.get('다른 사람 문서 섞임', 0)} · 문서 재현율 {q['정답 문서 재현율(유일 이름 평균)']} "
          f"| 동명이인 {q.get('동명이인 질의', 0)}개: 되묻기 {q.get('동명이인 되묻기(정상)', 0)} · "
          f"섞인 답 {q.get('동명이인 섞인 답', 0)} · 한 사람 답 {q.get('동명이인 한 사람으로 답', 0)}")
    if a.out:
        os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
        with open(a.out, "w", encoding="utf-8") as f:
            json.dump(summary, f, ensure_ascii=False, indent=2)
        print(f"  저장: {a.out}")


if __name__ == "__main__":
    main()
