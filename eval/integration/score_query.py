"""03 영역(앞부분): 질문 처리 채점. 입력: traces/search_<설정>.jsonl — 실행 대상의 모든 검색 설정을 각각 채점한다
(인물사전 ON/OFF 에 따라 질문 가명처리가 달라질 수 있으므로).

- Q01 질문 개인정보: 정답 개체마다 저장소와 같은 HMAC 토큰을 계산해, 그 토큰이 질문 토큰에 있는지 (개체-토큰을 짝으로 비교)
       미지원 식별정보(여권·고객번호·사번·운전면허)는 토큰이 없으므로 비교에서 빼고 표시한다
- P04·P08 과잉 가명처리: 개인정보가 없는 질문(상품명·거래번호 등)에서 토큰 0개
- Q02 정규화 불가 표기: 오류 없이 처리되고, 원문(sens)이 가명처리된 질문에 남지 않음
- Q03 필수 키워드 보존 / Q04 불용어 제거: **실행 당시** 기록한 키워드(probe.terms)로 채점한다.
       기록이 없는 옛 실행만 지금 코드로 다시 추출하고 비고에 적는다 (불용어 코드가 바뀌면 결과가 달라질 수 있음)
- Q05 조건 보존(날짜·금액·유형): 가명처리된 질문에 조건 값이 그대로 남음
- Q06 부정 조건 보존: 부정 어미(않·안 되·못·없·미-)가 키워드에 남고, 부정되는 동사 어간('완료'·'출금')도 남음
       ('안내'·'미팅'처럼 글자만 겹치는 말은 부정으로 보지 않음)
재질문(Q07~Q12)·역할 지정(Q08·Q14) 등 답변 단계의 상태는 score_answer.py 가 채점한다.
실행 대상 목록(plan) 기준으로 실행 오류·미실행 행을 남기고 통과율에서 뺀다.
"""
import collections
import re

from .common import EXEC_ERROR, JUDGE_RESULT, NOT_RUN, Gold, jl_read, latest, plan_targets, result_row, set_row, token_of

UNSUPPORTED_Q = ("passport_number", "customer_id", "employee_id", "driver_license")
NEG_TERM = re.compile(r"않|없|못|^안$|^안[되된돼됐됨될]|^미[처완결납제수]")
STEM = re.compile(r"^(\S+?)(?:되지|하지|지)\s*(?:않|못)|^(\S+?)\s*(?:안|못)\s*\S")
QUERY_CHECKS = ("Q01", "Q02", "Q03", "Q04", "Q05", "Q06", "P04", "P08")


def expected_pairs(c):
    """[(정답 개체, 토큰 또는 None)] — 미지원 식별정보는 빼고 개체와 토큰을 짝으로 둔다."""
    out = []
    for e in c["expected_query_entities"]:
        if e["label"] in UNSUPPORTED_Q:
            continue
        try:
            out.append((e, token_of(e["label"], e.get("value") or e["text"])))
        except ValueError:
            out.append((e, None))
    return out


def negation_kept(value, terms):
    """부정 조건 value('완료되지 않은', '안 된')가 키워드 terms 에 살아 있는지 → (ok, 설명)"""
    neg = sorted(x for x in terms if NEG_TERM.search(x))
    m = STEM.match(value.strip())
    stem = (m.group(1) or m.group(2)) if m else None
    stem_ok = (not stem) or any(stem in x for x in terms)
    ok = bool(neg) and stem_ok
    why = f"부정 {','.join(neg) or '없음'}" + (f", 어간 '{stem}' {'남음' if stem_ok else '사라짐'}" if stem else "")
    return ok, why


def score(run_dir, run_id):
    g = Gold()
    targets = plan_targets(run_dir, "search", g.cases)
    if not targets:
        return [], [], {"skipped": "검색 실행 대상 없음"}
    rows = []
    stats = collections.defaultdict(lambda: [0, 0])          # (cfg, item) → [통과, 판정 수]
    per_split = collections.defaultdict(lambda: [0, 0])
    reextracted = 0
    extract_terms = None

    for cfg, cases in targets.items():
        p = run_dir / "traces" / f"search_{cfg}.jsonl"
        tr_all = latest(jl_read(p)) if p.exists() else {}

        def add(c, item, content, expected, actual, ok, stage, note=""):
            chk = next((x for x in c["checks"] if x["check_id"] == item), None)
            judge = (chk["judgement"].get(cfg) or next(iter(chk["judgement"].values()))) if chk else "run"
            res = JUDGE_RESULT.get(judge) or ("통과" if ok else "실패")
            rows.append(result_row("질문", c["qid"], c["split"], c["difficulty"], cfg, item, content, expected, actual,
                                   res, stage, note))
            key = "P04" if item == "P08" else item
            if res in ("통과", "실패"):
                for d in (stats[(cfg, key)], per_split[(cfg, "전체", key)], per_split[(cfg, c["split"], key)]):
                    d[0] += ok
                    d[1] += 1

        for c in cases:
            t = tr_all.get(c["qid"])
            scen = set(c["scenario_ids"])
            mine = [x for x in QUERY_CHECKS if x in scen]
            if t is None or t.get("exec_error"):
                e = (t or {}).get("exec_error") or {}
                for item in mine:
                    rows.append(result_row("질문", c["qid"], c["split"], c["difficulty"], cfg, item, "질문 처리", "",
                                           f"{e.get('kind')} {e.get('message', '')[:80]}" if t else "실행 기록 없음",
                                           EXEC_ERROR if t else NOT_RUN))
                continue
            mq, qt = t.get("masked_query") or "", set(t.get("query_tokens") or [])
            pr = t.get("probe") or {}
            note = ""
            if "terms" in pr:
                terms = set(pr["terms"])
            else:
                if extract_terms is None:
                    from starter.bm25 import extract_terms
                terms = extract_terms(mq) if mq else set()
                note = "실행 당시 키워드 기록 없음: 지금 코드로 다시 추출"
                reextracted += 1
            pairs = expected_pairs(c)
            unsup = [e["text"] for e in c["expected_query_entities"] if e["label"] in UNSUPPORTED_Q]
            if c["expected_query_entities"] and ({"Q01", "P05", "S02"} & scen):
                miss = [e["text"] for e, tok in pairs if tok and tok not in qt]
                bad = [e["text"] for e, tok in pairs if tok is None]
                n_ok = sum(1 for _, tok in pairs if tok and tok in qt)
                actual = (f"{n_ok}개 일치" + (f", 불일치: {', '.join(miss)}" if miss else "")
                          + (f", 토큰 계산 불가: {', '.join(bad)}" if bad else "")
                          + (f" (미지원 식별정보 {len(unsup)}개 제외)" if unsup else ""))
                if not miss and (bad or not pairs):            # 기대 토큰이 없으면 통과로 보지 않는다
                    rows.append(result_row("질문", c["qid"], c["split"], c["difficulty"], cfg, "Q01",
                                           "질문 개인정보 → 문서와 같은 토큰", "기대 토큰 계산", actual or "비교할 개체 없음",
                                           "미검증", "", note))
                else:
                    add(c, "Q01", "질문 개인정보 → 문서와 같은 토큰", f"{sum(1 for _, t_ in pairs if t_)}개 일치",
                        actual, not miss, "질문 가명처리")
            if not c["expected_query_entities"] and ({"P04", "P08"} & scen):
                item = "P08" if "P08" in scen else "P04"
                add(c, item, "개인정보 아닌 값 과잉 가명처리", "토큰 0개", f"토큰 {len(qt)}개", not qt, "질문 가명처리")
            if "Q02" in scen:
                leaked = [s for s in c["sensitive_query_strings"] if s in mq]
                ok = not t.get("error") and not leaked
                add(c, "Q02", "정규화할 수 없는 표기 처리", "오류 없음, 원문 미기록",
                    f"오류 {t.get('error') or '없음'}, 원문 {'남음' if leaked else '없음'}", ok, "질문 가명처리")
            if c["required_keywords"]:
                lost = [k for k in c["required_keywords"] if not any(k in x for x in terms)]
                add(c, "Q03", "필수 키워드 보존", ", ".join(c["required_keywords"]),
                    "모두 남음" if not lost else f"빠짐: {', '.join(lost)}", not lost, "키워드 추출·불용어", note)
            if c["removable_terms"]:
                left = [k for k in c["removable_terms"] if k in terms]
                add(c, "Q04", "불용어 제거", "남지 않음", "모두 제거" if not left else f"남음: {', '.join(left)}", not left,
                    "키워드 추출·불용어", note)
            for con in c["expected_constraints"]:
                if con["type"] == "negation":
                    ok, why = negation_kept(con["value"], terms)
                    add(c, "Q06", f"부정 조건 '{con['value']}' 보존", "부정 어미와 어간이 키워드에 남음",
                        why + ("" if ok else f" (키워드: {', '.join(sorted(terms))[:80]})"), ok, "키워드 추출·불용어", note)
                else:
                    ok = con["value"].replace(" ", "") in mq.replace(" ", "")
                    add(c, "Q05", f"{con['type']} 조건 '{con['value']}' 보존", "질문에 남음", "남음" if ok else "바뀜", ok,
                        "질문 가명처리")
    sets = []
    for (cfg, sp, key), (a, n) in sorted(per_split.items()):
        sets.append(set_row(f"질문 처리 ({key})", sp, cfg, key, "통과율", a / n if n else 0.0))
    summary = {f"{cfg}/{k}": f"{a}/{n}" for (cfg, k), (a, n) in sorted(stats.items()) if n}
    if reextracted:
        summary["실행 당시 키워드 기록 없이 재추출"] = reextracted
    return rows, sets, summary
