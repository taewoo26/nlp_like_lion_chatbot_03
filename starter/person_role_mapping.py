"""원문 PERSON records를 가명처리 후 청크의 토큰 등장 순서에 연결한다."""
from . import pii_pipeline
from .person_roles import role_for_mention


def map_person_roles(doc_id, original, masked, records, chunks):
    tokens = list(pii_pipeline.TOKEN_PATTERN.finditer(masked))
    if [m.group() for m in tokens] != [r["token"] for r in records]:
        raise ValueError("가명 텍스트의 토큰 순서와 원문 records가 다름")

    chunk_ranges = []
    cursor = 0
    for index, part in enumerate(chunks):
        start = masked.find(part, cursor)
        if start < 0:
            raise ValueError("가명 텍스트와 청크의 위치가 다름")
        chunk_ranges.append((start, start + len(part), f"{doc_id}#{index}"))
        cursor = start + len(part)

    counts = {chunk_id: 0 for _, _, chunk_id in chunk_ranges}
    result = []
    for token_match, record in zip(tokens, records):
        if record["label"] != "person":
            continue
        chunk_id = next((cid for start, end, cid in chunk_ranges
                         if start <= token_match.start() and token_match.end() <= end), None)
        if chunk_id is None:
            raise ValueError("PERSON 토큰을 포함한 청크를 찾지 못함")
        result.append({
            "doc_id": doc_id, "chunk_id": chunk_id,
            "mention_index": counts[chunk_id], "person_token": record["token"],
            "role": role_for_mention(original, record["start"], record["end"]),
        })
        counts[chunk_id] += 1
    return result
