"""통합 테스트 설계서(엑셀)의 03 시트 표를 it_source CSV로 내보낸다.

엑셀이 사람이 보는 설계 원본이고, 변환·검사 스크립트는 이 CSV만 읽는다.
    python eval/integration/export_design_csv.py --xlsx 통합테스트_설계서_v4.2.xlsx --out data/integration_v1/it_source
v4.2 표 헤더 기준 (예전 헤더 이름도 읽음). 필요한 열이 없으면 빈 값으로 넘어가지 않고 멈춘다.
내보내는 표: tblScenario(02 시트) → scenarios.csv, tblQuestions → question_groups.csv,
            tblPersons → persons.csv, tblDocs → docs.csv, tblExpo → exposure.csv
"""
import argparse
import csv
import pathlib

from openpyxl import load_workbook

# 엑셀 열 이름 → CSV 필드. 값이 튜플이면 앞에서부터 찾는다 (v4.2 이름, 예전 이름).
# OPTIONAL 필드는 엑셀에 열이 없으면 기존 CSV 값을 그대로 둔다. 그 밖의 열이 없으면 빈 값으로 넘어가지 않고 멈춘다.
TABLES = {
    "tblScenario": ("scenarios.csv", "scenario_id", {
        "scenario_id": ("시나리오 ID", "ID"), "area": ("평가 영역", "영역"), "title": ("테스트 항목",),
        "condition": ("테스트 조건",), "expected": ("기대 결과", "정답 기준 (기대 동작)"),
        "current_expected": ("현재 구현 예상",), "feature_state": ("기능 상태",), "plan": ("테스트 구분",),
        "unit": ("판정 단위",), "priority": ("우선순위",)}),
    "tblQuestions": ("question_groups.csv", "group_id", {
        "group_id": ("질문 그룹",), "split": ("split",), "difficulty": ("난이도",), "n_questions": ("질문 수",),
        "n_users": ("사용자 수",), "intent": ("질문 의도",), "target": ("대상 인물·문서",), "scenarios": ("연결 시나리오",),
        "variants": ("변형 유형",), "search_configs": ("검색 설정",), "answer_configs": ("답변 설정",),
        "users": ("실행 사용자",)}),
    "tblPersons": ("persons.csv", "person_id", {
        "person_id": ("인물 ID",), "group": ("구분",), "name": ("이름",), "actual_roles": ("실제 역할 (복수·변경)",),
        "affiliation": ("소속·직급",), "phone": ("전화",), "email": ("이메일",), "account": ("계좌",),
        "customer_or_employee_no": ("고객번호·사번",), "other_pii": ("기타 개인정보",), "purpose": ("설계 목적",),
        "identity_gold": ("동일인 정답",), "relations": ("다른 인물과의 관계",), "v3_expected": ("v3 예상 판별",),
        "scenarios": ("연결 시나리오",), "docs": ("등장 문서",), "split": ("split",)}),
    "tblDocs": ("docs.csv", "doc_id", {
        "doc_id": ("문서 ID",), "split": ("split",), "category": ("구분",), "doc_type": ("doc_type",), "date": ("작성일",),
        "difficulty": ("난이도",), "track": ("작성 트랙",), "format": ("작성 형식",), "persons": ("등장 인물",),
        "facts": ("업무 내용·핵심 사실",), "excluded": ("넣지 않을 정보",), "related_docs": ("동일인·동명이인 관련 문서",),
        "confusable_docs": ("검색 혼동 문서",), "scenarios": ("정답 근거 시나리오",),
        "distractor_scenarios": ("혼동·방해 시나리오",), "constraints": ("작성 제약·특이 사항",),
        "access_level": ("열람 권한",)}),
    "tblExpo": ("exposure.csv", None, {
        "doc_id": ("문서 ID",), "person_id": ("인물 ID",), "name_text": ("이름 표기",), "doc_role": ("문서 내 역할",),
        "role_form": ("역할 표기 형식",), "pii_kind": ("개인정보 항목",), "value": ("문서에 쓸 값 (표기)",),
        "position": ("위치",), "v3_expected_attribution": ("v3 예상 귀속",), "purpose": ("노출 목적",),
        "omitted": ("이 문서에서 뺄 정보",)}),
}
OPTIONAL = {("tblScenario", "current_expected")}      # v4.2 에서 빠진 열 (채점·변환 코드가 쓰지 않음)


def read_table(ws, name):
    t = ws.tables[name]
    rows = list(ws[t.ref])
    head = [c.value for c in rows[0]]
    out = []
    for r in rows[1:]:
        vals = [c.value for c in r]
        if all(v in (None, "") for v in vals):
            continue
        out.append(dict(zip(head, vals)))
    return head, out


def _cell(v):
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--xlsx", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    wb = load_workbook(a.xlsx, data_only=True)
    sheet_of = {t: ws.title for ws in wb.worksheets for t in ws.tables}
    out = pathlib.Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    problems = []
    plans = []
    for tname, (fname, key, cols) in TABLES.items():
        if tname not in sheet_of:
            problems.append(f"{tname} 표가 없습니다")
            continue
        head, rows = read_table(wb[sheet_of[tname]], tname)
        src = {}
        for field, names in cols.items():
            hit = next((n for n in names if n in head), None)
            if hit is None and (tname, field) not in OPTIONAL:
                problems.append(f"{tname}: '{' / '.join(names)}' 열이 없습니다 (지금 헤더: {', '.join(map(str, head))})")
            src[field] = hit
        old = {}
        if key and (out / fname).exists():               # 엑셀에 없는 선택 열은 기존 CSV 값을 유지
            with open(out / fname, encoding="utf-8") as f:
                old = {r[key]: r for r in csv.DictReader(f)}
        plans.append((fname, key, cols, src, rows, old))
    if problems:
        raise SystemExit("설계서 표 구조가 변환 코드와 맞지 않습니다:\n  - " + "\n  - ".join(problems))
    for fname, key, cols, src, rows, old in plans:
        with open(out / fname, "w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(cols))
            w.writeheader()
            for r in rows:
                rec = {}
                for field, hit in src.items():
                    if hit is not None:
                        rec[field] = _cell(r.get(hit))
                    else:
                        k = _cell(r.get(src[key])) if key else None
                        rec[field] = (old.get(k) or {}).get(field, "")
                w.writerow(rec)
        kept = [f for f, h in src.items() if h is None]
        print(f"{fname}: {len(rows)}행" + (f" (엑셀에 없는 열 {', '.join(kept)} 은 기존 CSV 값 유지)" if kept else ""))


if __name__ == "__main__":
    main()
