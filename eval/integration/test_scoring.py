"""채점 로직 테스트 (DB·LLM 없이). 잘못된 답·결과가 통과로 처리되지 않는지 확인한다.

    python -m eval.integration.test_scoring        # 저장소 루트에서
    pytest eval/integration/test_scoring.py        # pytest 가 있으면
"""
import json
import os
import tempfile
import pathlib

os.environ.setdefault("HMAC_KEY", "0" * 64)           # 토큰 계산용 (테스트 전용 키)

from .common import DATA, Gold, jl_write  # noqa: E402
from . import score_answer as SA, score_query as SQ, score_retrieval as SR, score_detection as SD  # noqa: E402
from . import score_person as SP, report as RP, security as SEC  # noqa: E402


# ---------------------------------------------------------------- 답변 사실 비교
def test_answer_wrong_not_credited():
    M = SA.match_status
    cases = [("승인", "승인되지 않았습니다.", "neg"), ("승인", "승인 안 됨", "neg"), ("승인", "미승인 상태", "neg"),
             ("500만", "1500만 원입니다.", "miss"), ("3천만", "13,000만 원", "miss"), ("1억 8천", "1억 800만", "miss"),
             ("11월 17일", "11월 7일", "miss"), ("10월 2일", "10월 22일", "miss"), ("36개월", "136개월", "miss"),
             ("0.5", "0.55%", "miss"), ("30일", "130일", "miss"), ("정지", "정지되지 않았습니다", "neg"),
             ("지연", "지연이 없었습니다", "neg")]
    for m, text, want in cases:
        assert M(m, text) == want, (m, text, M(m, text))


def test_answer_right_credited():
    M = SA.match_status
    cases = [("승인", "승인되었습니다."), ("3천만", "신청 금액은 3,000만 원"), ("3,000만", "3천만원"),
             ("1억 8천", "1억 8천만 원"), ("11월 17일", "2026-11-17에 완료"), ("11-17", "11월 17일"),
             ("9월 30일", "2026.09.30"), ("12월", "12월 3일 예정"), ("36개월", "36개월"), ("0.5", "0.5%p"),
             ("정지", "정지 사유는 확인되지 않았습니다"), ("아직", "아직 처리되지 않았습니다"), ("일주일", "일주일 안에 발송")]
    for m, text in cases:
        assert M(m, text) == "hit", (m, text, M(m, text))


def test_fact_status_and_negative_fact():
    f = {"fact_id": "F1", "desc": "해당 은행 회신 없음", "match": ["회신"]}
    assert SA.fact_status(f, "은행 회신이 없어 정지하지 못했습니다") == "hit"
    f2 = {"fact_id": "F1", "desc": "승인", "match": ["승인"]}
    assert SA.fact_status(f2, "승인되지 않았습니다") == "manual"     # 단정 불가 → 수동 확인 (통과 아님)
    assert SA.fact_status(f2, "거절되었습니다") == "miss"


def test_gold_facts_found_in_source_docs():
    """정답지의 필수 사실은 정답 문서 원문에서 모두 '일치'여야 한다 (판정기가 너무 엄격하지 않은지)."""
    g = Gold()
    bad = []
    for c in g.cases:
        for lv, u in c["users"].items():
            text = "\n".join(g.docs[d]["text"] for d in u["relevant_doc_ids"] if d in g.docs)
            for f in u["required_facts"]:
                if text and not f.get("pii_refs") and SA.fact_status(f, text) != "hit":
                    bad.append((c["qid"], lv, f["fact_id"]))
    assert not bad, bad[:5]


# ---------------------------------------------------------------- 질문 처리
def test_query_negation():
    assert not SQ.negation_kept("안 된", {"안내", "미팅", "계좌"})[0]          # 글자만 겹치는 말은 부정 아님
    assert not SQ.negation_kept("완료되지 않은", {"않은", "재발급"})[0]        # 어간이 사라지면 실패
    assert SQ.negation_kept("완료되지 않은", {"완료", "않은"})[0]
    assert SQ.negation_kept("안 된", {"안된", "정지"})[0]


def test_query_pairs_align_with_unsupported():
    c = {"expected_query_entities": [{"label": "customer_id", "text": "C-1"},
                                     {"label": "phone_number", "text": "010-1234-5678"}]}
    pairs = SQ.expected_pairs(c)
    assert len(pairs) == 1 and pairs[0][0]["label"] == "phone_number"


# ---------------------------------------------------------------- 검색 시나리오
def _c(rel, conf=(), types=()):
    return {"relevant_doc_ids": list(rel), "confusable_doc_ids": list(conf), "neutral_doc_ids": [],
            "expected_doc_types": list(types)}


def test_retrieval_scenarios():
    J = SR.judge_scenario
    assert J("S01", _c(["A"]), ["B", "A"])[0] is False
    assert J("S01", _c(["A"]), ["A", "B"])[0] is True
    assert J("S03", _c(["A", "B"]), ["A", "C", "D", "B"])[0] is False
    assert J("S06", _c(["A"], ["X"]), ["A", "B", "X"])[0] is False                # 혼동 문서가 상위 3위
    assert J("S06", _c(["A"], ["X"]), ["A", "B", "C", "X"])[0] is True
    assert J("S02", _c(["A"], ["X"]), ["A", "X"])[0] is False
    assert J("S13", _c(["A"], ["X"]), ["X", "A"])[0] is False
    assert J("S14", _c(["NEW"]), ["OLD", "NEW"])[0] is False
    assert J("S11", _c([]), ["A"])[0] is None                                      # 기록만 (답변에서 판정)
    t = {"probe": {"before_bm25": [["A", 0.9], ["B", 0.8]], "after_bm25": ["B"], "after_threshold": ["B"],
                   "bm25_applied": True}, "ranked_doc_ids": ["B"]}
    assert SR.fail_stage(_c(["A"]), t, "R2") == "BM25·컷오프"
    t2 = {"probe": {"before_bm25": [["B", 0.9]], "after_bm25": ["B"], "after_threshold": ["B"]}, "ranked_doc_ids": ["B"]}
    assert SR.fail_stage(_c(["A"]), t2, "R2") == "임베딩 검색"


# ---------------------------------------------------------------- 원문 잔존
def test_residue():
    R = SD.residue_kind
    assert R("010-1234-5678", "연락처 010-1234-5678") == "원문"
    assert R("010-1234-5678", "연락처 010 1234 5678") == "표기 변형"
    assert R("010-1234-5678", "연락처 <PHONE_NUMBER_ab12cd34ef56>, 2026-11-17 방문, 상담 12345") is None
    assert R("352-481920-37", "계좌 352-<ACCOUNT_NUMBER_x1> 끝 481920-37") == "일부"   # 일부만 가린 경우도 잔존
    assert R("한지석", "고객 <PERSON_abc123def456>") is None


# ---------------------------------------------------------------- 인물 판별
def test_person_bcubed_penalizes():
    perfect = [("c1", "PS01"), ("c1", "PS01"), ("c2", "PS02")]
    assert SP.bcubed(perfect) == (1.0, 1.0)
    merged_ho = [("c1", "PS01"), ("c1", "HO"), ("c2", "PS02")]                     # 기존 문서 인물과 병합
    assert SP.bcubed(merged_ho)[0] < 1.0
    missed = [("c1", "PS01"), ("miss:x", "PS01"), ("c2", "PS02")]                  # 놓친 언급은 재현율 하락
    assert SP.bcubed(missed)[1] < 1.0


# ---------------------------------------------------------------- 권한 API 값 검증
def test_security_values():
    assert SEC.mask_name("윤서영") == "윤*영" and SEC.mask_name("윤서") == "윤*" and SEC.mask_name("남궁민수") == "남**수"

    class X:
        raw = {"윤서영"}
        truth = {"<PERSON_a>": "윤서영", "<PHONE_NUMBER_b>": "010-1234-5678"}
    x = X()
    ok, _ = SEC.Ctx.check_partial(x, {"<PERSON_a>": "윤*영"}, ["<PERSON_a>"])
    assert ok
    assert not SEC.Ctx.check_partial(x, {"<PERSON_a>": "윤**"}, ["<PERSON_a>"])[0]       # 형식 틀림
    assert not SEC.Ctx.check_partial(x, {"<PERSON_a>": "윤*영"}, [])[0]                  # 요청 밖 토큰
    assert not SEC.Ctx.check_partial(x, {"<PHONE_NUMBER_b>": "010-****"}, ["<PHONE_NUMBER_b>"])[0]   # 이름 아님
    assert SEC.Ctx.check_full(x, {"<PHONE_NUMBER_b>": "01012345678"}, ["<PHONE_NUMBER_b>"])[0]
    assert not SEC.Ctx.check_full(x, {"<PHONE_NUMBER_b>": "010-9999-0000"}, ["<PHONE_NUMBER_b>"])[0]


# ---------------------------------------------------------------- 보고서 판정·완료율
def test_security_verdict_requires_everything():
    req = ["ITP01", "ITP02"]
    passed = [{"id": "ITP01", "result": "통과", "x04": "통과", "x06": "통과"},
              {"id": "ITP02", "result": "통과", "x04": "통과", "x06": "통과"}]
    v = RP._security_verdict(passed, [], req, (0, 100))["verdict"]
    assert v.startswith("검증 미완료"), v                                     # 답변 유출 검사가 안 끝남
    v = RP._security_verdict(passed[:1], [], req, (100, 100))["verdict"]
    assert v.startswith("검증 미완료"), v                                     # 필수 케이스 미실행
    v = RP._security_verdict([{"id": "ITP01", "result": "통과", "x04": "통과", "x06": "미검증"}, passed[1]], [], req,
                             (100, 100))["verdict"]
    assert v.startswith("검증 미완료"), v                                     # 감사 로그(X06) 미검증
    v = RP._security_verdict([{"id": "ITP01", "result": "미검증"}, passed[1]], [], req, (100, 100))["verdict"]
    assert v.startswith("검증 미완료"), v
    v = RP._security_verdict([{"id": "ITP01", "result": "실패"}], [], req, (0, 100))["verdict"]
    assert v == "미충족", v
    v = RP._security_verdict(passed, [], req, (100, 100))["verdict"]
    assert v.startswith("조건부 충족"), v


def test_plan_missing_config_counts_as_not_run():
    g = Gold()
    with tempfile.TemporaryDirectory() as d:
        d = pathlib.Path(d)
        (d / "traces").mkdir()
        q = [c["qid"] for c in g.cases if "R0" in c["search_configs"]][:5]
        q1 = [c["qid"] for c in g.cases if "R1" in c["search_configs"]][:5]
        (d / "manifest.json").write_text(json.dumps({"plan": {"search": {"R0": q, "R1": q1}, "answer": {}, "security": []}}))
        jl_write(d / "traces" / "search_R0.jsonl", [{"qid": x, "ranked_doc_ids": []} for x in q])
        _, _, s = SR.score(d, "t")
        assert s["R1"]["실행"]["미실행"] == len(q1) and s["R1"]["실행"]["정상 실행"] == 0   # 파일 없는 설정도 미실행
        assert s["R0"]["실행"]["정상 실행"] == len(q)


def test_coverage_lists_all_scenarios():
    g = Gold()
    cov = RP._coverage([], g)
    assert len(cov) == len(g.scenarios) + 7
    assert {c["ID"] for c in cov if c["상태"] == "채점 미구현"} == {"G01", "G02", "G16"}   # Q12·Q13 은 대화 채점(score_dialog)


# ---------------------------------------------------------------- 2차 검토 반례
def test_answer_review2_counterexamples():
    M, F = SA.match_status, SA.fact_status
    assert M("2026년 11월 17일", "2025년 11월 17일") == "miss"                  # 연도가 다르면 불일치
    assert M("2026년 11월 17일", "2026-11-17 완료") == "hit"
    assert M("500만", "500만 원이 아니라 1500만 원") == "neg"                     # 부정된 금액은 정답 아님
    assert M("11월 17일", "11월 17일이 아니라 18일") == "neg"
    assert M("36개월", "36개월이 아닌 48개월") == "neg"
    assert M("승인", "승인 예정입니다") == "neg"                                  # 예정·검토 중은 완료가 아님
    assert M("승인", "승인 검토 중입니다") == "neg"
    neg_fact = {"desc": "해당 은행 회신 없음", "match": ["회신"]}
    assert F(neg_fact, "은행 회신을 받았습니다") == "manual"                      # 부정 사실과 반대되는 답
    assert F(neg_fact, "은행 회신이 없어 정지하지 못했습니다") == "hit"
    assert F({"desc": "인증 서버 설정 값 잘못 배포", "match": ["설정"]}, "설정 값이 잘못 배포") == "hit"   # '잘못'은 부정 아님
    assert F({"desc": "승인 예정", "match": ["승인"]}, "승인 예정입니다") == "hit"                   # 사실 자체가 예정


def test_security_missing_and_unknown():
    class X:
        raw = set()
        truth = {"<PERSON_a>": "윤서영", "<PHONE_NUMBER_b>": "010-1234-5678"}
    x = X()
    req = ["<PERSON_a>", "<PHONE_NUMBER_b>", "<EMAIL_c>"]
    assert SEC.Ctx.check_full(x, {"<PERSON_a>": "윤서영"}, req)[0] is False             # 3개 요청, 1개만 반환
    assert SEC.Ctx.check_full(x, {"<EMAIL_c>": "a@b.c"}, ["<EMAIL_c>"])[0] is None      # 정답 모름 → 통과 아님
    assert SEC.Ctx.check_partial(x, {}, ["<PERSON_a>"])[0] is False                     # 이름 누락
    assert SEC.Ctx.check_partial(x, {"<PERSON_z>": "김*수"}, ["<PERSON_z>"])[0] is None
    assert SEC._combine(True, None) is None and SEC._combine(False, True) is False and SEC._combine(True, True) is True


def test_security_complete_needs_x04_x06_rows():
    g = Gold()
    done, total = RP.answer_coverage([], g)
    assert done == 0 and total == 472                                              # R0~R3 질문 × 사용자 레벨 (엑셀 tblQSec 과 같음)
    c = next(c for c in g.cases if "R0" in c["answer_configs"])
    lv = next(iter(c["users"]))
    row = lambda sid: {"평가 항목 ID": sid, "실행 단위": "질문", "실행 설정": "R0", "실행 ID": c["qid"],
                       "실행 권한·계정": lv, "결과": "통과"}
    assert RP.answer_coverage([row("X04")], g)[0] == 0                             # X06 이 없으면 완료 아님
    assert RP.answer_coverage([row("X04"), row("X06")], g)[0] == 1
    fails, pend, n = RP.question_security_extra([], g)
    assert n == 32 and len(pend) == 32                                             # G12·G13·G14·G15 (엑셀 추가 검사 판정)


def test_excel_formats():
    from .common import EXCEL_RESULT_COLS, EXCEL_SET_COLS, result_row, set_row
    r = result_row("질문", "ITQ001", "dev", "Easy", "R2", "X04", "내용", "0건", "0건", "통과", level="Lv1")
    x = RP.to_excel_rows([r], "run1")[0]
    assert list(x) == EXCEL_RESULT_COLS and x["검증 키"] == "run1|ITQ001|R2|Lv1|X04" and x["케이스 ID"] == "ITQ001"
    y = RP.to_excel_sets([set_row("검색 질문 (Hard, 3개)", "전체", "R2", "S01", "Hit@1", 0.5)], "run1")[0]
    assert list(y) == EXCEL_SET_COLS and y["난이도"] == "Hard" and y["실행 ID"] == "run1"
    v = RP._security_verdict([], [], ["ITP01"], (472, 472), ([("R2", "ITQ100", "Lv1", "G15")], [], 32))["verdict"]
    assert v == "미충족", v                                                        # 질문 추가 보안 검사 실패도 미충족


def _perfect_load(g, drop=None):
    pseudo, masked = [], []
    for doc in g.docs.values():
        ents, text = [], doc["text"]
        for e in sorted(doc["entities"], key=lambda e: -e["start"]):
            if e["label"] in ("passport_number", "customer_id", "employee_id"):
                continue
            tok = None if e["label"] == "driver_license" else SD.token_of(e["label"], e["text"])
            ents.append({"label": e["label"], "start": e["start"], "end": e["end"], "token": tok})
            text = text[:e["start"]] + (tok or SD.DELETED) + text[e["end"]:]
        pseudo.append({"id": doc["id"], "entities": ents})
        if doc["id"] != drop:
            masked.append({"doc_id": doc["id"], "chunk_idx": 0, "masked_text": text, "tokens": []})
    return pseudo, masked


def test_detection_missing_masked_not_pass():
    g = Gold()
    with tempfile.TemporaryDirectory() as d:
        d = pathlib.Path(d)
        pseudo, masked = _perfect_load(g)
        jl_write(d / "pseudo_off.jsonl", pseudo)
        jl_write(d / "masked_off.jsonl", masked)
        rows, _, _ = SD.score(d, "t")
        res = {r["평가 항목 ID"]: r["결과"] for r in rows if r["split"] == "전체"}
        assert res["P02"] == "통과" and res["P03"] == "통과" and res["P05"] == "통과"
        pseudo, masked = _perfect_load(g, drop="IT0001")
        jl_write(d / "masked_off.jsonl", masked)
        rows, _, _ = SD.score(d, "t")
        res = {r["평가 항목 ID"]: r["결과"] for r in rows if r["split"] == "전체"}
        assert res["P02"] == "미검증", res                                         # 본문이 빠지면 통과 아님


def test_person_mention_level_and_scenario_fail():
    g = Gold()
    def rows_from(mentions, merge_ho=False):
        rows = [{"person_id": f"p{i}", "reg_id": m["person_id"], "info_label": [], "doc_id": m["doc_id"],
                 "evidence": {"name_spans": [[m["start"], m["end"]]]}} for i, m in enumerate(mentions)]
        if merge_ho:
            rows.append({"person_id": "h", "reg_id": "PS01", "info_label": [], "doc_id": "D0001",
                         "evidence": {"name_spans": [[0, 3]]}})
        return rows
    with tempfile.TemporaryDirectory() as d:
        d = pathlib.Path(d)
        drop = next(m for m in g.mentions if m["person_id"] == "PS01")
        (d / "person_rows.json").write_text(json.dumps(rows_from([m for m in g.mentions if m is not drop])))
        out, _, s = SP.score(d, "t")
        assert s["bcubed"]["확정"]["all"][1] < 1.0                                  # 놓친 언급 → 재현율 하락
        i01 = [r for r in out if r["평가 항목 ID"] == "I01" and "PS01" in r["비고"]]
        assert i01 and i01[0]["결과"] == "실패", i01                                # 이름 미탐도 시나리오 실패
        # 두 인물 언급이 한 후보 행에 섞임
        a, b = [m for m in g.mentions if m["doc_id"] == "IT0001"][:2]
        rows = rows_from([m for m in g.mentions if m not in (a, b)])
        rows.append({"person_id": "mix", "reg_id": "X", "info_label": [], "doc_id": "IT0001",
                     "evidence": {"name_spans": [[a["start"], a["end"]], [b["start"], b["end"]]]}})
        (d / "person_rows.json").write_text(json.dumps(rows))
        out, _, s = SP.score(d, "t")
        assert s["mixed_rows"] == 1 and s["bcubed"]["확정"]["all"][0] < 1.0
        assert any(r["결과"] == "실패" and "후보 혼합" in r["실제값"] for r in out if r["평가 항목 ID"].startswith("I"))


def test_run_safety_and_data_hash():
    from . import run as RUN
    orig = RUN._db_doc_ids
    try:
        RUN._db_doc_ids = lambda: {"IT0001", "D0001", "DEV_9999"}
        try:
            RUN.check_wipe_safe({"ids": ["D0001"]}, False, "it")
            raise AssertionError("다른 문서가 있는 DB를 지우려 함")
        except SystemExit:
            pass
        RUN.check_wipe_safe({"ids": ["D0001"]}, True, "it")                               # 명시적으로 허용할 때만
        RUN._db_doc_ids = lambda: {"IT0001", "D0001"}
        RUN.check_wipe_safe({"ids": ["D0001"]}, False, "it")
    finally:
        RUN._db_doc_ids = orig
    import shutil
    with tempfile.TemporaryDirectory() as d:
        d = pathlib.Path(d)
        for f in RUN.DERIVED:
            shutil.copy(DATA / f, d / f)
        m = json.loads((DATA / "it_manifest.json").read_text(encoding="utf-8"))
        for n in m["derived_sha256"]:
            if not (d / n).exists():
                shutil.copy(DATA / n, d / n)
        old = RUN.DATA
        RUN.DATA = d
        try:
            RUN.check_data()
            with open(d / "it_cases.jsonl", "a", encoding="utf-8") as fh:
                fh.write("\n")
            try:
                RUN.check_data()
                raise AssertionError("내용이 바뀐 데이터를 통과시킴")
            except SystemExit:
                pass
        finally:
            RUN.DATA = old


def test_phase_todo_skips_done():
    from . import run as RUN
    g = Gold()
    cases = [c for c in g.cases if "R0" in c["search_configs"]][:4]
    with tempfile.TemporaryDirectory() as d:
        d = pathlib.Path(d)
        (d / "traces").mkdir()
        assert RUN.phase_todo(["R0"], cases, d, True, False) == 4
        jl_write(d / "traces" / "search_R0.jsonl", [{"qid": c["qid"]} for c in cases[:3]] + [{"qid": cases[3]["qid"], "exec_error": {"kind": "rate_limit"}}])
        assert RUN.phase_todo(["R0"], cases, d, True, False) == 1                  # 실행 오류는 남은 일


def test_neg_one_word_later():
    M = SA.match_status
    assert M("승인", "승인을 받지 못했습니다") == "neg"          # 부정이 한 단어 뒤에 옴
    assert M("승인", "승인을 받지 않았습니다") == "neg"
    assert M("승인", "승인을 받았습니다") == "hit"
    assert M("지급", "지급 처리되었습니다") == "hit"
    assert M("승인", "승인 후 지급됩니다") == "hit"


def test_wipe_guard_db_unreachable(monkeypatch):
    import pytest
    from . import run as R
    h = {"ids": ["D1"]}
    monkeypatch.setattr(R, "_db_doc_ids", lambda: None)
    monkeypatch.setattr(R, "_project_volumes", lambda p: [])          # 첫 실행: 지울 볼륨 없음 → 통과
    R.check_wipe_safe(h, False, "it")
    monkeypatch.setattr(R, "_project_volumes", lambda p: ["it_pgdata"])  # 볼륨은 있는데 DB 연결 불가 → 멈춤
    with pytest.raises(SystemExit):
        R.check_wipe_safe(h, False, "it")
    R.check_wipe_safe(h, True, "it")                                  # --wipe-other-data 면 진행
    monkeypatch.setattr(R, "_project_volumes", lambda p: None)        # 볼륨 확인 실패 → 멈춤
    with pytest.raises(SystemExit):
        R.check_wipe_safe(h, False, "it")


def test_dialog_follow_merge_turns_and_unverified():
    """검토 반례: 이어받기 누락·불일치, 합치기 실패, 턴 누락, 검사 대상 없음은 통과가 아니어야 한다."""
    from . import score_dialog as SDG
    d = _dlg("ITM01")                                              # Q12: 2턴이 조각 → 합쳐야 함
    t1 = _turn(1, need_more=True, state="clarify")
    ok2 = dict(docs=["IT0019"], answer="대출 금액은 2,500만 원입니다 [IT0019]")
    assert SDG.judge(d, {"turns": [t1, _turn(2, follow="req_1", **ok2)]}, {})[0] == "통과"
    res, actual, stage, _, _ = SDG.judge(d, {"turns": [t1, _turn(2, follow=None, merged=True, **ok2)]}, {})
    assert res == "실패" and stage == "재질문·이어받기", actual              # follow_up_of 누락
    assert SDG.judge(d, {"turns": [t1, _turn(2, follow="req_9", **ok2)]}, {})[0] == "실패"   # 1턴 request_id 와 다름
    res, actual, stage, _, _ = SDG.judge(d, {"turns": [t1, _turn(2, follow="req_1", merged=False, **ok2)]}, {})
    assert res == "실패" and stage == "재질문·이어받기", actual              # 합치기 실패 (merged=False)
    d5 = _dlg("ITM05")                                             # Q11: 2턴이 완전한 질문 → 합치기는 보지 않음
    ok5 = {"turns": [t1, _turn(2, follow="req_1", merged=False, docs=["IT0017"], answer="카드 분실은 11월 11일 접수 [IT0017]")]}
    assert SDG.judge(d5, ok5, {})[0] == "통과"
    d3 = _dlg("ITM03")                                             # Q13: 1턴만 실행되면 기대 상태(clarify)여도 통과 아님
    res, actual, stage, _, _ = SDG.judge(d3, {"turns": [t1]}, {})
    assert res == "실패" and stage == "대화 중단" and "1/2" in actual
    _, _, _, x04, x06 = SDG.judge(d, {"turns": []}, {"홍길동": "pii"})
    assert x04[0] == "미검증" and x06[0] == "미검증"                      # 응답·감사 로그가 아예 없음
    no_audit = {"turns": [_turn(1, need_more=True, state="clarify", audit=False), _turn(2, follow="req_1", audit=False, **ok2)]}
    assert SDG.judge(d, no_audit, {})[4][0] == "미검증"


if __name__ == "__main__":                                # monkeypatch 를 쓰는 테스트가 있어 pytest 로 실행한다
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))


def test_audit_lv0_and_api_based(monkeypatch):
    """Lv0 권한값이 정수 0이어도 레벨 일치, 상태·문서는 실제 API 응답과 비교."""
    import contextlib
    import sys
    import types
    from . import run as R
    row = ("u0", 0, "answered", "가명 질문", ["IT0001"])

    class Cur:
        def execute(self, *a):
            pass

        def fetchall(self):
            return [row]

    @contextlib.contextmanager
    def conn():
        c = types.SimpleNamespace(cursor=lambda: contextlib.nullcontext(Cur()))
        yield c
    fake = types.ModuleType("starter.db")
    fake.auth_conn = conn
    pkg = sys.modules.get("starter") or types.ModuleType("starter")
    monkeypatch.setitem(sys.modules, "starter", pkg)
    monkeypatch.setitem(sys.modules, "starter.db", fake)
    monkeypatch.setattr(pkg, "db", fake, raising=False)
    ui = {"answered": "found"}.get
    out = R.audit_check("req", "u0", "Lv0", "found", ["IT0001"], {"홍길동"}, lambda s: ui(s, "sorry"))
    assert out["level_ok"] and out["user_ok"] and out["state_ok"] and out["docs_ok"] and out["raw_in_masked"] == 0
    out = R.audit_check("req", "u0", "Lv0", "sorry", ["IT0002"], set(), lambda s: ui(s, "sorry"))
    assert not out["state_ok"] and not out["docs_ok"]                 # API 응답과 다르면 실패
    out = R.audit_check("req", "u0", "Lv1", "found", ["IT0001"], set(), lambda s: ui(s, "sorry"))
    assert not out["level_ok"]


def test_x04_scans_whole_api_body():
    body = {"answer": "안내드립니다", "docs": [{"doc_id": "IT0001", "snippet": "a"}], "masked_query": "q",
            "extra": {"note": ["홍길동님"]}}
    assert "홍길동님" in " ".join(SA._strings(body))                  # 응답 어디에 있든 검사 대상
    assert "doc_id" not in " ".join(SA._strings(body))                # 키 이름은 제외


def _dlg(did):
    from . import score_dialog as SDG
    return next(d for d in SDG._gold().dialogs if d["id"] == did)


def _turn(i, need_more=False, state="answered", docs=(), answer="", follow=None, au_ok=True, fu=True, extra=None,
          merged=None, audit=True):
    au = {"rows": 1, "user_ok": True, "level_ok": au_ok, "state_ok": True, "docs_ok": True, "raw_in_masked": 0}
    if follow:
        au["follow_up_ok"] = fu
    body = {"request_id": f"req_{i}", "answer": answer, "docs": [{"doc_id": d, "snippet": "s"} for d in docs],
            "need_more": need_more, "masked_query": "q", **(extra or {})}
    return {"turn": i, "request_id": f"req_{i}", "need_more": need_more, "state": state, "api_body": body,
            "follow_up_of": follow, "merged": bool(follow) if merged is None else merged,
            "clarify_code": "ask_person" if need_more else None, "audit": au if audit else None}


def test_dialog_judge():
    from . import score_dialog as SDG
    d = _dlg("ITM01")                                              # 오태린 동명이인 → IT0019, 2,500만 원, 금지 '분실'
    good = {"turns": [_turn(1, need_more=True, state="clarify"),
                      _turn(2, docs=["IT0019"], answer="대출 금액은 2,500만 원입니다 [IT0019]", follow="req_1")]}
    res, actual, stage, x04, x06 = SDG.judge(d, good, {"홍길동": "pii"})
    assert res == "통과" and x04[0] == "통과" and x06[0] == "통과", (res, actual, x04, x06)
    no_ask = {"turns": [_turn(1, state="answered", docs=["IT0019", "IT0020"], answer="2,500만 원"),
                        _turn(2, docs=["IT0019"], answer="2,500만 원 [IT0019]")]}
    res, _, stage, _, _ = SDG.judge(d, no_ask, {})
    assert res == "실패" and stage == "재질문"                      # 1턴에 되묻지 않음
    mixed = {"turns": [good["turns"][0], _turn(2, docs=["IT0019", "IT0020"], answer="2,500만 원 [IT0019]", follow="req_1")]}
    res, actual, stage, _, _ = SDG.judge(d, mixed, {})
    assert res == "실패" and stage == "검색" and "IT0020" in actual   # 동명이인 문서가 섞임
    forbid = {"turns": [good["turns"][0], _turn(2, docs=["IT0019"], answer="2,500만 원, 카드 분실 신고 [IT0019]", follow="req_1")]}
    assert SDG.judge(d, forbid, {})[0] == "실패"                   # 금지 사실
    leak = {"turns": [good["turns"][0], _turn(2, docs=["IT0019"], answer="2,500만 원", follow="req_1",
                                              extra={"debug": "홍길동"})]}
    assert SDG.judge(d, leak, {"홍길동": "pii"})[3][0] == "실패"     # 응답 어느 필드든 원문이면 X04 실패
    bad_fu = {"turns": [good["turns"][0], _turn(2, docs=["IT0019"], answer="2,500만 원", follow="req_1", fu=False)]}
    assert SDG.judge(d, bad_fu, {})[4][0] == "실패"                 # follow_up_of 불일치
    no_col = {"turns": [good["turns"][0], _turn(2, docs=["IT0019"], answer="2,500만 원", follow="req_1", fu=None)]}
    assert SDG.judge(d, no_col, {})[4][0] == "미검증"
    lv0 = {"turns": [_turn(1, need_more=True, state="clarify", au_ok=False), good["turns"][1]]}
    assert SDG.judge(d, lv0, {})[4][0] == "실패"                    # 감사 로그 레벨 불일치


def test_dialog_unresolvable_and_split():
    from . import score_dialog as SDG
    d = _dlg("ITM03")                                              # 송유찬: 보충 답으로도 특정 불가 → 되묻기·보류
    ok = {"turns": [_turn(1, need_more=True, state="clarify"), _turn(2, need_more=True, state="no_result", follow="req_1")]}
    assert SDG.judge(d, ok, {})[0] == "통과"                       # 다시 되물음 (need_more) 도 인정
    picked = {"turns": [ok["turns"][0], _turn(2, state="answered", docs=["IT0051"], answer="상담 내용 [IT0051]", follow="req_1")]}
    assert SDG.judge(d, picked, {})[0] == "실패"                    # 한 사람으로 확정해 답함
    assert SDG.dialog_split(d) == "check" and SDG.dialog_split(_dlg("ITM01")) == "dev"
