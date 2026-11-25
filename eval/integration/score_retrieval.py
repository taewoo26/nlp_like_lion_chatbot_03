"""04 영역: 검색 채점. 지표 계산은 저장소 eval/eval_holdout_retrieval.py 의 score() 를 그대로 쓴다.

입력: traces/search_<설정>.jsonl (k=20, LLM 없음). 검색 대상은 기존 문서 + 통합 테스트 84건 전체.
질문 집합 지표: 설정 × (전체 / 난이도 / split) 별 Hit@1, Recall@3, MRR, Confuse@3. 기본은 '전체'.
  R0~R3 비교용으로 '공통 질문 집합'(실행 대상 모든 설정에서 정상 실행된 질문) 지표를 따로 낸다.
질문별 S 시나리오 판정 (SCENARIO 표). ranked = 상대 임계값까지 통과한 최종 검색 결과, 중립 문서(neutral)는 빼고 본다.
  S01 단일 정답      정답 문서 1위
  S02 복합 식별자    정답 문서 1위이고 다른 인물 조합 문서(혼동)가 상위 3위에 없음. 정답이 없으면 혼동 문서 0
  S03 복수 정답      정답 문서 전부 상위 3위 (Recall@3 = 1)
  S04 표현 변형      Recall@3 기록 → 설정 비교는 질문 집합 지표 (기준선 기록)
  S05 키워드 일치    Hit@1 기록 → 설정 비교는 질문 집합 지표 (기준선 기록)
  S06 다른 사람 문서  혼동 문서가 상위 3위에 없음 (Confuse@3 = 0)
  S07 같은 사람·다른 목적  요청 목적 문서(정답) 1위
  S08 개인정보 없는 주제  정답 전부 상위 3위, 혼동 문서 순위 기록
  S09 문서 유형      1위 문서 유형이 요청 유형(정답 문서 유형)과 같은지 기록 (기준선 기록)
  S10 개수 제한      Precision@3·반환 수·빠진 정답 수 기록 (기준선 기록)
  S11 정답 없음      반환 문서 수 기록 (기준선 기록). '무관 문서를 근거로 쓰지 않음'은 score_answer 가 판정
  S12 청크 분산      정답 문서 상위 3위. '뒤 청크 사실이 LLM 입력에 포함'은 score_answer 가 판정
  S13 동명이인      정답 문서 1위, 혼동 문서가 상위 3위에 없음
  S14 최신·과거      정답(요청 시점) 문서 1위
X07 BM25 정답 탈락: BM25 켠 설정에서 BM25 적용 전 후보에 있던 정답 문서가 BM25 합산·컷오프 뒤 빠졌는지
  (실행 때 남긴 probe.before_bm25 / after_bm25 로 직접 센다).
실패 원인 단계: 실행 기록의 단계별 후보로 정한다 — BM25 전 후보에 없음 '임베딩 검색', BM25 뒤 빠짐 'BM25·컷오프',
  임계값 뒤 빠짐 '상대 임계값', 남았지만 순위가 낮음 '재정렬·순위'.
실행 대상 목록(plan) 기준으로 실행 오류·미실행 행을 남기고 지표에서 뺀다.
"""
import collections

from .common import (DATA, EXEC_ERROR, JUDGE_RESULT, NOT_RUN, CONFIGS, Gold, csv_read, jl_read, latest, plan_targets,
                     result_row, set_row)

BM25_ON = {c for c, v in CONFIGS.items() if v.get("BM25_WEIGHT", "0.3") != "0"}


def _gold_row(c):
    return {"qid": c["qid"], "type": c["variant_type"], "relevant_doc_ids": c["relevant_doc_ids"],
            "confusable_doc_ids": c["confusable_doc_ids"], "neutral_doc_ids": c["neutral_doc_ids"]}


def _doc_types():
    return {d["doc_id"]: d["doc_type"] for d in csv_read(DATA / "it_source" / "docs.csv")}


def judge_scenario(sid, c, ranked, doc_type=None):
    """시나리오별 판정 → (ok: True/False/None(기록만), 기대, 실제)"""
    rel, conf = set(c["relevant_doc_ids"]), set(c["confusable_doc_ids"])
    top1, top3 = (ranked[0] if ranked else None), ranked[:3]
    conf3 = [d for d in top3 if d in conf]
    show = ",".join(top3) or "없음"
    if sid in ("S01", "S07", "S14"):
        return top1 in rel, f"정답 문서 1위 ({','.join(sorted(rel))})", f"1위 {top1 or '없음'}"
    if sid in ("S02", "S13"):
        if not rel:
            return not conf3, "혼동 문서 상위 3위 0건", f"상위3 {show}"
        ok = top1 in rel and not conf3
        return ok, "정답 1위, 혼동 문서 상위 3위 0건", f"1위 {top1 or '없음'}, 혼동 {','.join(conf3) or '0건'}"
    if sid in ("S03", "S12"):
        miss = sorted(rel - set(top3))
        return not miss, "정답 문서 전부 상위 3위", f"상위3 {show}" + (f", 빠짐 {','.join(miss)}" if miss else "")
    if sid == "S06":
        return not conf3, "혼동 문서 상위 3위 0건 (Confuse@3 = 0)", f"혼동 {','.join(conf3) or '0건'} / 상위3 {show}"
    if sid == "S08":
        miss = sorted(rel - set(top3))
        conf_rank = {d: ranked.index(d) + 1 for d in conf if d in ranked}
        return not miss, "정답 전부 상위 3위 (혼동 순위 기록)", (f"상위3 {show}" + (f", 빠짐 {','.join(miss)}" if miss else "")
                                                         + f", 혼동 순위 {conf_rank or '없음'}")
    if sid == "S04":
        r3 = len(rel & set(top3)) / len(rel) if rel else 0.0
        return None, "Recall@3 기록 (설정 비교는 질문 집합 지표)", f"Recall@3 {r3:.2f}"
    if sid == "S05":
        return None, "Hit@1 기록 (설정 비교는 질문 집합 지표)", f"Hit@1 {int(top1 in rel)}"
    if sid == "S09":
        want = set(c.get("expected_doc_types") or []) or {doc_type.get(d) for d in rel if doc_type and d in doc_type}
        got = doc_type.get(top1) if (doc_type and top1) else None
        return None, f"1위 유형 = {'/'.join(sorted(x for x in want if x)) or '?'}", f"1위 {top1 or '없음'} ({got or '유형 모름'}) → {'일치' if got in want else '불일치'}"
    if sid == "S10":
        p3 = len(rel & set(top3)) / 3
        return None, "Precision@3·반환 수·빠진 정답 기록", f"P@3 {p3:.2f}, 반환 {len(ranked)}, 빠진 정답 {len(rel - set(ranked))}"
    if sid == "S11":
        return None, "반환 문서 수 기록 (근거 사용 여부는 답변에서 판정)", f"반환 {len(ranked)}건 (상위3 {show})"
    return None, "", f"상위3 {show}"


def fail_stage(c, t, cfg):
    """실패한 검색이 어느 단계에서 정답을 잃었는지 (실행 때 남긴 단계별 후보 기준)."""
    rel = set(c["relevant_doc_ids"])
    pr = t.get("probe") or {}
    if t.get("error"):
        return "환경"
    before = {d for d, _ in pr.get("before_bm25") or []}
    after_b = set(pr.get("after_bm25") or [])
    after_t = set(pr.get("after_threshold") or t.get("ranked_doc_ids") or [])
    if not pr:
        return "임베딩 검색" if not rel & after_t else "재정렬·순위"
    if rel and not rel & before:
        return "임베딩 검색"
    if rel & before and not rel & after_b:
        return "BM25·컷오프" if pr.get("bm25_applied") else "재정렬·순위"
    if rel & after_b and not rel & after_t:
        return "상대 임계값"
    return "재정렬·순위"


def score(run_dir, run_id):
    import eval.eval_holdout_retrieval as er
    g = Gold()
    targets = plan_targets(run_dir, "search", g.cases)
    if not targets:
        return [], [], {"skipped": "검색 실행 대상 없음"}
    doc_type = _doc_types()
    rows, sets, summary = [], [], {}
    traces, ok_q = {}, {}
    for cfg, cases in targets.items():
        p = run_dir / "traces" / f"search_{cfg}.jsonl"
        tr_all = latest(jl_read(p)) if p.exists() else {}
        tr = {q: t for q, t in tr_all.items() if not t.get("exec_error")}       # 정상 실행된 질문만 채점
        traces[cfg] = tr_all
        ok_q[cfg] = {c["qid"] for c in cases if c["qid"] in tr}
        summary[cfg] = {"실행": {"대상": len(cases), "정상 실행": len(ok_q[cfg]),
                                 EXEC_ERROR: sum(c["qid"] in tr_all and c["qid"] not in tr for c in cases),
                                 NOT_RUN: sum(c["qid"] not in tr_all for c in cases)}}
        run = [{"qid": q, "ranked_doc_ids": t.get("ranked_doc_ids") or []} for q, t in tr.items()]
        groups = [("전체", "전체", lambda c: True)]
        groups += [("전체", d, (lambda d: lambda c: c["difficulty"] == d)(d)) for d in ("Easy", "Medium", "Hard")]
        groups += [(sp, "전체", (lambda sp: lambda c: c["split"] == sp)(sp)) for sp in ("dev", "check")]
        for sp, diff, keep in groups:
            gold = [_gold_row(c) for c in cases if c["qid"] in tr and c["relevant_doc_ids"] and keep(c)]
            if not gold:
                continue
            m = er.score(gold, run, ks=(1, 3, 5, 10))["ALL"]
            avg = {k: (sum(v) / len(v) if v else None) for k, v in m.items() if k != "n"}
            vals = {"n": len(gold), "Hit@1": avg["Hit@1"], "Recall@3": avg["Recall@3"], "MRR": avg["MRR"],
                    "Confuse@3": avg.get("Confuse@3")}
            summary[cfg][f"{sp}/{diff}"] = {k: (round(v, 3) if isinstance(v, float) else v) for k, v in vals.items()}
            for k in ("Hit@1", "Recall@3", "MRR", "Confuse@3"):
                if vals[k] is not None:
                    sets.append(set_row(f"검색 질문 ({diff}, {vals['n']}개)", sp, cfg, "S01", k, vals[k]))
        errs = sum(1 for t in tr.values() if t.get("error"))
        if errs:
            summary[cfg]["errors"] = errs

    # R0~R3 비교: 모든 설정에서 정상 실행된 같은 질문 집합
    common = set.intersection(*ok_q.values()) if ok_q else set()
    if len(ok_q) > 1 and common:
        summary["공통 질문 집합"] = {"n": len([c for c in g.cases if c["qid"] in common and c["relevant_doc_ids"]])}
        for cfg in targets:
            tr = traces[cfg]
            run = [{"qid": q, "ranked_doc_ids": tr[q].get("ranked_doc_ids") or []} for q in common]
            gold = [_gold_row(c) for c in g.cases if c["qid"] in common and c["relevant_doc_ids"]]
            if not gold:
                continue
            m = er.score(gold, run, ks=(1, 3))["ALL"]
            avg = {k: sum(v) / len(v) for k, v in m.items() if k != "n" and v}
            summary["공통 질문 집합"][cfg] = {k: round(avg[k], 3) for k in ("Hit@1", "Recall@3", "MRR") if k in avg}
            for k in ("Hit@1", "Recall@3", "MRR"):
                if k in avg:
                    sets.append(set_row(f"공통 질문 집합 ({len(gold)}개)", "전체", cfg, "S01", k, avg[k]))

    # 질문별 시나리오 판정 + X07
    x07 = collections.Counter()
    for cfg, cases in targets.items():
        tr_all = traces[cfg]
        for c in cases:
            t = tr_all.get(c["qid"])
            base = dict(unit="질문", run_id=c["qid"], split=c["split"], diff=c["difficulty"], cfg=cfg)
            schecks = [k for k in c["checks"] if k["check_id"].startswith("S") and cfg in k["judgement"]]
            if t is None or t.get("exec_error"):
                e = (t or {}).get("exec_error") or {}
                why = f"{e.get('kind')} {e.get('code') or ''} {e.get('message', '')[:80]}" if t else "실행 기록 없음"
                for chk in schecks:
                    rows.append(result_row(**base, item_id=chk["check_id"], content=f"{c['variant_type']}: 검색",
                                           expected="", actual=why, result=EXEC_ERROR if t else NOT_RUN))
                continue
            neutral = set(c["neutral_doc_ids"])
            ranked = [d for d in (t.get("ranked_doc_ids") or []) if d not in neutral]
            for chk in schecks:
                sid = chk["check_id"]
                ok, expected, actual = judge_scenario(sid, c, ranked, doc_type)
                if t.get("error"):
                    actual += f" (오류 {t['error']})"
                res = JUDGE_RESULT.get(chk["judgement"][cfg])
                if res is None:
                    res = "기준선 기록" if ok is None else ("통과" if ok else "실패")
                stage = fail_stage(c, t, cfg) if res == "실패" else ""
                rows.append(result_row(**base, item_id=sid, content=f"{c['variant_type']}: {chk['expected'][:40]}",
                                       expected=expected, actual=actual, result=res, stage=stage))
            # X07: BM25 단계에서 정답 탈락
            pr = t.get("probe") or {}
            rel = set(c["relevant_doc_ids"])
            if cfg in BM25_ON and rel:
                if "before_bm25" not in pr:
                    rows.append(result_row(**base, item_id="X07", content="BM25 적용 뒤 정답 탈락", expected="0건",
                                           actual="BM25 단계 후보 기록 없음 (옛 실행이거나 지역 되묻기 등으로 BM25 전에 끝남)",
                                           result="미검증"))
                    continue
                before = {d for d, _ in pr.get("before_bm25") or []}
                dropped = sorted((rel & before) - set(pr.get("after_bm25") or []))
                x07[(cfg, "대상")] += bool(rel & before)
                x07[(cfg, "탈락")] += bool(dropped)
                rows.append(result_row(**base, item_id="X07", content="BM25 적용 뒤 정답 탈락", expected="0건",
                                       actual=(f"탈락 {','.join(dropped)}" if dropped else "탈락 없음")
                                       + ("" if pr.get("bm25_applied") else " (키워드 없어 BM25 미적용)"),
                                       result="실패" if dropped else "통과", stage="BM25·컷오프"))
    for cfg in targets:
        if x07[(cfg, "대상")]:
            sets.append(set_row("BM25 전 후보에 정답이 있던 질문", "전체", cfg, "X07", "BM25 뒤 정답 탈락 비율",
                                x07[(cfg, "탈락")] / x07[(cfg, "대상")]))
            summary[cfg]["X07 탈락"] = f"{x07[(cfg, '탈락')]}/{x07[(cfg, '대상')]}"
    return rows, sets, summary
