"""이름 언급별 역할을 청크의 persons JSONB 값으로 만든다 (규칙 정의서 ROLE-30~32).

persons 한 칸 = {"token", "role", "mention", "source", "rule_id"}
  mention : 문서 전체에서 몇 번째 이름 언급인지 (청크를 다시 나눠도 같은 언급을 가리킴)
  source  : rule (규칙으로 정함) / default (단서 없음 → unknown)   ※ LLM 보조를 붙이면 llm 추가
역할은 원문에서 정하고, 저장하는 값은 토큰·역할 코드·규칙 ID 뿐이다 (COM-03).
"""
from . import pii_pipeline
from .roles import role_for_mention


def _chunk_ranges(doc_id, masked, chunks):
    ranges, cursor = [], 0
    for index, part in enumerate(chunks):
        start = masked.find(part, cursor)
        if start < 0:
            raise ValueError("가명 텍스트와 청크의 위치가 다름")
        ranges.append((start, start + len(part), f"{doc_id}#{index}"))
        cursor = start + len(part)
    return ranges


def persons_by_chunk(doc_id, original, masked, records, chunks, rules, person_spans):
    """반환: {chunk_id: [persons 항목, ...]}  (이름이 없는 청크는 빈 리스트)"""
    tokens = list(pii_pipeline.TOKEN_PATTERN.finditer(masked))
    if [m.group() for m in tokens] != [r["token"] for r in records]:
        raise ValueError("가명 텍스트의 토큰 순서와 원문 records가 다름")
    ranges = _chunk_ranges(doc_id, masked, chunks)
    out = {chunk_id: [] for _, _, chunk_id in ranges}
    mention = 0
    for token_match, record in zip(tokens, records):
        if record["label"] != "person":
            continue
        chunk_id = next((cid for s, e, cid in ranges
                         if s <= token_match.start() and token_match.end() <= e), None)
        if chunk_id is None:
            raise ValueError("PERSON 토큰을 포함한 청크를 찾지 못함")
        others = [s for s in person_spans if (s["start"], s["end"]) != (record["start"], record["end"])]
        role, rule_id = role_for_mention(original, record["start"], record["end"], rules, others)
        out[chunk_id].append({"token": record["token"], "role": role, "mention": mention,
                              "source": "rule" if role != "unknown" else "default",
                              "rule_id": rule_id})
        mention += 1
    return out
