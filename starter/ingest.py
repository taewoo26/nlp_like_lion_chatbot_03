"""적재 파이프라인: 문서 → 탐지 → 가명처리 → 청크 → 임베딩 → rag.chunks / vault.token_map

사용법
    python -m starter.ingest --data data/holdout_labeled.jsonl --reset

- 입력 파일의 entities(정답 라벨)는 쓰지 않습니다. 탐지는 pii_pipeline.detect() 가 합니다.
- outputs/pseudo_<파일명>.jsonl 에 문서별 가명처리 결과를 남깁니다 (check_token_consistency.py 입력).
- 청크마다 메타데이터를 함께 저장합니다 (메타데이터 규칙 정의서 v0.2, chunk_metadata.py).
    persons : 인물 언급별 {token, role, mention, source, rule_id}  (JSONB)
    regions : 주소의 '시도 시군구' 표준값                          (TEXT[])
  .env  CHUNK_METADATA=on(기본) | off   off 면 이전과 같이 두 열을 넣지 않음 (COM-09)
        ROLE_MASTER=db(기본) | file      역할 단서를 rag.role_master 에서 읽을지, 파일 사본을 쓸지 (ROLE-07)
        ROLE_LLM=off(기본) | on          역할 LLM 보조 (person_role_llm.py)
- --dry-run : 끝까지 처리하고 저장 결과 예시를 보여 준 뒤 rag·vault 모두 롤백합니다 (pseudo 파일도 쓰지 않음).
"""
import argparse
import json
import time
from pathlib import Path

from . import config, db, pii_pipeline
from .chunk_metadata import build_chunk_metadata
from .embed import embed
from .person_role_llm import make_classifier
from .role_master import load_role_master


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


def _switch(name, default, allowed):
    value = (config.env(name, default) or default).strip().lower()
    if value not in allowed:
        raise ValueError(f"{name} 는 {' 또는 '.join(allowed)} 이어야 합니다: {value}")
    return value


INSERT_BASE = """INSERT INTO rag.chunks (chunk_id, doc_id, chunk_idx, doc_type, masked_text, tokens, embedding)
               VALUES (%(chunk_id)s, %(doc_id)s, %(chunk_idx)s, %(doc_type)s, %(masked_text)s, %(tokens)s, %(embedding)s)
               ON CONFLICT (chunk_id) DO UPDATE SET masked_text = EXCLUDED.masked_text,
                   tokens = EXCLUDED.tokens, embedding = EXCLUDED.embedding, doc_type = EXCLUDED.doc_type"""
INSERT_META = """INSERT INTO rag.chunks (chunk_id, doc_id, chunk_idx, doc_type, masked_text, tokens, embedding,
                                    persons, regions)
               VALUES (%(chunk_id)s, %(doc_id)s, %(chunk_idx)s, %(doc_type)s, %(masked_text)s, %(tokens)s, %(embedding)s,
                       %(persons)s, %(regions)s)
               ON CONFLICT (chunk_id) DO UPDATE SET masked_text = EXCLUDED.masked_text,
                   tokens = EXCLUDED.tokens, embedding = EXCLUDED.embedding, doc_type = EXCLUDED.doc_type,
                   persons = EXCLUDED.persons, regions = EXCLUDED.regions"""


def attach_metadata(rows, prepared, master, llm=None):
    """rows 에 persons(Jsonb)·regions(list) 를 채운다. prepared: 문서별 (doc_id, text, spans, masked, records, parts)."""
    from psycopg.types.json import Jsonb
    by_id = {r["chunk_id"]: r for r in rows}
    for doc_id, text, spans, masked, records, parts in prepared:
        for chunk_id, meta in build_chunk_metadata(doc_id, text, spans, masked, records, parts,
                                                   master, llm).items():
            by_id[chunk_id]["persons"] = Jsonb(meta["persons"])
            by_id[chunk_id]["regions"] = meta["regions"]
            by_id[chunk_id]["_persons"] = meta["persons"]   # 요약 출력용 (DB에는 넣지 않음)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=str(config.DATA_DIR / "holdout_labeled.jsonl"))
    ap.add_argument("--reset", action="store_true", help="기존 rag.chunks / vault.token_map 비우고 시작")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--max-chars", type=int, default=600)
    ap.add_argument("--dry-run", action="store_true", help="저장 후 결과 예시만 보여 주고 롤백")
    a = ap.parse_args()
    if a.dry_run and a.reset:
        raise SystemExit("--dry-run 과 --reset 은 함께 쓸 수 없습니다.")
    use_meta = _switch("CHUNK_METADATA", "on", ("on", "off")) == "on"
    master_src = _switch("ROLE_MASTER", "db", ("db", "file"))

    if pii_pipeline.IS_DEMO and config.EMBED_PROVIDER == "api":
        print("⚠ 데모용 마스킹은 이름 등을 탐지하지 못해 원문 일부가 임베딩 API로 전송됩니다. "
              "가상 데이터라 테스트는 가능하지만, 실제 과제에서는 반드시 여러분의 모듈로 교체하세요.")

    docs = load_docs(a.data, a.limit)
    config.OUT_DIR.mkdir(exist_ok=True)
    pseudo_path = config.OUT_DIR / f"pseudo_{Path(a.data).stem}.jsonl"

    t0 = time.perf_counter()
    rows, mapping, pseudo, prepared = [], {}, [], []
    for d in docs:
        spans = pii_pipeline.detect(d["text"])
        masked, records = pii_pipeline.pseudonymize(d["text"], spans)
        pseudo.append({"id": d["id"], "entities": [
            {k: r[k] for k in ("label", "start", "end", "token")} for r in records]})
        for r in records:
            collect_mapping(mapping, r)
        aliases = {r["alias_token"] for r in records if r.get("alias_token")}  # NAME_LINK=doc 일 때만 생김
        parts = chunk(masked, a.max_chars)
        prepared.append((d["id"], d["text"], spans, masked, records, parts))
        for i, c in enumerate(parts):
            rows.append({"chunk_id": f"{d['id']}#{i}", "doc_id": d["id"], "chunk_idx": i,
                         "doc_type": d.get("doc_type"), "masked_text": c,
                         "tokens": sorted(set(pii_pipeline.TOKEN_PATTERN.findall(c)) | aliases)})
    t_mask = time.perf_counter() - t0

    t1 = time.perf_counter()
    # 임베딩에는 토큰을 라벨 이름으로 바꾼 사본만 넣는다 (DB에는 masked_text 그대로 저장)
    vecs = embed([pii_pipeline.embedding_text(r["masked_text"]) for r in rows])
    t_embed = time.perf_counter() - t1

    with db.rag_conn() as conn, conn.cursor() as cur:
        if use_meta:
            master = load_role_master(conn if master_src == "db" else None)
            attach_metadata(rows, prepared, master, make_classifier())
        if a.reset:
            cur.execute("TRUNCATE rag.chunks")
        cur.executemany(INSERT_META if use_meta else INSERT_BASE,
                        [{k: v for k, v in r.items() if not k.startswith("_")} | {"embedding": e}
                         for r, e in zip(rows, vecs)])

        # Vault의 오류가 RAG 커밋 전에 전파되도록 한다. 두 DB 연결의 완전한 원자성은 별도 과제다.
        with db.vault_conn() as vault, vault.cursor() as vault_cur:
            if a.reset:
                vault_cur.execute("TRUNCATE vault.token_map")
            save_mappings(vault_cur, mapping)
            if a.dry_run:
                vault.rollback()

        if a.dry_run:
            if use_meta:
                cur.execute("""SELECT chunk_id, persons, regions FROM rag.chunks
                               WHERE chunk_id = ANY(%s) ORDER BY chunk_id LIMIT 5""",
                            ([r["chunk_id"] for r in rows],))
                for chunk_id, persons, regions in cur.fetchall():
                    print(f"[dry-run] {chunk_id} regions={regions} persons={json.dumps(persons, ensure_ascii=False)}")
            conn.rollback()
            print("[dry-run] rag.chunks · vault.token_map 변경을 모두 롤백했습니다.")

    if not a.dry_run:
        with open(pseudo_path, "w", encoding="utf-8") as f:
            for p in pseudo:
                f.write(json.dumps(p, ensure_ascii=False) + "\n")

    n = len(docs)
    done = "처리 (dry-run: 롤백, 저장 안 됨)" if a.dry_run else "적재"
    print(f"문서 {n}건 → 청크 {len(rows)}개, 토큰 {len(mapping)}종 {done}")
    print(f"탐지+가명처리 {t_mask * 1000 / n:.1f} ms/문서, 임베딩 {t_embed * 1000 / n:.1f} ms/문서 ({config.EMBED_PROVIDER})")
    if use_meta:
        roles = {}
        for r in rows:
            for p in r.get("_persons", []):
                roles[p["role"]] = roles.get(p["role"], 0) + 1
        print(f"메타데이터: 인물 언급 {sum(roles.values())}개 {roles}, "
              f"지역이 있는 청크 {sum(1 for r in rows if r.get('regions'))}개 (역할 단서: {master_src})")
    if not a.dry_run:
        print(f"가명처리 결과: {pseudo_path}")


if __name__ == "__main__":
    main()