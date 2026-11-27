"""인물사전 적재 분포를 재서 산출물 5.2 표 모양으로 출력하는 확인용 스크립트 (레포에 커밋하지 않음).

    python measure_person_dist.py

- 지금 DB 에 들어 있는 인물사전을 그대로 센다 (적재는 하지 않음).
- 출력에는 개수·규칙 번호만 나오고 이름·번호는 나오지 않는다. 그대로 보내도 된다.
"""
import os  # 레포 폴더를 import 경로에 넣기
import sys  # import 경로
from collections import Counter, defaultdict  # 개수 세기

sys.path.insert(0, os.getcwd())  # 레포 폴더(지금 폴더)를 import 경로 맨 앞에 넣는다
from starter import db  # 팀 DB 접속 함수 (vault_conn)

LEVELS = ["high", "mid", "none", "hold"]  # 산출물 표의 줄 순서


def main():
    with db.vault_conn() as conn, conn.cursor() as cur:  # vault 스키마에 접속
        cur.execute("""SELECT person_id, reg_id, resolution_status, evidence->>'level',
                              evidence->>'rule', doc_id[1], evidence ? 'review'
                         FROM vault.person_info""")  # 후보 행 전부 (개인정보 칸은 안 읽음)
        rows = cur.fetchall()  # [(person_id, reg_id, 상태, 레벨, 규칙, 문서, 검토 여부), ...]
        cur.execute("""SELECT reg_id, reason->>'confidence', reason->>'path', reason->>'reason_code',
                              reason ? 'merged_into', reason->>'method'
                         FROM vault.person_registry""")  # 인물 등록부 전부
        regs = cur.fetchall()  # [(reg_id, confidence, path, 규칙, 통합됨 여부, method), ...]

    total = len(rows)  # 전체 후보 수
    if total == 0:  # 적재가 안 된 DB
        print("vault.person_info 가 비어 있습니다. PERSON_DICT=on 으로 적재했는지 확인하세요.")
        return

    # ---- 어떤 규칙으로 적재된 DB 인지 확인 (v3 는 registry.reason 에 confidence·path 를 남긴다)
    live = [r for r in regs if not r[4]]  # 다른 인물로 통합되지 않은 살아 있는 인물
    has_v3 = any(r[1] is not None or r[2] is not None for r in regs)  # v3 표시가 하나라도 있나
    print("=" * 64)
    print(f"적재 규칙 추정: {'v3' if has_v3 else ('v1 (confidence 표시 없음)' if regs else '판단 불가 (인물 0명)')}")
    print(f"지금 터미널의 PERSON_RULES = {os.environ.get('PERSON_RULES', '(없음 → .env 값 또는 기본 v1)')}")
    print("=" * 64)

    # ---- 표 1: 레벨별 건수 (산출물 5.2)
    by_level = Counter(r[3] or "(없음)" for r in rows)  # 레벨별 행 수
    status_of = defaultdict(Counter)  # 레벨별 상태 분포
    rules_of = defaultdict(Counter)  # 레벨별 규칙 분포
    for r in rows:  # 행마다
        status_of[r[3] or "(없음)"][r[2]] += 1  # 상태 세기
        rules_of[r[3] or "(없음)"][r[4] or "해당 없음"] += 1  # 규칙 세기
    print("\n[표 1] 레벨별 후보 행 (5.2 표의 'v3 (현재)' · '주요 규칙' 칸)")
    print(f"{'레벨':<6}{'상태':<24}{'건수':>6}{'비율':>9}   주요 규칙")
    for lv in LEVELS + [k for k in by_level if k not in LEVELS]:  # 정해진 순서 + 그 밖의 값
        n = by_level.get(lv, 0)  # 이 레벨 행 수
        if n == 0 and lv not in LEVELS:  # 없는 기타 레벨은 건너뜀
            continue
        st = " / ".join(f"{k} {v}" for k, v in status_of[lv].most_common())  # 상태 요약
        ru = " · ".join(f"{k} {v}" for k, v in rules_of[lv].most_common(4))  # 많은 규칙 4개
        print(f"{lv:<6}{st:<24}{n:>6}{n / total:>8.1%}   {ru}")
    print(f"{'합계':<6}{'':<24}{total:>6}{1:>8.1%}")

    # ---- 표 2: 인물(reg_id) 수
    conf = Counter(r[1] or "(표시 없음)" for r in live)  # 확정/추정별 인물 수
    manual = sum(1 for r in live if r[5] == "manual")  # 관리자가 만든 인물
    print("\n[표 2] 인물(reg_id) 수 (5.2 표 마지막 줄)")
    print(f"살아 있는 인물 {len(live)}명  =  " + " · ".join(f"{k} {v}명" for k, v in conf.most_common()))
    print(f"(통합되어 사라진 인물 {len(regs) - len(live)}명, 관리자가 만든 인물 {manual}명)")

    # ---- 표 3: 인물이 몇 개 문서에 걸쳐 있나
    docs_of = defaultdict(set)  # reg_id → 문서 집합
    rows_of = Counter()  # reg_id → 행 수
    for r in rows:  # 행마다
        if r[1]:  # reg_id 가 있으면
            docs_of[r[1]].add(r[5])  # 그 인물의 문서 추가
            rows_of[r[1]] += 1  # 그 인물의 행 수
    span = Counter(len(v) for v in docs_of.values())  # 문서 수별 인물 수
    print("\n[표 3] 인물 한 명이 걸친 문서 수")
    print("  " + " · ".join(f"{k}개 문서 {v}명" for k, v in sorted(span.items())))
    linked_rows = sum(rows_of.values())  # reg_id 를 받은 행 수
    print(f"  reg_id 를 받은 후보 행 {linked_rows}개 / 전체 {total}개 ({linked_rows / total:.1%})")

    # ---- 표 4: 실제로 발동한 규칙 (ver 0.1 의 4.9 표와 같은 모양)
    fired = Counter((r[4] or "해당 없음", r[3] or "(없음)") for r in rows)  # (규칙, 레벨)별 행 수
    print("\n[표 4] 실제로 발동한 규칙 (4.9 표)")
    print(f"{'규칙':<12}{'레벨':<8}{'건수':>6}")
    for (rule, lv), n in fired.most_common():  # 많은 순서
        print(f"{rule:<12}{lv:<8}{n:>6}")

    # ---- 표 5: 관리자 검토 거리
    review = sum(1 for r in rows if r[6])  # evidence.review 가 있는 행
    mid_people = conf.get("mid", 0)  # 추정으로 만든 인물
    print("\n[표 5] 관리자 검토 거리 (6장)")
    print(f"  추정(confidence=mid) 인물 {mid_people}명 · 검토 사유가 남은 행 {review}개")
    print("=" * 64)


if __name__ == "__main__":
    main()
