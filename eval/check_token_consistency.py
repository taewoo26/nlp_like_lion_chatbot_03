"""토큰 일관성 채점: 같은 인물은 항상 같은 토큰이 되었는가?

사용법:
    python eval/check_token_consistency.py --gold data/holdout_persons_gold.jsonl --pred outputs/pseudo_holdout_labeled.jsonl

pred 파일 형식 (starter.ingest 가 만들어 줌, 원문 기준 위치):
    {"id": "H0001", "entities": [{"label": "person", "start": 10, "end": 13, "token": "<PERSON_03f0d420>"}, ...]}

채점 대상은 person 라벨입니다. 정답 이름 구간과 겹치는 예측 구간의 토큰을 그 인물의 토큰으로 봅니다.
    미탐       : 정답 이름 구간이 어떤 토큰으로도 바뀌지 않음 (원문이 그대로 남음)
    쪼개짐     : 한 인물이 2개 이상의 토큰으로 나뉨  (예: '윤 선영'과 '윤선영'이 다른 토큰)
    합쳐짐     : 한 토큰에 2명 이상의 인물이 묶임
      - 동명이인 : 묶인 인물들의 이름이 같음 (이름 문자열 기준 토큰이면 실전 2에서는 예상되는 결과)
      - 그 외    : 이름이 다른 사람끼리 묶임 (정규화나 토큰 생성 오류)
토큰을 만드는 방법은 알려주지 않고 결과만 채점합니다. 사례는 outputs/token_consistency_cases.csv 에 저장됩니다.
"""
import argparse
import collections
import csv
import json
import os


def load(path):
    with open(path, encoding="utf-8") as f:
        return {d["id"]: d for d in (json.loads(l) for l in f if l.strip())}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gold", required=True)
    ap.add_argument("--pred", required=True)
    ap.add_argument("--out", default="outputs/token_consistency_cases.csv")
    a = ap.parse_args()
    gold, pred = load(a.gold), load(a.pred)

    person_tokens = collections.defaultdict(collections.Counter)   # person_id -> token -> 횟수
    token_persons = collections.defaultdict(set)                   # token -> person_id
    names, missed, total = {}, [], 0
    for doc_id, g in gold.items():
        P = pred.get(doc_id, {"entities": []})["entities"]
        for m in g["persons"]:
            total += 1
            names.setdefault(m["person_id"], m["text"].replace(" ", "") if m.get("form") != "given" else None)
            hit = [p for p in P if p["start"] < m["end"] and m["start"] < p["end"]]
            if not hit:
                missed.append((doc_id, m["person_id"], m["text"]))
                continue
            for p in hit:
                person_tokens[m["person_id"]][p["token"]] += 1
                token_persons[p["token"]].add(m["person_id"])

    persons = {m["person_id"] for g in gold.values() for m in g["persons"]}
    split = {p: t for p, t in person_tokens.items() if len(t) > 1}
    merged = {t: ps for t, ps in token_persons.items() if len(ps) > 1}
    homonym = {t: ps for t, ps in merged.items() if len({names.get(p) for p in ps} - {None}) <= 1}
    wrong = {t: ps for t, ps in merged.items() if t not in homonym}

    print(f"정답 인물 {len(persons)}명, 이름 등장 {total}회")
    print(f"미탐 (토큰화 안 됨) : {len(missed)}회 ({len(missed) / total:.1%})")
    print(f"쪼개짐             : {len(split)}명 ({len(split) / len(persons):.1%})")
    print(f"합쳐짐 - 동명이인   : {len(homonym)}개 토큰")
    print(f"합쳐짐 - 그 외      : {len(wrong)}개 토큰")

    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    with open(a.out, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["type", "doc_id_or_token", "person_id", "detail"])
        for doc_id, pid, text in missed:
            w.writerow(["미탐", doc_id, pid, text])
        for pid, toks in split.items():
            w.writerow(["쪼개짐", "", pid, " | ".join(f"{t}×{c}" for t, c in toks.items())])
        for t, ps in homonym.items():
            w.writerow(["합쳐짐(동명이인)", t, "|".join(sorted(ps)), names.get(next(iter(ps))) or ""])
        for t, ps in wrong.items():
            w.writerow(["합쳐짐(그 외)", t, "|".join(sorted(ps)), " / ".join(sorted({names.get(p) or "?" for p in ps}))])
    print(f"사례 저장: {a.out}")


if __name__ == "__main__":
    main()
