"""정답 문서를 받고도 '보류(withheld)'로 판정된 질문의 답변 전체를 보는 확인용 스크립트 (레포에 커밋하지 않음).

    python check_withheld.py                      # 기본 9건
    python check_withheld.py Q027 Q051            # 원하는 질문만

- 화면에는 가명처리된 질문·답변만 나온다 (실제 이름·번호는 안 나옴). 출력을 그대로 보내도 된다.
"""
import json  # jsonl 읽기
import os  # 레포 폴더를 import 경로에 넣기
import sys  # 명령줄 인자, import 경로
import time  # 질문 사이 대기 (429 예방)

sys.path.insert(0, os.getcwd())  # 레포 폴더(지금 폴더)를 import 경로 맨 앞에 넣는다
from starter import chat  # 현재 브랜치의 챗봇 (화면과 같은 함수)

QUERIES = "data/holdout/holdout_retrieval_queries.jsonl"  # 원래 질문지
GOLD = "data/holdout/holdout_retrieval_queries_gold.jsonl"  # 정답 파일
DEFAULT = ["Q027", "Q028", "Q029", "Q033", "Q040", "Q051", "Q068", "Q078", "Q081"]  # 지난번 보류 9건


def load(path):
    """jsonl 파일을 줄마다 읽는다."""
    with open(path, encoding="utf-8") as f:  # 파일 열기
        return [json.loads(line) for line in f if line.strip()]  # 빈 줄 빼고 읽기


def first_sentence(text):
    """답변의 첫 문장만 잘라 낸다 (마침표·물음표·줄바꿈 기준)."""
    for i, ch in enumerate(text):  # 글자를 하나씩 보며
        if ch in ".?!\n":  # 문장이 끝나는 곳을 만나면
            return text[: i + 1]  # 거기까지가 첫 문장
    return text  # 끝 표시가 없으면 전체가 한 문장


def main():
    qids = sys.argv[1:] or DEFAULT  # 인자로 받은 질문, 없으면 기본 9건
    raw = {q["qid"]: q["query"] for q in load(QUERIES)}  # 원래 질문 (챗봇에만 넣고 화면에는 안 찍음)
    gold = {g["qid"]: set(g.get("relevant_doc_ids", [])) for g in load(GOLD)}  # 정답 문서
    for qid in qids:  # 질문마다
        res = chat.answer(raw[qid], 3)  # 화면과 같은 방식으로 답변 (문서 3개)
        ans = res.get("answer") or ""  # 답변 전체 (가명 상태)
        docs = [d["doc_id"] for d in res.get("docs", [])]  # 답변에 쓰인 문서
        hits = [w for w in chat.WITHHELD_WORDS if w in ans]  # 보류로 잡힌 단어들
        head = first_sentence(ans)  # 첫 문장
        in_head = [w for w in hits if w in head]  # 그중 첫 문장에 있는 단어
        if not hits:  # 보류 단어가 없으면
            kind = "보류 아님(이번엔 답변함)"
        elif in_head:  # 첫 문장부터 보류면
            kind = "진짜 보류(첫 문장부터)"
        else:  # 첫 문장은 답했는데 뒤에서만 걸리면
            kind = "단어 때문에 잡힘(첫 문장은 답함)"
        print("=" * 70)  # 구분선
        print(f"{qid} | state={res.get('state')} | 원인={res.get('clarify_code') or '-'} | 판정={kind}")  # 요약 줄
        print(f"가명 질문: {res.get('masked_query')}")  # 가명처리된 질문
        print(f"근거 문서: {', '.join(docs) or '-'} | 정답 문서: {', '.join(sorted(gold.get(qid, []))) or '-'}")  # 문서 비교
        print(f"걸린 단어: {', '.join(hits) or '-'}")  # 어떤 보류 단어가 들어갔는지
        print("답변 전체:")  # 답변 본문
        print(ans)  # 줄바꿈 그대로 출력
        time.sleep(3)  # 다음 질문 전 대기 (LLM 429 예방)


if __name__ == "__main__":
    main()
