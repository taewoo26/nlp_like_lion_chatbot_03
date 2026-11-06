"""적재 파이프라인: 문서 → 탐지 → 가명처리 → 청크 → 임베딩 → rag.chunks / vault.token_map

사용법
    python -m starter.ingest --data data/holdout_labeled.jsonl --reset

- 입력 파일의 entities(정답 라벨)는 쓰지 않습니다. 탐지는 pii_pipeline.detect() 가 합니다.
- outputs/pseudo_<파일명>.jsonl 에 문서별 가명처리 결과를 남깁니다 (check_token_consistency.py 입력).
"""
import argparse
import json
import time
from pathlib import Path

from . import address, config, db, pii_pipeline
from .embed import embed
from .person_role_mapping import persons_by_chunk
from .roles import load_rules


def doc_regions(text, spans):
    """문서의 주소 span 에서 시도·시군구만 뽑는다 (REG-01). 문서의 모든 청크에 같은 값을 넣는다."""
    return sorted({r for s in spans if s["label"] == "address"
                   for r in address.regions_of(text[s["start"]:s["end"]])})


def load_docs(path, limit=None):
    with open(path, encoding="utf-8") as f:
        docs = [json.loads(l) for l in f if l.strip()]
    return docs[:limit] if limit else docs


def chunk(text, max_chars=600):
    """빈 줄·줄바꿈 단위로 나누고 max_chars 안에서 이어 붙입니다. 토큰이 잘리지 않도록 줄 단위로만 자릅니다."""
    parts, cur = [], ""
    for line in text.split("\n"):
        if cur and len(cur) + len(line) + 1 > max_chars:
            parts.append(cur)
            cur = line
        else:
            cur = f"{cur}\n{line}" if cur else line
    if cur.strip():
        parts.append(cur)
    return parts or [text]


def collect_mapping(mapping, record):
    current = mapping.setdefault(record['token'], {
        'label': record['label'], 'normalized': record['normalized'], 'originals': set(),
    })
    if (current['label'], current['normalized']) != (record['label'], record['normalized']):
        raise ValueError('서로 다른 개인정보 값의 문서 간 토큰 충돌')
    current['originals'].add(record['original'])


def save_mappings(cur, mapping):
    for token, value in mapping.items():
        cur.execute(
            """INSERT INTO vault.token_map (token, label, normalized, originals)
               VALUES (%s, %s, %s, %s)
               ON CONFLICT (token) DO UPDATE SET originals =
                   ARRAY(SELECT DISTINCT unnest(vault.token_map.originals || EXCLUDED.originals))
               WHERE vault.token_map.label = EXCLUDED.label
                 AND vault.token_map.normalized = EXCLUDED.normalized
               RETURNING token""",
            (token, value['label'], value['normalized'], sorted(value['originals'])),
        )
        if cur.fetchone() is None:
            raise ValueError('기존 Vault 매핑과 토큰 충돌')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=str(config.DATA_DIR / "holdout_labeled.jsonl"))
    ap.add_argument("--reset", action="store_true", help="기존 rag.chunks / vault.token_map 비우고 시작")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--max-chars", type=int, default=600)
    a = ap.parse_args()

    if pii_pipeline.IS_DEMO and config.EMBED_PROVIDER == "api":
        print("⚠ 데모용 마스킹은 이름 등을 탐지하지 못해 원문 일부가 임베딩 API로 전송됩니다. "
              "가상 데이터라 테스트는 가능하지만, 실제 과제에서는 반드시 여러분의 모듈로 교체하세요.")

    docs = load_docs(a.data, a.limit)
    config.OUT_DIR.mkdir(exist_ok=True)
    pseudo_path = config.OUT_DIR / f"pseudo_{Path(a.data).stem}.jsonl"

    region_on, role_on = config.meta_on("REGION_META"), config.meta_on("ROLE_META")
    rules = None
    if role_on:
        with db.rag_conn() as conn, conn.cursor() as cur:
            rules = load_rules(cur)

    t0 = time.perf_counter()
    rows, mapping, pseudo = [], {}, []
    for d in docs:
        spans = pii_pipeline.detect(d["text"])
        masked, records = pii_pipeline.pseudonymize(d["text"], spans)
        parts = chunk(masked, a.max_chars)
        regions = doc_regions(d["text"], spans) if region_on else []
        persons = (persons_by_chunk(d["id"], d["text"], masked, records, parts, rules,
                                    [s for s in spans if s["label"] == "person"])
                   if role_on else {})
        pseudo.append({"id": d["id"], "entities": [
            {k: r[k] for k in ("label", "start", "end", "token")} for r in records]})
        for r in records:
            collect_mapping(mapping, r)
        aliases = {r["alias_token"] for r in records if r.get("alias_token")}  # NAME_LINK=doc 일 때만 생김
        for i, c in enumerate(parts):
            chunk_id = f"{d['id']}#{i}"
            rows.append({"chunk_id": chunk_id, "doc_id": d["id"], "chunk_idx": i,
                         "doc_type": d.get("doc_type"), "masked_text": c,
                         "tokens": sorted(set(pii_pipeline.TOKEN_PATTERN.findall(c)) | aliases),
                         "persons": json.dumps(persons.get(chunk_id, []), ensure_ascii=False),
                         "regions": regions})
    t_mask = time.perf_counter() - t0

    t1 = time.perf_counter()
    # 임베딩에는 토큰을 라벨 이름으로 바꾼 사본만 넣는다 (DB에는 masked_text 그대로 저장)
    vecs = embed([pii_pipeline.embedding_text(r["masked_text"]) for r in rows])
    t_embed = time.perf_counter() - t1

    with db.rag_conn() as conn, conn.cursor() as cur:
        if a.reset:
            cur.execute("TRUNCATE rag.chunks CASCADE")
        cur.executemany(
            """INSERT INTO rag.chunks (chunk_id, doc_id, chunk_idx, doc_type, masked_text, tokens, embedding,
                                       persons, regions)
               VALUES (%(chunk_id)s, %(doc_id)s, %(chunk_idx)s, %(doc_type)s, %(masked_text)s, %(tokens)s,
                       %(embedding)s, %(persons)s::jsonb, %(regions)s)
               ON CONFLICT (chunk_id) DO UPDATE SET masked_text = EXCLUDED.masked_text,
                   tokens = EXCLUDED.tokens, embedding = EXCLUDED.embedding, doc_type = EXCLUDED.doc_type,
                   persons = EXCLUDED.persons, regions = EXCLUDED.regions""",
            [r | {"embedding": v} for r, v in zip(rows, vecs)])

        # Vault의 오류가 RAG 커밋 전에 전파되도록 한다. 두 DB 연결의 완전한 원자성은 별도 과제다.
        with db.vault_conn() as vault, vault.cursor() as vault_cur:
            if a.reset:
                vault_cur.execute("TRUNCATE vault.token_map")
            save_mappings(vault_cur, mapping)

    with open(pseudo_path, "w", encoding="utf-8") as f:
        for p in pseudo:
            f.write(json.dumps(p, ensure_ascii=False) + "\n")

    n = len(docs)
    print(f"문서 {n}건 → 청크 {len(rows)}개, 토큰 {len(mapping)}종 적재")
    print(f"탐지+가명처리 {t_mask * 1000 / n:.1f} ms/문서, 임베딩 {t_embed * 1000 / n:.1f} ms/문서 ({config.EMBED_PROVIDER})")
    print(f"가명처리 결과: {pseudo_path}")


if __name__ == "__main__":
    main()