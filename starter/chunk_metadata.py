"""문서 한 건의 청크별 메타데이터 (rag.chunks.persons, rag.chunks.regions).

메타데이터 규칙 정의서 v0.2
  ROLE-30·31  persons: 언급마다 {token, role, mention, source, rule_id} 객체. 토큰과 역할을 짝으로 저장
  ROLE-32     같은 토큰이 여러 번 나오면 언급마다 저장. mention = 청크 안 순번 (문서 안 순번은 미정 ROLE-32-N)
  REG-20·21   regions: 그 청크에 들어간 주소의 '시도 시군구' 표준값, 청크 안에서 중복 없이

판단은 원문으로 하고(COM-03) 저장하는 값은 토큰·범주값뿐이다. 원문 이름·주소는 결과에 없다.
주소가 어느 청크에 들어갔는지는 가명 텍스트 위치로 정한다.
  - 토큰이 된 주소: 그 토큰이 있는 청크
  - 지역만 있어 본문에 그대로 남은 주소('대구 수성구'): 앞 토큰 다음 위치에서 그 글자를 찾아 정한다
"""
from . import pii_pipeline
from .address import region_bounds, standard_region
from .person_role_mapping import chunk_ranges, map_person_roles


def _chunk_of(ranges, pos):
    return next((cid for start, end, cid in ranges if start <= pos < end), None)


def _address_positions(text, spans, masked, records):
    """address span 마다 (가명 텍스트 위치, 원문 주소). 위치를 못 찾으면 위치 None."""
    tokens = list(pii_pipeline.TOKEN_PATTERN.finditer(masked))
    by_span = {(r["start"], r["end"]): m for m, r in zip(tokens, records)}
    placed = sorted((r["start"], m.end()) for m, r in zip(tokens, records))
    for s in sorted(spans, key=lambda x: x["start"]):
        if s.get("label") != "address":
            continue
        original = text[s["start"]:s["end"]]
        match = by_span.get((s["start"], s["end"]))
        if match:
            yield match.start(), original
            continue
        bounds = region_bounds(original)
        kept = original[bounds[0]:bounds[1]] if bounds else ""
        cursor = max([end for start, end in placed if start < s["start"]], default=0)
        pos = masked.find(kept, cursor) if kept else -1
        yield (pos if pos >= 0 else None), original


def build_chunk_metadata(doc_id, text, spans, masked, records, chunks, master=None, llm=None):
    """{chunk_id: {"persons": [...], "regions": [...]}}. 모든 청크가 키로 들어간다 (빈 값 포함)."""
    ranges = chunk_ranges(doc_id, masked, chunks)
    meta = {cid: {"persons": [], "regions": []} for _, _, cid in ranges}
    for row in map_person_roles(doc_id, text, masked, records, chunks, master, llm):
        meta[row["chunk_id"]]["persons"].append({
            "token": row["person_token"], "role": row["role"], "mention": row["mention_index"],
            "source": row["source"], "rule_id": row["rule_id"]})
    for pos, original in _address_positions(text, spans, masked, records):
        value = standard_region(original)
        chunk_id = _chunk_of(ranges, pos) if pos is not None else None
        if value and chunk_id and value not in meta[chunk_id]["regions"]:
            meta[chunk_id]["regions"].append(value)
    return meta
