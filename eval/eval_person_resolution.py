"""인물 사전 평가: 같은 사람은 같은 토큰으로 모이고(재현율), 다른 사람은 섞이지 않았나(정밀도)?

B-cubed 정밀도·재현율로 채점한다. 정답 인물 ID(gold)는 채점에만 쓴다.
  P (섞임 없음) : 언급마다 '같은 토큰을 받은 언급 중 진짜 같은 사람 비율'의 평균. 목표 1.000
  R (안 쪼개짐) : 언급마다 '진짜 같은 사람 언급 중 같은 토큰을 받은 비율'의 평균

사용법 1) 적재 결과 채점 (탐지까지 포함한 실제 결과, DB 불필요)
    python eval/eval_person_resolution.py --gold data/holdout_persons_gold.jsonl \\
        --pred outputs/pseudo_holdout_labeled.jsonl
    PERSON_DICT=off / on 으로 각각 적재한 pseudo 파일을 --pred 여러 개로 주면 나란히 비교한다.

사용법 2) 인물 사전만 따로 채점 (탐지 대신 라벨 span 사용, DB·적재 불필요)
    python eval/eval_person_resolution.py --gold data/holdout_persons_gold.jsonl \\
        --data data/holdout_labeled.jsonl
    이름 토큰(지금 방식)과 인물 사전(R6 on/off)을 함께 보여 준다. 라벨 span 은 '입력'으로만 쓰고,
    누가 누구인지(정답 인물 ID)는 사전을 만들 때 보지 않는다.
"""
import argparse
import collections
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def load(path):
    with open(path, encoding="utf-8") as f:
        return [json.loads(l) for l in f if l.strip()]


def gold_map(gold_rows):
    """{(doc_id, start, end): 정답 인물 ID}, {정답 인물 ID: 이름}"""
    out, names = {}, {}
    for g in gold_rows:
        for m in g["persons"]:
            out[(g["id"], m["start"], m["end"])] = m["person_id"]
            if m.get("form") != "given":
                names.setdefault(m["person_id"], m["text"].replace(" ", ""))
    return out, names


def pred_from_pseudo(rows, gold):
    """pseudo 파일의 person 토큰을, 겹치는 정답 이름 구간에 붙인다. 겹치는 토큰이 없으면 미탐."""
    by_doc = collections.defaultdict(list)
    for r in rows:
        by_doc[r["id"]] = [e for e in r["entities"] if e["label"] == "person"]
    pred = {}
    for (doc_id, start, end) in gold:
        hit = [e for e in by_doc.get(doc_id, []) if e["start"] < end and start < e["end"]]
        if hit:
            pred[(doc_id, start, end)] = hit[0]["token"]
    return pred


def bcubed(pred, gold):
    keys = [k for k in gold if k in pred]
    by_pred, by_gold = collections.defaultdict(set), collections.defaultdict(set)
    for k in keys:
        by_pred[pred[k]].add(k)
        by_gold[gold[k]].add(k)
    P = R = 0.0
    for k in keys:
        same = by_pred[pred[k]] & by_gold[gold[k]]
        P += len(same) / len(by_pred[pred[k]])
        R += len(same) / len(by_gold[gold[k]])
    n = len(keys) or 1
    return P / n, R / n, len(keys)


def problems(pred, gold, names):
    """쪼개진 인물 수, 동명이인이 섞인 토큰 수, 이름이 다른 사람이 섞인 토큰 수"""
    tokens_of, people_of = collections.defaultdict(set), collections.defaultdict(set)
    for k, g in gold.items():
        if k in pred:
            tokens_of[g].add(pred[k])
            people_of[pred[k]].add(g)
    split = sum(1 for ts in tokens_of.values() if len(ts) > 1)
    merged = [ps for ps in people_of.values() if len(ps) > 1]
    homonym = sum(1 for ps in merged if len({names.get(p) for p in ps} - {None}) <= 1)
    return split, homonym, len(merged) - homonym


def report(title, pred, gold, names):
    P, R, n = bcubed(pred, gold)
    F = 2 * P * R / (P + R) if P + R else 0.0
    split, homonym, other = problems(pred, gold, names)
    miss = len(gold) - n
    print(f"{title:<24} P={P:.3f}  R={R:.3f}  F1={F:.3f}  | 쪼개진 인물 {split:>3}명  "
          f"동명이인 섞임 {homonym:>2}토큰  그 외 섞임 {other:>2}토큰  미탐 {miss}회")


def offline(data_path, gold, names):
    """라벨 span 으로 인물 사전만 만들어 본다 (DB 불필요). HMAC_KEY 가 필요하다."""
    from starter import person_dict as pd
    from starter.roles import default_rules
    rules = default_rules()
    docs = [{"id": d["id"], "text": d["text"],
             "spans": [{"label": e["label"], "start": e["start"], "end": e["end"]} for e in d["entities"]]}
            for d in load(data_path)]
    name_pred = {}
    for d in docs:
        for s in d["spans"]:
            if s["label"] == "person":
                try:
                    name_pred[(d["id"], s["start"], s["end"])] = pd.normalize_name(d["text"][s["start"]:s["end"]])
                except ValueError:
                    pass
    report("이름 토큰 (지금)", name_pred, gold, names)
    saved = {k: os.environ.get(k) for k in ("PERSON_LINK_UNIQUE", "PERSON_LINK_GIVEN")}
    try:
        for unique, given in (("off", "off"), ("on", "off"), ("on", "on")):
            os.environ["PERSON_LINK_UNIQUE"], os.environ["PERSON_LINK_GIVEN"] = unique, given
            dic = pd.build_dictionary(docs, rules)
            pred = {k: v["token"] for k, v in dic["mentions"].items()}
            report(f"인물 사전 R6={unique} R7={given}", pred, gold, names)
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gold", required=True)
    ap.add_argument("--pred", nargs="*", default=[], help="ingest 가 만든 pseudo_*.jsonl (여러 개 비교 가능)")
    ap.add_argument("--data", help="라벨 jsonl (인물 사전만 따로 채점)")
    a = ap.parse_args()
    if not a.pred and not a.data:
        ap.error("--pred 또는 --data 중 하나는 필요합니다")
    gold, names = gold_map(load(a.gold))
    print(f"정답 인물 {len(set(gold.values()))}명, 이름 언급 {len(gold)}회\n")
    for path in a.pred:
        title = os.path.join(os.path.basename(os.path.dirname(path)), os.path.basename(path))
        report(title if len(title) <= 24 else "…" + title[-23:], pred_from_pseudo(load(path), gold), gold, names)
    if a.data:
        offline(a.data, gold, names)


if __name__ == "__main__":
    main()
