"""원문 PERSON records를 가명처리 후 청크의 토큰 등장 순서에 연결한다.

행: {doc_id, chunk_id, mention_index, person_token, role, source, rule_id}
  - mention_index: 청크 안 순번 (ROLE-32. 문서 안 순번으로 바꿀지는 미정 ROLE-32-N)
  - source: rule | default | llm (ROLE-23), rule_id: 판정 근거 (COM-08)
역할은 원문 위치로 판단하고(COM-03), LLM 보조는 가명 텍스트만 받는다(ROLE-21, COM-04).
"""
from . import pii_pipeline
from .person_roles import classify_mention
from .person_role_llm import context_lines


def chunk_ranges(doc_id, masked, chunks):
    """가명 텍스트 안에서 각 청크의 [start, end)와 chunk_id. 청크는 ingest.chunk() 결과 순서."""
    ranges, cursor = [], 0
    for index, part in enumerate(chunks):
        start = masked.find(part, cursor)
        if start < 0:
            raise ValueError("가명 텍스트와 청크의 위치가 다름")
        ranges.append((start, start + len(part), f"{doc_id}#{index}"))
        cursor = start + len(part)
    return ranges


def map_person_roles(doc_id, original, masked, records, chunks, master=None, llm=None):
    """llm: person_role_llm.make_classifier() 결과. None이면 LLM 보조를 쓰지 않는다."""
    tokens = list(pii_pipeline.TOKEN_PATTERN.finditer(masked))
    if [m.group() for m in tokens] != [r["token"] for r in records]:
        raise ValueError("가명 텍스트의 토큰 순서와 원문 records가 다름")

    ranges = chunk_ranges(doc_id, masked, chunks)
    counts = {chunk_id: 0 for _, _, chunk_id in ranges}
    result = []
    for token_match, record in zip(tokens, records):
        if record["label"] != "person":
            continue
        chunk_id = next((cid for start, end, cid in ranges
                         if start <= token_match.start() and token_match.end() <= end), None)
        if chunk_id is None:
            raise ValueError("PERSON 토큰을 포함한 청크를 찾지 못함")
        role, source, rule_id = classify_mention(original, record["start"], record["end"], master)
        if role == "unknown" and llm is not None:          # ROLE-20: 규칙으로 못 정한 언급만
            guess = llm(context_lines(masked, token_match.start(), token_match.end()), record["token"])
            if guess != "unknown":
                role, source, rule_id = guess, "llm", "ROLE-20"
        result.append({
            "doc_id": doc_id, "chunk_id": chunk_id,
            "mention_index": counts[chunk_id], "person_token": record["token"],
            "role": role, "source": source, "rule_id": rule_id,
        })
        counts[chunk_id] += 1
    return result
