"""holdout 질의 88개를 하나씩 보며 '정답 문서를 찾았는지' 확인하는 스크립트 (레포에 커밋하지 않는 확인용).

1) 검색만 확인 (빠름, LLM 안 씀)
    python -m starter.run_queries --out outputs/run_clarify.jsonl
    python check_each_query.py --run outputs/run_clarify.jsonl

2) 챗봇 답변까지 확인 (느림, 질의마다 LLM 호출)
    python check_each_query.py --run outputs/run_clarify.jsonl --answer

- 화면에는 가명처리된 질문만 나온다 (실제 이름·번호는 안 나옴). 출력을 그대로 보내도 된다.
- 전체 결과는 outputs/check_each_query.tsv 에도 저장된다 (엑셀·구글 시트로 열 수 있음).
"""
import argparse  # 명령줄 옵션
import json  # jsonl 읽기
import os  # 레포 폴더를 import 경로에 넣기
import sys  # import 경로
import time  # 질문 사이 대기
from collections import defaultdict  # 유형별 집계
from pathlib import Path  # 파일 경로

sys.path.insert(0, os.getcwd())  # 레포 폴더(지금 폴더)를 import 경로 맨 앞에 넣는다

GOLD = "data/holdout/holdout_retrieval_queries_gold.jsonl"  # 정답 파일
QUERIES = "data/holdout/holdout_retrieval_queries.jsonl"  # 질문지


def load(path):
    """jsonl 파일을 줄마다 읽는다."""
    with open(path, encoding="utf-8") as f:  # 파일 열기
        return [json.loads(line) for line in f if line.strip()]  # 빈 줄 빼고 읽기


def main():
    ap = argparse.ArgumentParser()  # 옵션 정의
    ap.add_argument("--run", required=True)  # starter.run_queries 가 만든 결과 파일
    ap.add_argument("--gold", default=GOLD)  # 정답 파일
    ap.add_argument("--queries", default=QUERIES)  # 질문지 (--answer 때 원래 질문을 챗봇에 넣으려고)
    ap.add_argument("-k", type=int, default=3)  # 챗봇이 LLM 에 넘기는 문서 수와 같게 3
    ap.add_argument("--answer", action="store_true")  # 챗봇 답변까지 볼지
    ap.add_argument("--sleep", type=float, default=3.0)  # 답변 볼 때 질문 사이 대기 초 (429 예방)
    ap.add_argument("--out", default="outputs/check_each_query.tsv")  # 전체 결과 저장 파일
    a = ap.parse_args()

    gold = load(a.gold)  # 정답
    runs = {r["qid"]: r for r in load(a.run)}  # 검색 결과 (qid → 결과)
    raw = {q["qid"]: q["query"] for q in load(a.queries)}  # 원래 질문 (화면에는 안 찍고 챗봇에만 넣음)
    chat = None
    if a.answer:  # 답변까지 볼 때만 챗봇을 불러온다
        from starter import chat  # 현재 브랜치의 chat.py

    head = ["qid", "유형", "가명 질문", "정답 문서", f"상위{a.k}", f"Hit@{a.k}", "정답 첫 순위", "혼동 문서 섞임"]
    if a.answer:  # 답변 칸 추가
        head += ["state", "재질문 원인", "답변 근거 문서", "답변에 정답 문서", "답변 앞부분"]
    rows, by_type, misses = [], defaultdict(lambda: [0, 0]), []  # 결과, 유형별 [맞음, 전체], 틀린 질문
    for q in gold:  # 정답 파일 순서대로
        r = runs.get(q["qid"], {})  # 이 질문의 검색 결과
        neutral = set(q.get("neutral_doc_ids", []))  # 채점에서 빼는 문서 (평가 스크립트와 같은 규칙)
        ranked = [d for d in r.get("ranked_doc_ids", []) if d not in neutral]  # 순위 목록
        rel = set(q["relevant_doc_ids"])  # 정답 문서
        conf = set(q.get("confusable_doc_ids", []))  # 동명이인 등 혼동 문서
        top = ranked[: a.k]  # 상위 k개
        hit = bool(rel & set(top))  # 상위 k개 안에 정답이 있나
        first = next((i + 1 for i, d in enumerate(ranked) if d in rel), "-")  # 정답이 처음 나온 순위
        row = [q["qid"], q["type"], (r.get("masked_query") or "").replace("\t", " "), ",".join(sorted(rel)),
               ",".join(top) or "-", "O" if hit else "X", first, "O" if conf & set(top) else "X"]
        if a.answer:  # 챗봇 답변 확인
            time.sleep(a.sleep)  # 429 예방
            try:
                res = chat.answer(raw[q["qid"]], a.k)  # 화면과 같은 함수로 답변
                docs = [d["doc_id"] for d in res.get("docs", [])]  # 답변에 쓰인 문서
                ans = (res.get("answer") or "").replace("\n", " ").replace("\t", " ")[:120]  # 답변 앞부분 (가명 상태)
                row += [res.get("state"), res.get("clarify_code") or "-", ",".join(docs) or "-",
                        "O" if rel & set(docs) else "X", ans]
            except Exception as exc:  # 실패해도 다음 질문은 계속
                row += ["error", type(exc).__name__, "-", "X", "-"]
        rows.append(row)
        by_type[q["type"]][0] += hit  # 유형별 맞음
        by_type[q["type"]][1] += 1  # 유형별 전체
        if not hit:
            misses.append(row)  # 틀린 질문 모음
        print("\t".join(str(x) for x in row))  # 한 줄씩 바로 출력 (진행 상황 확인용)

    Path(a.out).parent.mkdir(parents=True, exist_ok=True)  # 저장 폴더
    with open(a.out, "w", encoding="utf-8") as f:  # 전체 결과 저장
        f.write("\t".join(head) + "\n")  # 머리글
        for row in rows:
            f.write("\t".join(str(x) for x in row) + "\n")  # 한 줄씩

    total = sum(v[1] for v in by_type.values())  # 전체 질문 수
    ok = sum(v[0] for v in by_type.values())  # 맞힌 수
    print(f"\n=== 요약: Hit@{a.k} {ok}/{total} ({ok / max(total, 1):.1%}) ===")
    for t, (h, n) in sorted(by_type.items()):  # 유형별
        print(f"  {t:<26} {h}/{n}")
    if a.answer:  # 답변 상태 집계
        states = defaultdict(int)
        for row in rows:
            states[row[8]] += 1  # state 칸
        print("  답변 state:", dict(states))
    print(f"\n=== 정답을 상위 {a.k}개 안에서 못 찾은 질문 {len(misses)}개 ===")
    print("\t".join(head))
    for row in misses:
        print("\t".join(str(x) for x in row))
    print(f"\n전체 결과 저장: {a.out}")


if __name__ == "__main__":
    main()
