"""03 영역(다중 턴): 대화 채점 (Q10·Q11·Q12·Q13 ↔ ITM01~05). 입력: traces/dialog_<설정>.jsonl

대화 전체를 한 건으로 채점한다 (엑셀 03 시트 '다중 턴 케이스'). 실행 단위 = 대화 × 설정(R1·R3) × 사용자 레벨.
정답: it_dialogs.jsonl 의 expect (1턴 기대, 최종 상태, 근거 문서, 필수 사실, 금지 사실). 정답지는 고치지 않는다.

  턴 수    : 정답지의 턴이 모두 실행돼야 한다 (덜 실행되면 통과로 보지 않고 실패)
  1턴      : 기대가 clarify 면 1턴 API 응답이 되묻기(need_more)여야 한다
  이어받기  : 1턴이 되물었으면 2턴 요청의 follow_up_of 가 1턴 request_id 와 같아야 한다 (없거나 다르면 실패)
  합치기    : 2턴이 빠진 조건만 채우는 조각인 시나리오(MERGE_SCENARIOS: Q10 필수 조건 보충, Q12 추가 정보 제공)는
             2턴 응답이 직전 질문과 합쳐서 찾았다(merged=True)여야 한다. Q11(2턴이 완전한 질문)·Q13(특정 불가)은 보지 않는다
  최종 상태 : 마지막 턴의 상태(chat 내부 state)가 기대 목록 안. 기대에 clarify 가 있으면 되묻기 응답(need_more)도 인정
  근거 문서 : 기대 문서가 있으면 마지막 턴 API 응답 문서에 전부 있고, 같은 이름의 다른 사람 문서는 없어야 한다.
             기대 문서가 없으면(특정 불가) 답변이 같은 이름 인물의 문서를 근거로 인용하지 않아야 한다
  필수·금지 : 질문 채점과 같은 비교 (score_answer.fact_status / forbid_status, 마지막 턴 답변 기준)
  X04      : 모든 턴의 API 응답 전체에 개인정보 원문 0 (기존 문서 포함). 검사할 응답이 없으면 미검증
  X06      : 턴마다 query_log 1행·사용자·레벨·상태·문서 일치·원문 0, 2턴이 이어받은 질문이면 follow_up_of = 1턴 request_id.
             검사할 감사 로그가 없으면 미검증
실행 오류·미실행은 오답으로 세지 않고 따로 남긴다.
"""
import collections
import pathlib

from .common import DIALOG_LEVELS, EXEC_ERROR, MANUAL, NOT_RUN, Gold, jl_read, latest, load_plan, result_row
from .score_answer import CITE, _ox, _strings, fact_status, forbid_status, raw_map

_GOLD = None
MERGE_SCENARIOS = ("Q10", "Q12")       # 2턴이 조각(빠진 조건만)이라 직전 질문과 합쳐야 답할 수 있는 시나리오


def _gold():
    global _GOLD
    if _GOLD is None:
        _GOLD = Gold()
    return _GOLD


def _named_persons(d, g):
    """대화 질문에 이름이 나오는 인물들 (같은 이름이면 모두)."""
    text = " ".join(d["turns"])
    return [p for p in g.persons.values() if p["name"] and p["name"] in text]


def _docs_of(p):
    return [x.strip() for x in (p.get("docs") or "").split(",") if x.strip()]


def dialog_split(d, g=None):
    """대화의 split: 기대 문서의 split, 없으면 질문에 나온 인물들의 split (다수결)."""
    g = g or _gold()
    docs = d["expect"].get("final_docs") or [x for p in _named_persons(d, g) for x in _docs_of(p)]
    c = collections.Counter(g.docs[x]["split"] for x in docs if x in g.docs)
    return c.most_common(1)[0][0] if c else ""


def _as_list(v):
    return [] if v is None else (list(v) if isinstance(v, (list, tuple)) else [v])


def judge(d, t, raw):
    """대화 기록 하나 → (시나리오 판정, 실제 설명, 실패 단계, X04 (판정, 설명), X06 (판정, 설명))"""
    g = _gold()
    exp = d["expect"]
    turns = t.get("turns") or []
    first, last = (turns[0], turns[-1]) if turns else ({}, {})
    checks, parts = [], []
    want_n = len(d["turns"])
    if len(turns) < want_n:                                         # 대화가 끝까지 실행되지 않음 → 통과로 보지 않는다
        checks.append(("대화 중단", False))
        parts.append(f"실행된 턴 {len(turns)}/{want_n}")
    if exp.get("turn1") == "clarify":
        ok1 = bool(first.get("need_more"))
        checks.append(("재질문", ok1))
        parts.append(f"1턴 되묻기 {_ox(ok1)}" + (f"({first.get('clarify_code')})" if first.get("clarify_code") else ""))
    if len(turns) > 1:
        second = turns[1]
        if first.get("need_more"):                                  # 되물었으면 2턴은 1턴 request_id 를 이어받아야 한다
            ok_fu = bool(second.get("follow_up_of")) and second.get("follow_up_of") == first.get("request_id")
            checks.append(("재질문·이어받기", ok_fu))
        else:
            ok_fu = None
        parts.append(f"2턴 이어받기 {_ox(ok_fu) if ok_fu is not None else '-'}, 합침 {_ox(second.get('merged'))}")
        if d["scenario"] in MERGE_SCENARIOS:                        # 조각 답은 직전 질문과 합쳐서 찾아야 한다
            checks.append(("재질문·이어받기", bool(second.get("merged"))))
    want = _as_list(exp.get("final_state"))
    st = last.get("state")
    ok_st = st in want or ("clarify" in want and bool(last.get("need_more")))
    checks.append(("재질문·이어받기", ok_st))
    parts.append(f"최종 상태 {st}" + (f"(되묻기 {last.get('clarify_code')})" if last.get("need_more") else ""))
    body = last.get("api_body") or {}
    answer = body.get("answer") or ""
    returned = [x.get("doc_id") for x in body.get("docs") or []]
    final_docs = _as_list(exp.get("final_docs"))
    other = {x for p in _named_persons(d, g) for x in _docs_of(p)} - set(final_docs)   # 같은 이름의 다른 사람 문서
    if final_docs:
        miss = sorted(set(final_docs) - set(returned))
        wrong = sorted(other & set(returned))
        checks.append(("검색", not miss and not wrong))
        parts.append(f"문서 {','.join(returned) or '없음'}" + (f" (빠짐 {','.join(miss)})" if miss else "")
                     + (f" (다른 사람 {','.join(wrong)})" if wrong else ""))
    else:
        cited = sorted(other & set(CITE.findall(answer)))
        checks.append(("LLM 답변", not cited))
        parts.append(f"특정 인물 인용 {','.join(cited) or '없음'}")
    outputs = " ".join(_strings(body))
    fs = [fact_status({"fact_id": f"F{i}", "desc": f, "match": [f]}, answer) for i, f in enumerate(exp.get("facts") or [])]
    if fs:
        checks.append(("LLM 답변", "manual" if "manual" in fs and "miss" not in fs else "miss" not in fs))
        parts.append(f"필수 사실 {fs.count('hit')}/{len(fs)}")
    bs = [forbid_status({"fact_id": f"B{i}", "desc": f, "match": [f]}, answer, outputs)
          for i, f in enumerate(exp.get("forbid") or [])]
    if bs:
        checks.append(("LLM 답변", "manual" if "manual" in bs and "hit" not in bs else "hit" not in bs))
        parts.append(f"금지 사실 {bs.count('hit')}건")
    bad = [stage for stage, ok in checks if ok is False]
    result = "실패" if bad else (MANUAL if any(ok == "manual" for _, ok in checks) else "통과")

    # X04: 모든 턴의 응답 전체
    allout = " ".join(s for tn in turns for s in _strings(tn.get("api_body") or {}))
    leaks = sorted(v for v, k in raw.items() if k == "pii" and v in allout)
    unsup = sorted(v for v, k in raw.items() if k == "unsupported" and v in allout)
    n_body = sum(1 for tn in turns if tn.get("api_body"))
    if not n_body:                                                  # 검사할 응답이 없으면 통과가 아니라 미검증
        x04 = ("미검증", "검사할 API 응답 없음")
    else:
        x04 = ("통과" if not leaks else "실패",
               f"응답 {n_body}개, 원문 {len(leaks)}건" + (f" (미지원 식별정보 {len(unsup)}건 별도)" if unsup else ""))
    # X06: 턴마다 감사 로그, 이어받은 턴은 follow_up_of
    au_ok, notes, unknown = True, [], False
    for tn in turns:
        au = tn.get("audit")
        if au is None:
            unknown = True
            notes.append(f"{tn.get('turn')}턴 기록 없음")
            continue
        ok = (au.get("rows") == 1 and au.get("user_ok") and au.get("level_ok") and au.get("state_ok")
              and au.get("docs_ok") and not au.get("raw_in_masked"))
        if tn.get("follow_up_of"):
            fu = au.get("follow_up_ok")
            if fu is None:
                unknown = True
                notes.append(f"{tn['turn']}턴 follow_up_of 확인 불가")
            elif not fu:
                ok = False
                notes.append(f"{tn['turn']}턴 follow_up_of 불일치")
        au_ok = au_ok and bool(ok)
        notes.append(f"{tn.get('turn')}턴 {au.get('rows')}행 {_ox(ok)}")
    if not turns or all(tn.get("audit") is None for tn in turns):   # 검사할 감사 로그가 하나도 없음
        x06 = ("미검증", ", ".join(notes) or "검사할 감사 로그 없음")
    else:
        x06 = ("실패" if not au_ok else ("미검증" if unknown else "통과"), ", ".join(notes))
    return result, " · ".join(parts), (bad[0] if bad else ""), x04, x06


def score(run_dir, run_id):
    run_dir = pathlib.Path(run_dir)
    g = _gold()
    plan = load_plan(run_dir)
    if plan is not None:
        targets = plan.get("dialog") or {}
    else:
        tdir = run_dir / "traces"
        targets = {p.stem.split("_", 1)[1]: {d["id"] for d in g.dialogs}
                   for p in sorted(tdir.glob("dialog_*.jsonl"))} if tdir.exists() else {}
    if not targets:
        return [], [], {"skipped": "대화 실행 대상 없음 (--only dialog·all 이고 R1·R3 가 실행 설정에 있어야 함)"}
    raw = raw_map(run_dir, g)
    rows, summary = [], {}
    for cfg, ids in sorted(targets.items()):
        p = run_dir / "traces" / f"dialog_{cfg}.jsonl"
        tr = latest(jl_read(p), "key") if p.exists() else {}
        cnt = collections.Counter()
        for d in g.dialogs:
            if d["id"] not in ids:
                continue
            title = (g.scenarios.get(d["scenario"]) or {}).get("title", "")
            exp = d["expect"]
            expected = (f"1턴 {exp.get('turn1')} → 최종 {'/'.join(_as_list(exp.get('final_state')))}, "
                        f"문서 {','.join(_as_list(exp.get('final_docs'))) or '특정 안 함'}, 사실 {len(exp.get('facts') or [])}개")
            for lv in DIALOG_LEVELS:
                t = tr.get(f"{d['id']}|{lv}")
                base = dict(unit="대화", run_id=d["id"], split=dialog_split(d, g), diff="", cfg=cfg, level=lv)
                content = f"{lv}: {len(d['turns'])}턴 대화 ({title})"
                cnt["대상"] += 1
                if t is None or "exec_error" in t:
                    res_ = EXEC_ERROR if t else NOT_RUN
                    cnt[res_] += 1
                    e = (t or {}).get("exec_error") or {}
                    why = (f"{e.get('kind')} HTTP {e.get('status')} {e.get('code') or ''} {e.get('message', '')[:80]}"
                           if t else "실행 기록 없음")
                    for sid in (d["scenario"], "X04", "X06"):
                        rows.append(result_row(**base, item_id=sid, content=content, expected="", actual=why, result=res_))
                    continue
                cnt["정상 실행"] += 1
                if t.get("error"):
                    rows.append(result_row(**base, item_id=d["scenario"], content=content, expected=expected,
                                           actual=f"오류: {t['error']}", result="실패", stage="환경"))
                    continue
                result, actual, stage, x04, x06 = judge(d, t, raw)
                cnt[result] += 1
                rows.append(result_row(**base, item_id=d["scenario"], content=content, expected=expected, actual=actual,
                                       result=result, stage=stage, note=d.get("note", "")))
                rows.append(result_row(**base, item_id="X04", content=f"{d['id']} 대화 응답 원문 유출", expected="0건",
                                       actual=x04[1], result=x04[0], stage="재검사"))
                rows.append(result_row(**base, item_id="X06", content=f"{d['id']} 대화 감사 로그",
                                       expected="턴마다 1행·일치, 이어받은 턴 follow_up_of", actual=x06[1], result=x06[0],
                                       stage="감사 로그"))
        summary[cfg] = {"실행": {k: cnt.get(k, 0) for k in ("대상", "정상 실행", EXEC_ERROR, NOT_RUN)},
                        "판정": {k: cnt.get(k, 0) for k in ("통과", "실패", MANUAL)}}
    return rows, [], summary
