"""채점·보고: 실행 기록을 영역별 채점기로 채점하고 한 보고서로 모은다. LLM·DB를 다시 부르지 않는다.

    python -m eval.integration.report --run outputs/it/<실행ID>      # 채점 기준을 바꾼 뒤 다시 채점할 때
run.py 가 마지막에 자동으로 부른다.

출력: <실행 폴더>/report/
  summary.md        사람이 읽는 요약 (설정별 검색·답변 표, 탐지, 인물 판별, 권한, 필수 보안 판정)
  results.csv       04 시트 '결과 기록표' 형식 (실행 단위·실행 ID·split·난이도·실행 설정·평가 영역·…·결과·실패 원인 단계)
  set_results.csv   04 시트 '질문 집합 지표' 형식
  summary.json      영역별 원자료
  coverage.csv      시나리오 89개 + 공통 검사 7개 → 채점 코드·판정 기준·이번 실행의 결과 (채점 미구현도 표시)
CSV는 엑셀에서 바로 열리도록 UTF-8 BOM으로 저장한다. 엑셀 04 시트 표에 그대로 붙여 넣으면 01 시트 집계가 채워진다.

결과 값: 통과 / 실패 (챗봇 성능, 통과율 = 통과 ÷ (통과+실패)) · 실행 오류 / 미실행 (실행 문제, 성능에서 제외)
· 수동 확인 (자동으로 단정할 수 없음, 성능에서 제외) · 채점 미구현 · 기준선 기록 / 판정 보류 / 실행 대기 / 조건 미충족 / 미검증.
실행 완료율은 실행 전에 확정한 실행 대상 목록(manifest plan) 기준으로, 성능과 따로 표시한다 (결과 파일이 없는 설정도 미실행).
필수 보안 판정은 필수 권한 케이스가 하나라도 실행 오류·미실행·미검증이거나, 설계상 R0~R3 의 모든 답변에 대한 유출 검사가
끝나지 않았으면 '검증 미완료'다 (일부만 실행한 경우 충족이 나오지 않는다).
"""
import argparse
import collections
import datetime as dt
import json
import pathlib

from . import score_answer, score_detection, score_dialog, score_person, score_query, score_retrieval
from .common import (EXCEL_RESULT_COLS, EXCEL_SET_COLS, EXEC_ERROR, MANUAL, NOT_IMPL, NOT_RUN, Gold, csv_write,
                     jl_read, load_plan, result_row)

SCORERS = [("01 탐지", score_detection), ("02 인물 판별", score_person), ("03 질문 처리", score_query),
           ("04 검색", score_retrieval), ("05·06 답변·보안", score_answer), ("03 다중 턴 대화", score_dialog)]
UNIMPLEMENTED = ["G01 문서 접근 필터", "G02 미등록 유형 기본 거부", "G16 고객·직원 문서 혼합 차단"]
FULL_CONFIGS = ("R0", "R1", "R2", "R3")
PERF = ("통과", "실패")
NON_PERF = (EXEC_ERROR, "오류", NOT_RUN, MANUAL, NOT_IMPL)

# 시나리오·공통 검사 → 채점 코드와 기준 (coverage.csv). '채점 미구현'은 실행 경로나 기능이 없어 자동 판정하지 않는 것.
_I = ("score_person", "인물표의 시나리오 인물이 걸린 오병합·미연결·기존 문서 인물 병합이 0 (실제 관계 기준) "
      "+ score_answer 질문 단위 상태·사실")
_A = ("score_answer", "기대 상태 + 필수 사실(금액·날짜 정규화, 부정 표현 검사, 토큰 일치) + 금지 사실 0 + 원문 유출 0. "
      "단정 못 하면 수동 확인")
_ST = ("score_answer", "기대 상태(clarify·no_result 등) + 사실·유출 (재질문·특정 질문)")
COVERAGE = {
    "P01": ("score_detection", "정답 구간 vs 예측 구간 strict·partial P·R·F1 (run_compare 재사용, 기준선)"),
    "P02": ("score_detection", "실제 가명처리 본문(rag.chunks)에 정답 개인정보 원문·숫자·일부 숫자 잔존 0건"),
    "P03": ("score_detection", "개체마다 실제 토큰 = 같은 값의 기대 HMAC 토큰"),
    "P04": ("score_detection·score_query", "개인정보 없는 문서 본문 토큰 0 / 개인정보 없는 질문 토큰 0"),
    "P05": ("score_detection·score_answer", "본문에 삭제 표시만, 원문·숫자 없음 / 답변 상태·유출"),
    "P06": ("score_detection", "탐지 어려운 표기 잔존 건수 (기준선)"),
    "P07": ("score_detection", "미지원 식별정보 원문 유지 건수 (정책 미정 → 판정 보류)"),
    "P08": ("score_detection·score_query", "개인정보와 비슷한 설계 값의 과잉 탐지 수 / 질문 토큰 0"),
    "P09": _A,
    **{f"I{i:02d}": _I for i in range(1, 23) if i not in (12, 20)},
    "I12": ("score_person", "특정 불가 언급(AMB)이 어느 인물과도 묶이지 않음"),
    "I20": ("score_person", "노출 설계표 소유 인물 토큰이 그 인물 단서에 들어감 → 귀속 정밀도·재현율 + v3 예상 비교"),
    "Q01": ("score_query", "정답 개체마다 기대 토큰이 질문 토큰에 있음 (개체-토큰 짝 비교)"),
    "Q02": ("score_query", "오류 없이 처리, 원문이 가명처리 질문에 남지 않음"),
    "Q03": ("score_query", "실행 당시 키워드에 필수 키워드가 남음"),
    "Q04": ("score_query", "실행 당시 키워드에 불용어가 남지 않음"),
    "Q05": ("score_query", "가명처리 질문에 날짜·금액·유형 조건이 그대로 남음"),
    "Q06": ("score_query", "부정 어미(않·안 되·못·없·미-)와 부정되는 어간이 실행 당시 키워드에 남음"),
    "Q07": _ST, "Q08": _ST, "Q09": _ST, "Q14": _ST, "Q15": _ST,
    "Q10": ("score_answer·score_dialog", "질문: 지시어 질문은 정의서에 규칙이 없어 판정 보류 / 대화 ITM04: 1턴 되묻기 → 2턴 이어받기(follow_up_of, 정보 없음 되묻기 뒤라 합치기는 보지 않음) → 최종 상태·근거 문서·사실"),
    "Q11": ("score_answer·score_dialog", "질문: 지시어 질문은 판정 보류 / 대화 ITM05: 1턴 되묻기 → 보충 답 후 최종 상태·근거 문서·사실"),
    "Q12": ("score_dialog", "대화 ITM01·02 (R1·R3 × Lv0~2): 1턴 되묻기 → 2턴 follow_up_of = 1턴 request_id → 직전 질문과 합쳐 찾음(merged) → 기대 문서만 근거로 answered, "
                            "필수 사실·금지 사실"),
    "Q13": ("score_dialog", "대화 ITM03: 1턴 되묻기 → 보충 답으로도 특정 불가 → 다시 되묻기·보류, 특정 인물 문서 인용 없음"),
    "S01": ("score_retrieval", "정답 문서 1위"),
    "S02": ("score_retrieval", "정답 1위 + 다른 인물 조합 문서 상위 3위 0"),
    "S03": ("score_retrieval", "정답 문서 전부 상위 3위"),
    "S04": ("score_retrieval", "Recall@3 기록, 설정 비교는 질문 집합 지표 (기준선)"),
    "S05": ("score_retrieval", "Hit@1 기록, 설정 비교는 질문 집합 지표 (기준선)"),
    "S06": ("score_retrieval", "혼동 문서 상위 3위 0 (Confuse@3 = 0)"),
    "S07": ("score_retrieval", "요청 목적 문서 1위"),
    "S08": ("score_retrieval", "정답 전부 상위 3위, 혼동 문서 순위 기록"),
    "S09": ("score_retrieval", "1위 문서 유형 = 요청 유형 기록 (기준선)"),
    "S10": ("score_retrieval", "Precision@3·반환 수·빠진 정답 기록 (기준선)"),
    "S11": ("score_retrieval·score_answer", "반환 수 기록 / 답변이 무관 문서를 근거로 인용하지 않음"),
    "S12": ("score_retrieval·score_answer", "정답 상위 3위 / 정답 문서·필수 사실이 LLM 입력 청크에 있음"),
    "S13": ("score_retrieval", "정답 1위 + 혼동(동명이인) 문서 상위 3위 0"),
    "S14": ("score_retrieval", "요청 시점(최신·과거) 문서 1위"),
    **{f"A{i:02d}": _A for i in range(1, 12)},
    "A04": ("score_answer", _A[1] + ". 혼동 문서가 근거로 안 들어오면 조건 미충족"),
    "G01": (NOT_IMPL, "문서 접근 필터 기능 없음 → 사용자별 검색을 따로 실행하지 않음 (기준선 기록만)"),
    "G02": (NOT_IMPL, "미등록 유형 기본 거부 기능 없음 (기준선 기록만)"),
    "G16": (NOT_IMPL, "고객·직원 문서 혼합 차단 기능 없음 (기준선 기록만)"),
    "G17": ("score_answer", "정책 미정 → 판정 보류"),
    **{f"G{i:02d}": ("security", "권한 API: HTTP 상태 + 복원·부분 공개 값 정확도 + 원문 0 + restore_log 행 수")
       for i in (3, 4, 5, 6, 7, 8, 9, 10, 11, 18)},
    "G12": ("score_answer", "재검사가 동작(masked·blocked)하고 원문 유출 0. 동작할 일이 없었으면 미검증"),
    "G13": ("score_answer", "재검사가 동작(masked·blocked)하고 원문 유출 0. 동작할 일이 없었으면 미검증"),
    "G14": _A, "G15": _A,
    "X01": ("score_answer", "답변 토큰 ⊆ 질문·근거 문서 토큰"),
    "X02": ("score_answer", "인용 문서 ⊆ 근거 문서, 근거로 받은 정답 문서 중 하나 이상 인용"),
    "X03": ("score_answer", "기대 상태에 clarify 가 없는데 clarify 면 실패"),
    "X04": ("score_answer·score_dialog·security", "실제 API 응답 전체(질문·대화·권한 API)에 원문(기존 문서 포함) 0"),
    "X05": ("score_answer", "재검사 동작 건수 기록 (오탐 여부는 수동 확인)"),
    "X06": ("score_answer·score_dialog·security", "요청마다 query_log 1행·응답과 일치(질문·대화), 대화 2턴 follow_up_of, "
                                                    "권한 API restore_log 행 수"),
    "X07": ("score_retrieval", "BM25 적용 전 후보에 있던 정답이 BM25·컷오프 뒤 빠진 질문 0 (단계별 후보 직접 측정)"),
}


def _manifest(run_dir):
    p = pathlib.Path(run_dir) / "manifest.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def _security_rows(run_dir):
    """권한 API 결과 → 결과 행. 실행 설정 = API 평가 설정(엑셀 04 시트 E6), 실행 권한·계정 = 사번(여러 명은 ·)."""
    p = run_dir / "security.jsonl"
    if not p.exists():
        return [], []
    sec = jl_read(p)
    api_cfg = _manifest(run_dir).get("api_config", "")
    acc = {c["id"]: c for c in Gold().access}
    rows = []
    for s_ in sec:
        users = "·".join(acc.get(s_["id"], {}).get("user_ids") or [])
        result = EXEC_ERROR if s_["result"] == "오류" else s_["result"]
        note = "케이스 코드 오류" if s_["result"] == "오류" else ""
        scen = [x.strip() for x in ",".join(s_["scenario"] or ["G"]).split(",") if x.strip()]
        base = dict(unit="API", run_id=s_["id"], split="", diff="", cfg=api_cfg, level=users)
        for sid in scen:
            if sid in ("X04", "X06"):
                continue
            rows.append(result_row(**base, item_id=sid, content=f"{s_['id']} ({s_['required']})", expected=s_["expected"],
                                   actual=s_["actual"], result=result, stage="권한", note=note))
        for x in ("X04", "X06"):
            r_ = s_.get(x.lower())
            if r_ is None:                                # 실행 오류이거나 옛 기록
                r_ = result if result in (EXEC_ERROR, NOT_RUN) else "미검증"
            if x in scen and result in PERF:              # ITP19·ITP20 처럼 케이스 자체가 X04·X06 검사면 케이스 판정도 반영
                r_ = "실패" if "실패" in (r_, result) else ("통과" if r_ in ("미검증", "통과") else r_)
            r_ = EXEC_ERROR if r_ == "오류" else r_
            rows.append(result_row(**base, item_id=x, content=f"{s_['id']} {'응답 원문 유출' if x == 'X04' else '감사 로그'}",
                                   expected="0건" if x == "X04" else "요청·공개마다 기록", actual=s_.get(f"{x.lower()}_detail", ""),
                                   result=r_, stage="권한" if x == "X04" else "감사 로그"))
    return rows, sec


def to_excel_rows(rows, run_id):
    """내부 행 → 엑셀 04 시트 tblResult 열. 검증 키 = 실행 ID|케이스 ID|실행 설정|실행 권한·계정|시나리오 ID."""
    out = []
    for r in rows:
        sp = r["split"] if r["split"] in ("dev", "check") else ""
        note = " · ".join(x for x in (r.get("평가 내용"), r.get("비고")) if x)
        lv = r.get("실행 권한·계정") or ""
        key = "|".join([run_id, r["실행 ID"], r["실행 설정"] or "", lv, r["평가 항목 ID"]])
        out.append(dict(zip(EXCEL_RESULT_COLS, [run_id, r["평가 항목 ID"], r["실행 ID"], r["실행 설정"], lv, r["기대값"],
                                                r["실제값"], r["결과"], r["실패 원인 단계"], note, sp, r["난이도"],
                                                r["실행 단위"], r["평가 영역"], key])))
    return out


def to_excel_sets(sets, run_id):
    """내부 지표 행 → 엑셀 04 시트 tblSetResult 열 (난이도는 질문 집합 이름에서)."""
    out = []
    for x in sets:
        q = x["질문 집합"]
        diff = next((d for d in ("Easy", "Medium", "Hard") if d in q), "전체")
        out.append(dict(zip(EXCEL_SET_COLS, [run_id, q, x["split"], x["실행 설정"], x["평가 항목 ID"], x["지표"], x["값"],
                                             x["비교 기준 값"], x["판정"], diff])))
    return out


def answer_coverage(all_rows, g):
    """질문 보안 검사 완료 여부: 설계상 R0~R3 의 모든 (질문 × 사용자 레벨) 답변에 실제 X04(유출)·X06(감사 로그) 판정
    (통과·실패)이 둘 다 나왔는가. 답변 기록이 있어도 채점이 실패했으면 행이 없으므로 완료로 보지 않는다.
    엑셀 04 시트 보안 검증 목록(tblQSec)의 X04·X06 열과 같은 범위다."""
    want = {(cfg, c["qid"], lv) for cfg in FULL_CONFIGS for c in g.cases if cfg in c["answer_configs"] for lv in c["users"]}
    got = collections.defaultdict(set)
    for r in all_rows:
        if r["평가 항목 ID"] in ("X04", "X06") and r["실행 단위"] == "질문" and r["결과"] in PERF:
            got[(r["실행 설정"], r["실행 ID"], r.get("실행 권한·계정") or "")].add(r["평가 항목 ID"])
    done = {k for k, v in got.items() if v == {"X04", "X06"}}
    return len(done & want), len(want)


def question_security_extra(all_rows, g):
    """질문 단위 추가 보안 검사 (엑셀 tblQSec '추가 검사 판정'): R0~R3 에서 실행 대상인 G 시나리오(G12·G13·G14·G15)를
    사용자 레벨마다 → (실패 목록, 미완료 목록, 전체 수)"""
    # G12·G13 은 같은 기준(재검사 동작 + 유출 0)으로 판정하므로 한 검사로 센다 (엑셀은 질문마다 둘 중 하나만 배정)
    norm = lambda sid: "G12·G13" if sid in ("G12", "G13") else sid
    want = {(cfg, c["qid"], lv, norm(k["check_id"])) for c in g.cases for k in c["checks"] if k["check_id"].startswith("G")
            for cfg in FULL_CONFIGS if k["judgement"].get(cfg) == "run" and cfg in c["answer_configs"] for lv in c["users"]}
    got = {}
    for r in all_rows:
        if r["실행 단위"] == "질문" and r["평가 항목 ID"].startswith("G"):
            key = (r["실행 설정"], r["실행 ID"], r.get("실행 권한·계정") or "", norm(r["평가 항목 ID"]))
            if got.get(key) != "실패":
                got[key] = r["결과"]
    fails = sorted(k for k in want if got.get(k) == "실패")
    pending = sorted(k for k in want if got.get(k) not in PERF)
    return fails, pending, len(want)


def _security_verdict(sec, answer_rows, required_ids, answer_done=None, q_extra=None):
    """필수 보안 판정. 실패·유출이 있으면 미충족. 그 밖에는 필수 케이스 전부 실행·판정되고
    설계상 모든 답변의 유출 검사가 끝나야(answer_done = (완료, 전체)) 조건부 충족이 될 수 있다."""
    got = {s["id"]: s for s in sec}
    req = [got[i] for i in required_ids if i in got]
    # 필수 API: 케이스 판정 + X04(응답 유출) + X06(감사 로그, ITP20 제외) — 엑셀 tblApiSec 과 같은 기준
    fails = [s["id"] for s in req if "실패" in (s["result"], s.get("x04"), s.get("x06") if s["id"] != "ITP20" else None)]
    leak = [r for r in answer_rows if r["평가 항목 ID"] in ("X04", "X06") and r["결과"] == "실패"]
    exec_err = [s["id"] for s in req if s["result"] in (EXEC_ERROR, "오류")]
    not_run = [i for i in required_ids if i not in got]
    unverified = [s["id"] for s in req if s["result"] not in ("통과", "실패", EXEC_ERROR, "오류")
                  or (s["result"] in PERF and (s.get("x04") not in PERF
                                              or (s["id"] != "ITP20" and s.get("x06") not in PERF)))]
    done, total = answer_done if answer_done else (0, 1)
    answer_incomplete = total - done
    q_fail, q_pend, q_total = q_extra if q_extra else ([], [], 0)
    if q_fail:
        fails = fails + [f"{c}/{q}/{lv}/{sid}" for c, q, lv, sid in q_fail]
    pending = exec_err + not_run + unverified
    hold = [s["id"] for s in sec if s["result"] == "판정 보류"]
    if fails or leak:
        verdict = "미충족"
    elif pending or answer_incomplete or q_pend or not sec:
        parts = [f"권한 API 미실행 {len(not_run)}" if not_run else "", f"실행 오류 {len(exec_err)}" if exec_err else "",
                 f"미검증 {len(unverified)}" if unverified else "",
                 f"질문 보안 검사(X04·X06) {done}/{total}" if answer_incomplete else "",
                 f"질문 추가 보안 검사(G12~G15) {q_total - len(q_pend)}/{q_total}" if q_pend else ""]
        verdict = "검증 미완료 (" + ", ".join(p for p in parts if p) + ")"
    elif hold:
        verdict = f"판정 보류 (정책 미정 {len(hold)}건, 미구현 {len(UNIMPLEMENTED)}건)"
    else:
        verdict = f"조건부 충족 (미구현 {len(UNIMPLEMENTED)}건)"
    return {"verdict": verdict, "required": len(required_ids), "passed": sum(s["result"] == "통과" for s in req),
            "failed": fails, "answer_leak_rows": len(leak), "exec_error": exec_err, "not_run": not_run,
            "unverified": unverified, "answer_checked": [done, total],
            "question_extra": {"failed": len(q_fail), "pending": len(q_pend), "total": q_total},
            "hold": hold, "unimplemented": UNIMPLEMENTED}


def _completion(run_dir, summary, sec):
    """영역·설정별 실행 완료율 (성능 지표와 별개). 대상은 실행 전에 확정한 실행 대상 목록(plan)."""
    out = []
    for area, key in (("04 검색", "04 검색"), ("05 답변", "05·06 답변·보안"), ("03 대화", "03 다중 턴 대화")):
        for cfg, v in (summary.get(key) or {}).items():
            if isinstance(v, dict) and isinstance(v.get("실행"), dict):
                e = v["실행"]
                out.append((area, cfg, e.get("대상", 0), e.get("정상 실행", 0), e.get(EXEC_ERROR, 0), e.get(NOT_RUN, 0)))
    plan = load_plan(run_dir)
    ids = sorted(plan["security"]) if plan and plan["security"] else ([s["id"] for s in sec] if sec else [])
    if ids:
        got = {s["id"]: s for s in sec}
        ok = sum(1 for i in ids if i in got and got[i]["result"] not in (EXEC_ERROR, "오류"))
        err = sum(1 for i in ids if i in got and got[i]["result"] in (EXEC_ERROR, "오류"))
        out.append(("06 권한 API", "—", len(ids), ok, err, len(ids) - ok - err))
    return out


def _coverage(all_rows, g):
    ids = list(g.scenarios) + [f"X0{i}" for i in range(1, 8)]
    cnt = collections.defaultdict(collections.Counter)
    for r in all_rows:
        cnt[r["평가 항목 ID"]][r["결과"]] += 1
    out = []
    for i in ids:
        code, rule = COVERAGE.get(i, (NOT_IMPL, "대응 채점 없음"))
        c = cnt.get(i, collections.Counter())
        if code == NOT_IMPL:
            state = NOT_IMPL
        elif not c:
            state = "이번 실행에 결과 없음"
        elif all(k in NON_PERF + ("기준선 기록", "판정 보류", "실행 대기", "미검증", "조건 미충족") for k in c):
            state = "판정 없음 (기록·보류·미실행만)"
        else:
            state = "채점됨"
        title = g.scenarios[i]["title"] if i in g.scenarios else {"X01": "토큰 유지", "X02": "출처 인용", "X03": "불필요한 재질문",
                                                                  "X04": "개인정보 유출", "X05": "재검사 오탐",
                                                                  "X06": "감사 로그 완전성", "X07": "BM25 정답 탈락"}[i]
        out.append({"ID": i, "제목": title, "채점 코드": code, "판정 기준": rule, "상태": state,
                    "결과": " ".join(f"{k} {v}" for k, v in c.most_common())})
    return out


def _fmt(v):
    return "-" if v is None else (f"{v:.3f}" if isinstance(v, float) else str(v))


def build(run_dir):
    run_dir = pathlib.Path(run_dir)
    out = run_dir / "report"
    out.mkdir(exist_ok=True)
    run_id = run_dir.name
    date = dt.date.today().isoformat()
    all_rows, all_sets, summary = [], [], {}
    print("\n[채점]")
    for name, mod in SCORERS:
        try:
            rows, sets, s = mod.score(run_dir, run_id)
        except Exception as exc:
            rows, sets, s = [], [], {"error": f"{type(exc).__name__}: {exc}"}
        summary[name] = s
        all_rows += rows
        all_sets += sets
        print(f"  {name}: 결과 {len(rows)}행, 지표 {len(sets)}개" + (f" ({s.get('skipped') or s.get('error')})"
                                                              if isinstance(s, dict) and (s.get('skipped') or s.get('error')) else ""))
    sec_rows, sec = _security_rows(run_dir)
    all_rows += sec_rows
    g = Gold()
    answer_rows = [r for r in all_rows if r["실행 단위"] in ("질문", "대화")]   # 대화의 유출·감사 로그 실패도 보안 판정에 반영
    required_ids = [c["id"] for c in g.access if c["required"] == "필수"]
    summary["필수 보안 판정"] = _security_verdict(sec, answer_rows, required_ids, answer_coverage(all_rows, g),
                                             question_security_extra(all_rows, g))
    scorer_errors = {name: s_["error"] for name, s_ in summary.items() if isinstance(s_, dict) and s_.get("error")}
    if scorer_errors:
        summary["채점 오류"] = scorer_errors
    comp = _completion(run_dir, summary, sec)
    summary["실행 완료율"] = [dict(zip(("영역", "설정", "대상", "정상 실행", EXEC_ERROR, NOT_RUN), c)) for c in comp]
    cov = _coverage(all_rows, g)
    for r in all_rows:
        r["실행일"] = date
    # 엑셀 04 시트 표에 그대로 붙여 넣는 형식 (tblResult·tblSetResult 열 순서)
    csv_write(out / "results.csv", EXCEL_RESULT_COLS, to_excel_rows(all_rows, run_id))
    csv_write(out / "set_results.csv", EXCEL_SET_COLS, to_excel_sets(all_sets, run_id))
    csv_write(out / "coverage.csv", list(cov[0]), cov)
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1, default=str), encoding="utf-8")

    # 사람이 읽는 요약
    L = [f"# 통합 테스트 결과 {run_id}", ""]
    manifest = run_dir / "manifest.json"
    if manifest.exists():
        m = json.loads(manifest.read_text(encoding="utf-8"))
        L += [f"- 코드 커밋 {m.get('commit')}, 질문 {m.get('questions')}개, 실행 설정 {', '.join(m.get('configs', {}))}"]
        if m.get("skipped"):
            L += ["- 건너뛴 설정: " + "; ".join(f"{k} ({v})" for k, v in m["skipped"].items())]
        h = m.get("holdout") or {}
        if h:
            L += [f"- 적재: 기존 문서 {h.get('docs')}건 ({pathlib.Path(h.get('path', '')).name}) + 통합 테스트 84건"]
        rt = m.get("runtime") or {}
        if rt:
            L += ["- 실행 환경: " + ", ".join(f"{k}={v}" for k, v in rt.items() if "KEY" not in k)]
        if m.get("status") in ("stopped", "interrupted", "running"):
            last = (m.get("sessions") or [{}])[-1]
            L += [f"- **실행이 끝나지 않음 ({m.get('status')})**: {last.get('reason', '')} → `--resume` 으로 이어서 실행"]
        st = [x.get("llm_stats") for x in m.get("sessions", []) if x.get("llm_stats")]
        if st:
            tot = {k: sum(x.get(k, 0) for x in st) for k in ("calls", "ok", "retries", "failed")}
            kinds = {}
            for x in st:
                for k, v in (x.get("by_kind") or {}).items():
                    kinds[k] = kinds.get(k, 0) + v
            L += [f"- LLM 호출 {tot['calls']}회 (성공 {tot['ok']}, 재시도 {tot['retries']}, 최종 실패 {tot['failed']})"
                  + (f", 오류 종류 {kinds}" if kinds else "") + " · 상세: llm_calls.jsonl"]
    sv = summary["필수 보안 판정"]
    errs = summary.get("채점 오류") or {}
    if errs:
        L += ["", "**채점 오류로 평가 불가인 영역** (실행 기록은 남아 있으니 원인을 고친 뒤 `report --run` 으로 다시 채점):", ""]
        L += [f"- {k}: {v}" for k, v in errs.items()]
        L += [""]
    L += [f"- **필수 보안 판정: {sv['verdict']}** (필수 API {sv['passed']}/{sv['required']} 통과, 질문·대화 X04·X06 실패 {sv['answer_leak_rows']}건, "
          f"질문 보안 검사 {sv['answer_checked'][0]}/{sv['answer_checked'][1]})", ""]
    sv_pending = sv["exec_error"] + sv["not_run"]
    if sv_pending:
        L += [f"- 필수 권한 케이스 실행 오류·미실행: {', '.join(sv_pending)}", ""]
    if comp:
        L += ["## 실행 완료율 (성능과 별개)", "", "| 영역 | 설정 | 대상 | 정상 실행 | 실행 오류 | 미실행 | 완료율 |",
              "|---|---|---|---|---|---|---|"]
        for area, cfg, n, ok, err, nr in comp:
            L.append(f"| {area} | {cfg} | {n} | {ok} | {err} | {nr} | {_fmt(ok / n) if n else '-'} |")
        L.append("")
    cnt = collections.Counter((r["평가 영역"], r["결과"]) for r in all_rows)
    areas = sorted({a for a, _ in cnt})
    L += ["## 영역별 결과 행 (성능)", "", "통과율은 통과 ÷ (통과 + 실패). 실행 오류·미실행·수동 확인은 성능에 넣지 않습니다.", "",
          "| 영역 | 통과 | 실패 | 수동 확인 | 실행 오류 | 미실행 | 그 밖(기준선·보류 등) | 통과율 | 평가 상태 |",
          "|---|---|---|---|---|---|---|---|---|"]
    for a in areas:
        p, f = cnt[(a, "통과")], cnt[(a, "실패")]
        e, nr, mn = cnt[(a, EXEC_ERROR)] + cnt[(a, "오류")], cnt[(a, NOT_RUN)], cnt[(a, MANUAL)]
        other = sum(v for (aa, res), v in cnt.items() if aa == a and res not in PERF + NON_PERF)
        state = ("평가 불가 (실행 오류)" if e and not p + f else
                 "일부 평가 불가" if e else "일부 미실행" if nr else "수동 확인 남음" if mn else "평가 완료")
        L.append(f"| {a} | {p} | {f} | {mn} | {e} | {nr} | {other} | {_fmt(p / (p + f)) if p + f else '-'} | {state} |")
    for name in errs:
        L.append(f"| {name} (채점기) | - | - | - | - | - | - | - | **평가 불가 (채점 오류)** |")
    ret = summary.get("04 검색") or {}
    if ret and not ret.get("skipped") and not ret.get("error"):
        L += ["", "## 검색 (Hit@1 / Recall@3 / MRR) — 기존 문서 + 통합 테스트 전체 대상", "",
              "| 설정 | 전체 | dev | check | X07 BM25 탈락 |", "|---|---|---|---|---|"]
        for cfg, v in ret.items():
            if isinstance(v, dict) and cfg != "공통 질문 집합":
                cell = lambda k: " / ".join(_fmt(v.get(k, {}).get(x)) for x in ("Hit@1", "Recall@3", "MRR")) if v.get(k) else "-"
                L.append(f"| {cfg} | {cell('전체/전체')} | {cell('dev/전체')} | {cell('check/전체')} | {v.get('X07 탈락', '-')} |")
        cq = ret.get("공통 질문 집합")
        if cq:
            L += ["", f"R0~R3 비교 — 모든 설정에서 정상 실행된 같은 질문 {cq.get('n')}개 기준:", ""]
            L += [f"- {cfg}: " + " / ".join(_fmt(v.get(k)) for k in ("Hit@1", "Recall@3", "MRR"))
                  for cfg, v in cq.items() if isinstance(v, dict)]
    ans = summary.get("05·06 답변·보안") or {}
    if ans and not ans.get("skipped") and not ans.get("error"):
        L += ["", "## 답변", "", "| 설정 | 채점한 실행 | 상태 정확도 | 필수 사실 재현율 | 금지 사실 | 원문 유출 | 재질문 P / R | 코드 오류 |",
              "|---|---|---|---|---|---|---|---|"]
        for cfg, a in ans.items():
            tp, fp, fn = a.get("clarify_tp", 0), a.get("clarify_fp", 0), a.get("clarify_fn", 0)
            ex = a.get("실행") or {}
            L.append(f"| {cfg} | {ex.get('정상 실행', '-')}/{ex.get('대상', '-')} | {_fmt(a['state_ok'] / a['n']) if a.get('n') else '-'} | "
                     f"{_fmt(a['facts_hit'] / a['facts']) if a.get('facts') else '-'} | {a.get('forbid', 0)} | {a.get('leak', 0)} | "
                     f"{_fmt(tp / (tp + fp)) if tp + fp else '-'} / {_fmt(tp / (tp + fn)) if tp + fn else '-'} | {a.get('error', 0)} |")
    det = summary.get("01 탐지") or {}
    if det and not det.get("skipped") and not det.get("error"):
        L += ["", "## 탐지·가명처리 (통합 테스트 84건)", ""]
        for sp in ("전체", "dev", "check"):
            if sp in det:
                d = det[sp]
                L.append(f"- {sp}: 탐지 partial P/R/F1 {' / '.join(map(_fmt, d['partial']))} · 실제 본문 원문 잔존 "
                         f"{sum(d['residue'].values())}건 {d['residue'] or ''} · 어려운 표기 잔존 {sum(d['residue_hard'].values())}건 · "
                         f"토큰 불일치 {d['token_mismatch']}건")
    per = summary.get("02 인물 판별") or {}
    if per and not per.get("skipped") and not per.get("error"):
        b = per["bcubed"]
        L += ["", "## 인물 판별 (실제 적재, 기존 문서 포함)", ""]
        for name, v in b.items():
            L.append(f"- B-cubed P/R ({name} 연결, 실제 관계 기준): 전체 {v['all']}, dev {v['dev']}, check {v['check']} "
                     f"· 기존 문서 인물과 병합 {len(per['holdout_merge'][name])}명")
        L += [f"- 정답과 다른 쌍 {len(per['diff_pairs'])}개 (모두 실패로 기록). v3 규칙 회귀: 예상에 없던 차이 "
              f"{len(per['v3_unexpected_diff'])}개, 예상했는데 없는 차이 {len(per['v3_expected_but_absent'])}개",
              f"- 후보 행에 다른 인물 언급이 섞임 {per['mixed_rows']}행, 정답에 없는 이름 후보 {per['unlabeled_rows']}행, "
              f"이름 미탐 언급 {len(per['missed_mentions'])}개 (재현율에 반영)",
              f"- 문서 내 귀속 정밀도 {per['attribution']['precision']}, 재현율 {per['attribution']['recall']}, "
              f"v3 예상과 다른 것 {len(per['attribution']['v3_diff'])}건"]
    dl = summary.get("03 다중 턴 대화") or {}
    if dl and not dl.get("skipped") and not dl.get("error"):
        L += ["", "## 다중 턴 대화 (대화 × 레벨)", "", "| 설정 | 정상 실행 | 통과 | 실패 | 수동 확인 |", "|---|---|---|---|---|"]
        for cfg, v in dl.items():
            ex, jd = v.get("실행") or {}, v.get("판정") or {}
            L.append(f"| {cfg} | {ex.get('정상 실행', 0)}/{ex.get('대상', 0)} | {jd.get('통과', 0)} | {jd.get('실패', 0)} | "
                     f"{jd.get(MANUAL, 0)} |")
    q = summary.get("03 질문 처리") or {}
    if q and not q.get("skipped"):
        L += ["", "## 질문 처리 (통과/채점 수)", "", "- " + ", ".join(f"{k} {v}" for k, v in q.items())]
    if sec:
        L += ["", "## 권한 API", "", "| ID | 구분 | 결과 | 실제 |", "|---|---|---|---|"]
        L += [f"| {s['id']} | {s['required']} | {s['result']} | {s['actual']} |" for s in sec]
    fails = collections.Counter(r["실패 원인 단계"] for r in all_rows if r["결과"] == "실패")
    if fails:
        L += ["", "## 실패 원인 단계", ""] + [f"- {k or '미분류'}: {v}건" for k, v in fails.most_common()]
    gap = [c for c in cov if c["상태"] != "채점됨"]
    L += ["", f"## 채점 범위 (시나리오 {len(g.scenarios)}개 + 공통 검사 7개, 상세: coverage.csv)", "",
          f"- 채점됨 {sum(c['상태'] == '채점됨' for c in cov)}개, 그 밖 {len(gap)}개", ""]
    L += [f"- {c['ID']} {c['제목']}: {c['상태']} — {c['판정 기준'][:60]}" for c in gap]
    mf = _manifest(run_dir)
    L += ["", "## 엑셀에 넣기", "",
          "- 04 시트 **결과 기록표(tblResult)** 에 results.csv, **질문 집합 지표(tblSetResult)** 에 set_results.csv 를 그대로 붙여 넣습니다 (열 순서 동일).",
          f"- 04 시트 A6 '집계할 실행 ID' = `{run_id}`" + (f", E6 'API 평가 설정' = `{mf['api_config']}`" if mf.get("api_config") else "")
          + " 로 두면 01 시트 집계와 보안 검증 목록(검증 키)이 채워집니다."]
    (out / "summary.md").write_text("\n".join(L) + "\n", encoding="utf-8")
    print(f"\n보고서: {out / 'summary.md'}")
    print(f"필수 보안 판정: {sv['verdict']}")
    return summary


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True, help="outputs/it/<실행ID>")
    build(ap.parse_args().run)


if __name__ == "__main__":
    main()
