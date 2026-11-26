"""문서 메타데이터(담당 팀·담당 직원) 채점.

    python eval/eval_doc_meta.py --gold data/doc_meta_gold.csv --pred outputs/doc_meta.jsonl

gold (CSV, 사람이 라벨링): doc_id, doc_type, text, gold_teams, gold_owner, note
  - gold_teams: 팀 이름을 쉼표로 (예: "디지털전략팀,인사팀"). 정할 수 없으면 "없음"
  - gold_owner: 담당 직원 성명 (예: "정서현"). 정할 수 없으면 "없음"
pred (jsonl, starter/doc_meta.py 출력): doc_id, teams(팀 코드), owner_token, status
  - owner_token → 성명은 vault.token_map 으로 바꾼다(--no-db 면 pred 의 owner_name 칸을 쓴다).
    채점용으로만 실명을 꺼내며, 결과 파일에는 성명을 적지 않는다(일치 여부만).

지표
  팀    정밀도 = 팀을 정한 문서 중 정답과 정확히 같은 비율 (목표 100%)
        커버리지 = 정답 팀이 있는 문서 중 팀을 정한 비율
        틀린 사례는 '다른 팀'(유출 위험)과 '일부만'(정답 팀의 일부만 정함, 보수적)으로 나눠 보여 준다
  담당 직원  정밀도·커버리지 (같은 방식)
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def norm(s):
    return "".join((s or "").split())


def load_gold(path):
    rows = {}
    raw = Path(path).read_bytes()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("cp949")                      # 엑셀에서 저장한 CSV
    for r in csv.DictReader(io.StringIO(text)):
        if not (r.get("gold_teams") or "").strip():
            continue                                  # 아직 라벨링하지 않은 행
        teams = {norm(t) for t in r["gold_teams"].split(",") if norm(t) and norm(t) != "없음"}
        owner = norm(r.get("gold_owner"))
        rows[r["doc_id"]] = {"doc_type": r["doc_type"], "teams": teams,
                             "owner": None if owner in ("", "없음") else owner}
    return rows


def token_names(tokens):
    from starter import db
    with db.ingest_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT token, normalized FROM vault.token_map WHERE token = ANY(%s)", (sorted(tokens),))
        return {t: norm(n) for t, n in cur.fetchall()}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gold", default="data/doc_meta_gold.csv")
    ap.add_argument("--pred", nargs="+", default=["outputs/doc_meta.jsonl"],
                    help="여러 개 가능 (적재 결과 + starter.doc_meta --from-data 결과)")
    ap.add_argument("--no-db", action="store_true", help="vault 조회 없이 pred 의 owner_name 을 사용")
    a = ap.parse_args()

    from policy.access_policy import load_policy
    team_name = load_policy().teams                       # team_cd -> 이름
    gold = load_gold(a.gold)
    pred = {}
    for path in a.pred:
        for l in open(path, encoding="utf-8"):
            r = json.loads(l)
            pred[r["doc_id"]] = r
    need = {p["owner_token"] for p in pred.values() if p.get("owner_token") and "owner_name" not in p}
    names = token_names(need) if need and not a.no_db else {}

    t_dec = t_ok = t_has = 0
    o_dec = o_ok = o_has = 0
    errors = []
    for doc_id, g in gold.items():
        p = pred.get(doc_id)
        if p is None:
            errors.append((doc_id, g["doc_type"], "pred 없음", ""))
            continue
        pt = {norm(team_name.get(t, t)) for t in p.get("teams") or []}
        t_has += bool(g["teams"])
        if pt:
            t_dec += 1
            if pt == g["teams"]:
                t_ok += 1
            else:
                kind = "일부만(보수적)" if pt < g["teams"] else "다른 팀(유출 위험)"
                errors.append((doc_id, g["doc_type"], f"팀 {kind}", f"pred={sorted(pt)} gold={sorted(g['teams'])}"))
        po = p["owner_name"] if "owner_name" in p else names.get(p.get("owner_token"))
        po = norm(po) or None
        o_has += bool(g["owner"])
        if po:
            o_dec += 1
            if po == g["owner"]:
                o_ok += 1
            else:
                errors.append((doc_id, g["doc_type"], "담당 직원 불일치", ""))   # 성명은 출력하지 않음

    def pct(x, y):
        return f"{x}/{y} ({x / y:.0%})" if y else "-"

    print(f"채점 문서 {len(gold)}건")
    print(f"팀       정밀도 {pct(t_ok, t_dec)}  커버리지 {pct(t_dec, t_has)}")
    print(f"담당 직원 정밀도 {pct(o_ok, o_dec)}  커버리지 {pct(o_dec, o_has)}")
    for e in errors:
        print("  ✗", *e)


if __name__ == "__main__":
    main()
