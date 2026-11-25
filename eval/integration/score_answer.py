"""05 답변·06 권한(질문 단위)·재질문·공통 검사 채점. 입력: traces/answer_<설정>.jsonl (LLM 답변 기록)

질문 × 설정 × 사용자 레벨마다 정답지(it_cases.jsonl)와 비교한다.
  실행 단위 : 질문 × 설정 × 사용자 레벨. run.py 가 서비스 API(/api/chat)를 레벨별 계정으로 따로 호출한 기록을 채점한다
  상태      : 실제 state 가 그 사용자·설정의 기대 상태 목록 안에 있음
  X06 감사  : 요청마다 audit.query_log 1행, 사용자·레벨·상태·반환 문서가 응답과 같고 masked_query 에 원문 없음
  필수 사실  : fact_status() — 허용 표기(match)를 종류별로 비교한다
               금액  '3천만'·'3,000만'·'1억 8천' → 원 단위 숫자로 바꿔 같은 값이 답변에 있는지 ('1500만'은 '500만'이 아님)
               날짜  '11월 17일'·'11-17'·'2026.11.17' → (월, 일) 로 바꿔 비교. '12월'은 월만
               숫자  '36개월'·'0.5'·'30일' → 앞뒤가 다른 숫자에 붙어 있지 않을 때만 일치
               말    '정지'·'승인'·'지연' → 들어 있고, 그 말이 부정되지 않았을 때 ('정지되지 않았', '승인 안 됨', '미승인')
                     부정된 채로만 나오면 단정하지 않고 '수동 확인'
               개인정보 사실은 그 값의 토큰이 답변에 그대로 있을 때 (토큰 정확 일치)
  금지 사실  : 같은 비교로 금지 표기·토큰이 답변에 있거나, 금지 원문(raw)이 답변·조각에 있음 (부정된 채로만 있으면 수동 확인)
  유출(X04) : 통합 테스트 문서와 함께 적재한 기존 문서의 개인정보 원문(이름 3자 이상·번호 6자 이상)이
              답변·조각·가명처리 질문에 있음 (미지원 식별정보는 따로 셈)
  X01 토큰 : 답변의 토큰이 질문·근거 문서의 토큰 안에 있음
  X02 인용 : 답변의 [문서ID] 가 근거 문서 안에 있고, 근거로 받은 정답 문서 중 하나 이상을 인용
  X03 재질문: 기대 상태에 clarify 가 없는데 clarify
시나리오 판정 (질문의 checks): A·G·I·P05/P07/P09 시나리오는 상태·필수 사실·금지 사실·유출을 모두 만족해야 통과.
  단정할 수 없는 사실만 남으면 '수동 확인'. A04: 혼동 문서가 근거로 들어오지 않으면 '조건 미충족',
  G12·G13: 재검사가 동작할 일이 없었으면 '미검증'.
실행 대상은 manifest 의 실행 대상 목록(plan) 기준이다. 실행 오류·미실행 질문은 오답으로 세지 않고 '실행 오류'·'미실행'
행으로 남기며 지표에서 뺀다. 결과 파일이 없는 설정도 목록에 있으면 전부 '미실행'이다.
"""
import collections
import re

from .common import (EXEC_ERROR, JUDGE_RESULT, MANUAL, NOT_RUN, Gold, holdout_raw, jl_read, plan_targets,
                     result_row, set_row, token_of)

CITE = re.compile(r"\[([A-Za-z]+\d[A-Za-z0-9_-]*)\]")
TOKEN = re.compile(r"<[A-Za-z_]+_[0-9A-Za-z]+>")
UNSUPPORTED = ("passport_number", "customer_id", "employee_id")
QUERY_ONLY = {f"Q0{i}" for i in range(1, 7)}       # score_query.py 가 채점
ANSWER_SCEN = lambda cid: not (cid in QUERY_ONLY or cid[0] in "SX" or (cid[0] == "P" and cid not in ("P05", "P07", "P09")))


def _tok(label, value):
    try:
        return token_of(label, value)
    except ValueError:
        return None


# ------------------------------------------------------------------ 사실 비교
UNIT = {"억": 10 ** 8, "천만": 10 ** 7, "백만": 10 ** 6, "십만": 10 ** 5, "만": 10 ** 4, "천": 10 ** 3}
_NUM = r"\d[\d,]*(?:\.\d+)?"
MONEY = re.compile(rf"(?<![\d.,])({_NUM})\s*(억|천만|백만|십만|만|천)(?:\s*({_NUM})\s*(천만|백만|십만|만|천)?)?")
WON = re.compile(r"(?<![\d.,])(\d{1,3}(?:,\d{3})+|\d{5,})\s*원")
DATE_YMD = re.compile(r"(?<!\d)(\d{4}|\d{2})\s*[-./년]\s*(\d{1,2})\s*[-./월]\s*(\d{1,2})(?!\d)")
DATE_MD = re.compile(r"(?<![\d.])(\d{1,2})\s*월\s*(\d{1,2})\s*일")
DATE_MD2 = re.compile(r"(?<![\d./-])(\d{1,2})\s*[-/]\s*(\d{1,2})(?![\d./-])")
MONTH = re.compile(r"(?<![\d.])(\d{1,2})\s*월(?!\s*\d)")
NEG_INHERENT = ("않", "없", "못", "아니", "아직", "미처리", "미완료", "미제출", "미발송", "미납", "불가", "거절", "실패")
# 숫자·금액·날짜 뒤의 부정: '500만 원이 아니라', '11월 17일이 아닌'
NUM_NEG = re.compile(r"^\s*(?:만\s*)?(?:원|개월|일|건|%|퍼센트|시)?\s*(?:이|가|은|는|을|를|도)?\s*(?:아니|아닌|아닙|않)")
# 완료가 아닌 표현: '승인 예정', '승인 검토 중', '승인 대기', '승인 요청'
PENDING = re.compile(r"^[^\s]*?\s*(?:할|될|하실|되실)?\s*(?:예정|검토\s*중|검토중|대기|요청|진행\s*중|여부|가능성)")
PENDING_WORDS = ("예정", "대기", "검토", "진행", "요청", "중", "보류", "접수")
NEG_DESC = re.compile(r"않|없|아니|아직|(?<!잘)못|미처리|미완료|미제출|미발송|미납|안\s*[되돼됐됨된]")   # 사실 설명이 부정형인지
NEG_AFTER = re.compile(r"^[^\s]*?(?:지\s*않|지\s*못|지\s*마|불가|아니|아님|없)"
                       r"|^[^\s]*\s+(?:안\s*[되돼됐됨됩될하해했함합]|않|못|아니|아닙|아닌|없)"
                       r"|^[^\s]*\s+\S*?지\s*(?:않|못)")   # 한 단어 뒤 부정: '승인을 받지 못했습니다'
NEG_BEFORE = ("미", "불", "비", "무", "안 ", "못 ")


def _num(x):
    return float(x.replace(",", ""))


def amounts(text):
    """금액 표현 → 원 단위 정수 집합. '1억 8천' 은 1억 8천만으로 읽는다."""
    return {v for v, _ in _money_hits(text)}


def _date_hits(text):
    """[(년 또는 None, 월, 일 또는 None, 끝 위치)]"""
    out = []
    for m in DATE_YMD.finditer(text):
        y, mo, d = m.group(1), int(m.group(2)), int(m.group(3))
        y = int(y) + (2000 if len(y) == 2 else 0)
        if 1 <= mo <= 12 and 1 <= d <= 31:
            out.append((y, mo, d, m.end()))
    for rx in (DATE_MD, DATE_MD2):
        for m in rx.finditer(text):
            mo, d = int(m.group(1)), int(m.group(2))
            if 1 <= mo <= 12 and 1 <= d <= 31:
                y = None
                pre = re.search(r"(\d{4})\s*년\s*$", text[max(0, m.start() - 8):m.start()])
                if pre:
                    y = int(pre.group(1))
                out.append((y, mo, d, m.end()))
    for m in MONTH.finditer(text):
        mo = int(m.group(1))
        if 1 <= mo <= 12:
            out.append((None, mo, None, m.end()))
    return out


def dates(text):
    """날짜 → {(월, 일)} 과 월만 쓴 {(월, None)}."""
    return {(mo, d) for _, mo, d, _ in _date_hits(text)}


def _money_hits(text):
    """[(원 단위 값, 끝 위치)]"""
    out = []
    for m in MONEY.finditer(text):
        v = _num(m.group(1)) * UNIT[m.group(2)]
        if m.group(3):
            u = m.group(4)
            if m.group(2) == "억":
                u = {"천": "천만", None: "만"}.get(u, u)
            if u:
                v += _num(m.group(3)) * UNIT[u]
        out.append((round(v), m.end()))
    for m in WON.finditer(text):
        out.append((round(_num(m.group(1))), m.end()))
    return out


def _kind(m):
    if amounts(m):
        return "money"
    if dates(m):
        return "date"
    if re.match(r"^\d", m):
        return "number"
    return "text"


def _strings(x):
    """JSON 응답 안의 모든 문자열 값 (키 이름 제외)."""
    if isinstance(x, str):
        yield x
    elif isinstance(x, dict):
        for v in x.values():
            yield from _strings(v)
    elif isinstance(x, (list, tuple)):
        for v in x:
            yield from _strings(v)


def _negated(text, i, j):
    after = text[j:j + 12]
    before = text[max(0, i - 2):i]
    return bool(NEG_AFTER.match(after)) or any(before.endswith(p) for p in NEG_BEFORE)


def _verdict(ends, text):
    """같은 값이 나온 위치들(끝 위치) → 부정되지 않은 곳이 하나라도 있으면 hit, 모두 부정이면 neg"""
    if not ends:
        return "miss"
    return "hit" if any(not NUM_NEG.match(text[e:e + 15]) for e in ends) else "neg"


def match_status(m, text, negative_fact=False, pending_fact=False):
    """한 표기 m 이 text 에 있는가: 'hit' / 'neg'(부정·미완료·반대로만 나옴 → 단정 불가) / 'miss'
    negative_fact: 사실 자체가 부정형('회신 없음') → 답에서도 부정과 함께 나와야 hit, 긍정으로만 나오면 neg
    pending_fact: 사실 자체가 미완료('승인 예정') → '예정' 표현 검사를 하지 않는다"""
    if not m or not text:
        return "miss"
    k = _kind(m)
    if k == "money":
        want = amounts(m)
        return _verdict([e for v, e in _money_hits(text) if v in want], text)
    if k == "date":
        want = [(y, mo, d) for y, mo, d, _ in _date_hits(m)]
        ends = []
        for y, mo, d, e in _date_hits(text):
            for wy, wmo, wd in want:
                if mo == wmo and (wd is None or d == wd) and (wy is None or y is None or wy == y) and (d is not None or wd is None):
                    ends.append(e)
        return _verdict(ends, text)
    if k == "number":
        rx = re.compile(r"(?<![\d.,])" + re.escape(m) + (r"(?![\d,]|\.\d)" if m[-1].isdigit() else ""))
        return _verdict([x.end() for x in rx.finditer(text)], text)
    found = [x for x in re.finditer(re.escape(m), text)]
    if not found:
        return "miss"
    if any(n in m for n in NEG_INHERENT):          # 표기 자체가 부정('미제출')이면 그대로 일치
        return "hit"
    negated = [_negated(text, x.start(), x.end()) for x in found]
    if negative_fact:                               # 사실이 부정형: 답에서도 부정과 함께 나와야 일치
        return "hit" if any(negated) else "neg"
    pending = [not pending_fact and not any(w in m for w in PENDING_WORDS)
               and bool(PENDING.match(text[x.end():x.end() + 14])) for x in found]
    return "hit" if any(not n and not p for n, p in zip(negated, pending)) else "neg"


def fact_status(f, text):
    """사실 하나: 'hit' / 'manual'(부정 표현과 함께만 나와 단정 불가) / 'miss'"""
    desc = f.get("desc") or ""
    neg_fact = bool(NEG_DESC.search(desc))
    pending_fact = any(w in desc for w in PENDING_WORDS)
    st = [match_status(m, text, neg_fact, pending_fact) for m in f.get("match") or []]
    for r in f.get("pii_refs") or []:
        t = _tok(r["label"], r["value"])
        st.append("hit" if t and t in text else "miss")
    if "hit" in st:
        return "hit"
    return "manual" if "neg" in st else "miss"


def forbid_status(f, text, outputs):
    if f.get("raw") and any(v in outputs for v in f["raw"]):
        return "hit"
    return fact_status(f, text) if (f.get("match") or f.get("pii_refs")) else "miss"


def _index(rows):
    """답변 기록 → {'질문|레벨': 행} (옛 기록은 {'질문': 행}). 같은 키는 마지막 행."""
    return {(r.get("key") or r["qid"]): r for r in rows}


def _ox(v):
    return "-" if v is None else ("O" if v else "X")


def score(run_dir, run_id):
    g = Gold()
    raw = {}
    for v, lab in holdout_raw(run_dir).items():                    # 함께 적재한 기존 문서의 개인정보
        raw[v] = "unsupported" if lab in UNSUPPORTED else "pii"
    for d in g.docs.values():
        for e in d["entities"]:
            if (e["label"] == "person" and len(e["text"]) >= 3) or (e["label"] != "person" and len(e["text"]) >= 6):
                raw[e["text"]] = "unsupported" if e["label"] in UNSUPPORTED else "pii"
    targets = plan_targets(run_dir, "answer", g.cases)
    if not targets:
        return [], [], {"skipped": "답변 실행 대상 없음 (--skip-answers 이거나 --only search)"}
    rows, sets = [], []
    agg = collections.defaultdict(lambda: collections.Counter())
    run_state = {}
    for cfg, cases in targets.items():
        p = run_dir / "traces" / f"answer_{cfg}.jsonl"
        tr = _index(jl_read(p)) if p.exists() else {}
        rs = run_state.setdefault(cfg, collections.Counter())
        for c in cases:
            judge_of = {k["check_id"]: k["judgement"].get(cfg) for k in c["checks"]}
            for lv, u in c["users"].items():                    # 사용자 레벨마다 따로 실행·채점 (API 경로)
                t = tr.get(f"{c['qid']}|{lv}") or tr.get(c["qid"])     # 옛 기록(질문당 한 번)도 읽는다
                base = dict(unit="질문", run_id=c["qid"], split=c["split"], diff=c["difficulty"], cfg=cfg, level=lv)
                rs["대상"] += 1
                if t is None or "exec_error" in t:
                    rs[EXEC_ERROR if t else NOT_RUN] += 1
                    e = (t or {}).get("exec_error") or {}
                    why = (f"{e.get('kind')} HTTP {e.get('status')} {e.get('code') or ''} {e.get('message', '')[:80]}"
                           if t else "실행 기록 없음")
                    res_ = EXEC_ERROR if t else NOT_RUN
                    ids = [k["check_id"] for k in c["checks"] if (ANSWER_SCEN(k["check_id"]) or k["check_id"] in ("S11", "S12"))
                           and k["judgement"].get(cfg) is not None]
                    for cid in ids + ["X04", "X06"]:            # 보안 목록(X04·X06)도 실행 오류·미실행으로 남긴다
                        rows.append(result_row(**base, item_id=cid, content=f"{lv}: {c['variant_type']} ({c['intent']})",
                                               expected="", actual=why, result=res_))
                    continue
                rs["정상 실행"] += 1
                ans = t.get("answer") or ""
                snippets = " ".join((d.get("snippet") or "") for d in t.get("docs") or [])
                api = t.get("api_body")                         # 실제 API 응답 전체 (API 경로)
                outputs = (" ".join(_strings(api)) if api is not None
                           else " ".join([ans, snippets, t.get("masked_query") or ""]))
                returned = [d["doc_id"] for d in t.get("docs") or []]
                doc_text = "\n".join(d.get("masked_text") or "" for d in t.get("docs") or [])
                leaks = sorted(v for v, k in raw.items() if k == "pii" and v in outputs)
                unsup = sorted(v for v, k in raw.items() if k == "unsupported" and v in outputs)
                st = t.get("state")
                allowed = set(t.get("query_tokens") or []) | {x for d in t.get("docs") or [] for x in d.get("tokens") or []}
                stray = sorted(set(TOKEN.findall(ans)) - allowed)
                cited = set(CITE.findall(ans))
                bad_cite = sorted(cited - set(returned))
                rel_back = set(u["relevant_doc_ids"]) & set(returned)
                miss_cite = sorted(rel_back) if st == "answered" and rel_back and not rel_back & cited else []
                note = "수동 확인 필요: " + c["note"] if c.get("note") and "수동" in c["note"] else ""
                rows.append(result_row(**base, item_id="X01", content="답변 토큰 ⊆ 질문·근거 문서 토큰", expected="0건",
                                       actual=f"{len(stray)}건", result="통과" if not stray else "실패", stage="LLM 답변"))
                if st == "answered":
                    ok2 = not bad_cite and not miss_cite
                    rows.append(result_row(**base, item_id="X02", content="근거 인용 정확·누락 없음", expected="잘못 0, 누락 0",
                                           actual=f"잘못 {len(bad_cite)}, 누락 {len(miss_cite)}",
                                           result="통과" if ok2 else "실패", stage="LLM 답변"))
                rows.append(result_row(**base, item_id="X04", content="개인정보 원문 유출 (실제 API 응답 전체)",
                                       expected="0건",
                                       actual=f"{len(leaks)}건" + (f" (미지원 식별정보 {len(unsup)}건 별도)" if unsup else ""),
                                       result="통과" if not leaks else "실패", stage="재검사"))
                au = t.get("audit")
                if au is None:
                    rows.append(result_row(**base, item_id="X06", content="질문 요청 감사 로그 (query_log)", expected="요청당 1행",
                                           actual="감사 로그 확인 기록 없음 (API 경로가 아닌 옛 실행)", result="미검증"))
                else:
                    ok6 = (au.get("rows") == 1 and au.get("user_ok") and au.get("level_ok") and au.get("state_ok")
                           and au.get("docs_ok") and not au.get("raw_in_masked"))
                    rows.append(result_row(**base, item_id="X06", content="질문 요청 감사 로그 (query_log)",
                                           expected="1행, 사용자·레벨·상태·반환 문서 일치, masked_query 원문 0",
                                           actual=(f"{au.get('rows')}행, 사용자 {_ox(au.get('user_ok'))}, 레벨 {_ox(au.get('level_ok'))}, "
                                                   f"상태 {_ox(au.get('state_ok'))}, 문서 {_ox(au.get('docs_ok'))}, "
                                                   f"원문 {au.get('raw_in_masked', '-')}"),
                                           result="통과" if ok6 else "실패", stage="감사 로그"))
                if judge_of.get("S11") is not None:
                    used = sorted(cited & set(returned))
                    ok11 = not (st == "answered" and used)
                    rows.append(result_row(**base, item_id="S11", content="정답 없는 질문: 무관 문서를 근거로 쓰지 않음",
                                           expected="no_result 또는 근거 인용 없음",
                                           actual=f"상태 {st}" + (f", 인용 {','.join(used)}" if used else ""),
                                           result=JUDGE_RESULT.get(judge_of["S11"]) or ("통과" if ok11 else "실패"),
                                           stage="LLM 답변"))
                if judge_of.get("S12") is not None:
                    llm_in = set(t.get("llm_input_doc_ids") or returned)
                    miss_doc = sorted(set(u["relevant_doc_ids"]) - llm_in)
                    miss_fact = [f["fact_id"] for f in u["required_facts"] if not f.get("pii_refs")
                                 and fact_status(f, doc_text) != "hit"]
                    ok12 = not miss_doc and not miss_fact
                    rows.append(result_row(**base, item_id="S12", content="뒤 청크의 사실이 LLM 입력에 포함",
                                           expected="정답 문서·필수 사실 모두 LLM 입력에 있음",
                                           actual=("모두 포함" if ok12 else f"빠진 문서 {','.join(miss_doc) or '없음'}, "
                                                   f"빠진 사실 {','.join(miss_fact) or '없음'}"),
                                           result=JUDGE_RESULT.get(judge_of["S12"]) or ("통과" if ok12 else "실패"),
                                           stage="LLM 입력(청크)"))
                a = agg[(cfg, c["split"], c["difficulty"])]
                a["runs"] += 1
                a["leak"] += bool(leaks)
                a["unsupported"] += bool(unsup)
                a["stray"] += bool(stray)
                a["error"] += st == "error"
                a["audit_bad"] += au is not None and not (au.get("rows") == 1 and au.get("user_ok") and au.get("state_ok"))
                a["recheck_action"] += (t.get("recheck") or {}).get("action") in ("masked", "blocked")
                exp = u["expected_state"].get(cfg) or []
                state_ok = st in exp
                facts = [f for f in u["required_facts"]]
                fst = [fact_status(f, ans) for f in facts]
                hits = [x == "hit" for x in fst]
                manual_f = [f["fact_id"] for f, x in zip(facts, fst) if x == "manual"]
                bst = {f["fact_id"]: forbid_status(f, ans, outputs) for f in u["forbidden_facts"]}
                forb = [k for k, v in bst.items() if v == "hit"]
                manual_x = [k for k, v in bst.items() if v == "manual"]
                rel = set(u["relevant_doc_ids"])
                a["n"] += 1
                a["state_ok"] += state_ok
                a["facts"] += len(facts)
                a["facts_hit"] += sum(hits)
                a["facts_manual"] += len(manual_f)
                a["forbid"] += bool(forb)
                if exp == ["clarify"] and st == "clarify":
                    a["clarify_tp"] += 1
                if "clarify" not in exp and st == "clarify":
                    a["clarify_fp"] += 1
                if exp == ["clarify"] and st != "clarify":
                    a["clarify_fn"] += 1
                definite_miss = [f for f, x in zip(facts, fst) if x == "miss"]
                ok = state_ok and not definite_miss and not forb and not leaks
                unsure = ok and bool(manual_f or manual_x)              # 실패 근거는 없지만 단정할 수 없음
                if not state_ok and ("clarify" in exp or st == "clarify"):
                    stage = "재질문"
                elif st == "error":
                    stage = "환경"
                elif leaks:
                    stage = "재검사"
                elif rel and not rel & set(returned):
                    stage = "임베딩 검색"
                elif definite_miss:
                    stage = "LLM 입력(청크)" if any(fact_status(f, doc_text) == "miss" and not f.get("pii_refs")
                                                  for f in definite_miss) else "LLM 답변"
                else:
                    stage = "LLM 답변"
                actual = (f"상태 {st}, 필수 {sum(hits)}/{len(facts)}, 금지 {len(forb)}"
                          + (f", 부정 표현과 함께 나옴 {', '.join(manual_f + manual_x)}" if manual_f or manual_x else "")
                          + (f", 유출 {len(leaks)}" if leaks else "") + (f", 근거 {','.join(returned)}" if returned else ""))
                expected = f"상태 {'/'.join(exp)}, 필수 {len(facts)}개, 금지 0"
                if "clarify" not in exp and st == "clarify":
                    rows.append(result_row(**base, item_id="X03", content=f"{lv}: 불필요한 재질문 없음", expected="clarify 아님",
                                           actual="clarify", result="실패", stage="재질문"))
                for chk in c["checks"]:
                    cid = chk["check_id"]
                    if not ANSWER_SCEN(cid):
                        continue
                    judge = chk["judgement"].get(cfg)
                    if judge is None:
                        continue
                    res = JUDGE_RESULT.get(judge)
                    if res is None:
                        res = (MANUAL if unsure else "통과") if ok else "실패"
                        if cid == "A04" and c["confusable_doc_ids"] and not set(c["confusable_doc_ids"]) & set(returned):
                            res = "조건 미충족"
                        if cid in ("G12", "G13"):
                            action = (t.get("recheck") or {}).get("action")
                            res = "실패" if leaks else ("통과" if action in ("masked", "blocked") else "미검증")
                    rows.append(result_row(**base, item_id=cid, content=f"{lv}: {c['variant_type']} ({c['intent']})",
                                           expected=expected, actual=actual, result=res, stage=stage,
                                           note=note or ("부정 표현 확인 필요" if res == MANUAL else "")))
    summary = {}
    for cfg, rs in run_state.items():
        summary.setdefault(cfg, collections.Counter())
    for (cfg, sp, diff), a in sorted(agg.items()):
        s = summary.setdefault(cfg, collections.Counter())
        s.update(a)
    for cfg, a in summary.items():
        summary[cfg] = dict(a)
        rs = run_state.get(cfg, {})
        summary[cfg]["실행"] = {k: rs.get(k, 0) for k in ("대상", "정상 실행", EXEC_ERROR, NOT_RUN)}
    by = collections.defaultdict(collections.Counter)
    for (cfg, sp, diff), a in agg.items():          # 전체 기준이 기본, split·난이도는 보조
        by[(cfg, "전체", "전체")].update(a)
        by[(cfg, "전체", diff)].update(a)
        by[(cfg, sp, "전체")].update(a)
    for (cfg, sp, diff), a in sorted(by.items()):
        q = f"답변 질문 ({diff})"
        if a["n"]:
            sets.append(set_row(q, sp, cfg, "A01", "상태 정확도", a["state_ok"] / a["n"]))
        if a["facts"]:
            sets.append(set_row(q, sp, cfg, "A01", "필수 사실 재현율", a["facts_hit"] / a["facts"]))
            if a["facts_manual"]:
                sets.append(set_row(q, sp, cfg, "A01", "수동 확인 필요 사실 수", a["facts_manual"]))
        sets.append(set_row(q, sp, cfg, "A03", "금지 사실 포함 건수", a["forbid"], "0", "통과" if a["forbid"] == 0 else "실패"))
        sets.append(set_row(q, sp, cfg, "X04", "원문 유출 실행 수", a["leak"], "0", "통과" if a["leak"] == 0 else "실패"))
        sets.append(set_row(q, sp, cfg, "X06", "감사 로그 이상 요청 수", a["audit_bad"], "0", "통과" if a["audit_bad"] == 0 else "실패"))
        if a["runs"]:
            sets.append(set_row(q, sp, cfg, "X05", "재검사 동작(마스킹·차단) 실행 수", a["recheck_action"], "", "기준선 기록"))
        tp, fp, fn = a["clarify_tp"], a["clarify_fp"], a["clarify_fn"]
        if tp + fp:
            sets.append(set_row(q, sp, cfg, "Q07", "재질문 Precision", tp / (tp + fp)))
        if tp + fn:
            sets.append(set_row(q, sp, cfg, "Q07", "재질문 Recall", tp / (tp + fn)))
    return rows, sets, summary
