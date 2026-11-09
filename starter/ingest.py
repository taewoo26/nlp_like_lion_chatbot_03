"""적재 파이프라인: 문서 → 탐지 → 가명처리 → 청크 → 임베딩 → rag.chunks / vault.token_map

사용법
    python -m starter.ingest                      # 문서 30건씩 처리하고 배치마다 커밋
    python -m starter.ingest --batch-size 50      # 배치 크기 변경 (.env 의 INGEST_BATCH 로도 가능)
    python -m starter.ingest --resume             # 중단된 실행을 이어서 (완료 배치만 건너뜀)
    python -m starter.ingest --reset              # 전체 재적재: 지금처럼 한 트랜잭션 (배치 커밋 안 함)

- 입력 파일의 entities(정답 라벨)는 쓰지 않습니다. 탐지는 pii_pipeline.detect() 가 합니다.
- outputs/pseudo_<파일명>.jsonl 에 문서별 가명처리 결과를 남깁니다 (check_token_consistency.py 입력).
  실행 중에는 .part 파일에 쌓고, 전체가 끝나야 원래 이름으로 바꿉니다 (실패해도 기존 파일은 그대로).

배치 적재 (문서 단위)
- 배치마다 rag INSERT → vault 저장·커밋 → rag 커밋 → pseudo(.part) 기록 → 체크포인트 기록 순서입니다.
  vault 를 먼저 커밋하므로 '검색되는데 복원할 수 없는 토큰'은 생기지 않습니다.
- 체크포인트 outputs/ingest_checkpoint_<파일명>.json 에는 완료 doc_id 와 청크 수, 실행 조건만 적습니다 (원문 없음).
- --resume 은 실행 조건(입력 파일 해시, 임베딩·가명처리 설정)이 같을 때만 동작하고,
  체크포인트의 문서가 DB에 청크 전부 + vault 토큰까지 있는지 확인한 뒤 확인된 문서만 건너뜁니다.
- --reset 없이 적재할 때 DB에 이미 데이터가 있으면, 그 청크 몇 개를 현재 모델로 다시 임베딩해 비교합니다.
  벡터가 다르면(모델·설정 변경) 서로 다른 벡터가 섞이지 않도록 멈춥니다 → 모델을 바꿨다면 --reset 으로 적재하세요.

한계 (후속 과제)
- 수정된 문서만 골라 다시 넣는 기능은 없습니다. --resume 없이 실행하면 지금처럼 전체를 다시 처리합니다.
- 청크 수가 줄어든 문서의 예전 청크(유령 청크), rag 저장 전에 실패한 배치의 vault 매핑은 남을 수 있습니다.
"""
import argparse
import hashlib
import json
import os
import time
from pathlib import Path

import numpy as np

from . import address, config, db, pii_pipeline
from .embed import embed
from .person_role_mapping import persons_by_chunk
from .roles import load_rules

MODEL_CHECK_ROWS = 3        # 모델 확인에 다시 임베딩해 볼 기존 청크 수
MODEL_CHECK_MIN_COS = 0.999  # 같은 모델이면 1.0 에 가깝다 (배치 구성에 따른 미세한 오차만 허용)


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


# ---------------------------------------------------------------- 배치 적재·이어하기
def run_settings(a, region_on, role_on):
    """이어하기가 같은 조건의 실행인지 확인하는 값. 원문·키는 넣지 않는다 (해시만)."""
    key = (config.env("HMAC_KEY") or "").encode()
    return {
        "data": Path(a.data).name,
        "data_sha256": hashlib.sha256(Path(a.data).read_bytes()).hexdigest(),
        "limit": a.limit, "max_chars": a.max_chars,
        "embed_provider": config.EMBED_PROVIDER, "embed_model": config.EMBED_MODEL,
        "embed_model_path": config.env("EMBED_MODEL_PATH", ""), "embed_dim": config.EMBED_DIM,
        "embed_tokens": config.env("EMBED_TOKENS", ""), "address_mode": config.env("ADDRESS_MODE", ""),
        "name_link": config.env("NAME_LINK", ""), "region_meta": region_on, "role_meta": role_on,
        "hmac_key_sha256": hashlib.sha256(key).hexdigest()[:16],
    }


def write_json_atomic(path, value):
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(value, f, ensure_ascii=False)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def read_pseudo(path):
    """pseudo(.part) 줄을 doc_id 별로 읽는다. 같은 문서가 두 번 있으면 마지막 줄을 쓴다."""
    out = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                row = json.loads(line)
                out[row["id"]] = row
    return out


def write_pseudo(path, rows):
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        for p in rows:
            f.write(json.dumps(p, ensure_ascii=False) + "\n")
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def append_pseudo(path, rows):
    with open(path, "a", encoding="utf-8") as f:
        for p in rows:
            f.write(json.dumps(p, ensure_ascii=False) + "\n")
        f.flush()
        os.fsync(f.fileno())


def verified_done(rag_cur, vault_cur, done):
    """체크포인트의 완료 문서 중 DB에서 실제로 확인되는 것만 남긴다.
    확인 = 그 문서의 청크 0..n-1 이 rag 에 모두 있고, 그 청크들의 토큰이 vault 에 모두 있음."""
    if not done:
        return {}
    rag_cur.execute("SELECT doc_id, chunk_idx, masked_text FROM rag.chunks WHERE doc_id = ANY(%s)",
                    (sorted(done),))
    idx, toks = {}, {}
    for doc_id, chunk_idx, masked in rag_cur.fetchall():
        idx.setdefault(doc_id, set()).add(chunk_idx)
        toks.setdefault(doc_id, set()).update(pii_pipeline.TOKEN_PATTERN.findall(masked))
    ok = {d for d, n in done.items() if set(range(n)) <= idx.get(d, set())}
    need = sorted(set().union(*(toks.get(d, set()) for d in ok))) if ok else []
    if need:
        vault_cur.execute("SELECT token FROM vault.token_map WHERE token = ANY(%s)", (need,))
        have = {r[0] for r in vault_cur.fetchall()}
        ok = {d for d in ok if toks.get(d, set()) <= have}
    return {d: done[d] for d in ok}


def check_embedding_model(rag_cur):
    """DB 에 모델 정보가 없으므로, 기존 청크 몇 개를 현재 설정으로 다시 임베딩해 저장된 벡터와 비교한다."""
    rag_cur.execute("SELECT masked_text, embedding FROM rag.chunks ORDER BY chunk_id LIMIT %s",
                    (MODEL_CHECK_ROWS,))
    rows = list(rag_cur.fetchall())
    if not rows:
        return
    fresh = embed([pii_pipeline.embedding_text(t) for t, _ in rows])
    for (_, old), new in zip(rows, fresh):
        old = old.to_numpy() if hasattr(old, "to_numpy") else old   # pgvector 버전에 따라 Vector 또는 ndarray
        old, new = np.asarray(old, dtype=np.float64), np.asarray(new, dtype=np.float64)
        cos = float(old @ new / ((np.linalg.norm(old) * np.linalg.norm(new)) or 1.0))
        if cos < MODEL_CHECK_MIN_COS:
            raise SystemExit(
                f"DB 에 있는 임베딩이 현재 임베딩 설정({config.EMBED_PROVIDER})과 다릅니다 (cos={cos:.4f}). "
                "서로 다른 벡터가 섞이지 않도록 멈춥니다. 임베딩 모델·설정을 바꿨다면 --reset 으로 전체 적재하세요.")


def prepare(docs, a, region_on, role_on, rules):
    """배치 문서의 탐지 → 가명처리 → 청크 → 메타데이터 (기존 처리와 같음)."""
    rows, mapping, pseudo, counts = [], {}, [], {}
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
        counts[d["id"]] = len(parts)
        for i, c in enumerate(parts):
            chunk_id = f"{d['id']}#{i}"
            rows.append({"chunk_id": chunk_id, "doc_id": d["id"], "chunk_idx": i,
                         "doc_type": d.get("doc_type"), "masked_text": c,
                         "tokens": sorted(set(pii_pipeline.TOKEN_PATTERN.findall(c)) | aliases),
                         "persons": json.dumps(persons.get(chunk_id, []), ensure_ascii=False),
                         "regions": regions})
    return rows, mapping, pseudo, counts


INSERT_SQL = """INSERT INTO rag.chunks (chunk_id, doc_id, chunk_idx, doc_type, masked_text, tokens, embedding,
                                       persons, regions)
               VALUES (%(chunk_id)s, %(doc_id)s, %(chunk_idx)s, %(doc_type)s, %(masked_text)s, %(tokens)s,
                       %(embedding)s, %(persons)s::jsonb, %(regions)s)
               ON CONFLICT (chunk_id) DO UPDATE SET masked_text = EXCLUDED.masked_text,
                   tokens = EXCLUDED.tokens, embedding = EXCLUDED.embedding, doc_type = EXCLUDED.doc_type,
                   persons = EXCLUDED.persons, regions = EXCLUDED.regions"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=str(config.DATA_DIR / "holdout_labeled.jsonl"))
    ap.add_argument("--reset", action="store_true",
                    help="기존 rag.chunks / vault.token_map 비우고 시작 (한 트랜잭션, 배치 커밋 안 함)")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--max-chars", type=int, default=600)
    ap.add_argument("--batch-size", type=int, default=int(config.env("INGEST_BATCH", "30")),
                    help="한 번에 처리·커밋할 원본 문서 수 (기본 .env INGEST_BATCH 또는 30)")
    ap.add_argument("--resume", action="store_true", help="중단된 실행을 이어서 (완료 확인된 문서만 건너뜀)")
    a = ap.parse_args()
    if a.reset and a.resume:
        raise SystemExit("--reset 과 --resume 은 함께 쓸 수 없습니다.")
    if a.batch_size < 1:
        raise SystemExit("--batch-size 는 1 이상이어야 합니다.")

    if pii_pipeline.IS_DEMO and config.EMBED_PROVIDER == "api":
        print("⚠ 데모용 마스킹은 이름 등을 탐지하지 못해 원문 일부가 임베딩 API로 전송됩니다. "
              "가상 데이터라 테스트는 가능하지만, 실제 과제에서는 반드시 여러분의 모듈로 교체하세요.")

    docs = load_docs(a.data, a.limit)
    config.OUT_DIR.mkdir(exist_ok=True)
    stem = Path(a.data).stem
    pseudo_path = config.OUT_DIR / f"pseudo_{stem}.jsonl"
    part_path = pseudo_path.with_name(pseudo_path.name + ".part")
    ckpt_path = config.OUT_DIR / f"ingest_checkpoint_{stem}.json"

    region_on, role_on = config.meta_on("REGION_META"), config.meta_on("ROLE_META")
    rules = None
    if role_on:
        with db.rag_conn() as conn, conn.cursor() as cur:
            rules = load_rules(cur)

    settings = run_settings(a, region_on, role_on)
    done = {}
    if a.resume:
        if not ckpt_path.exists():
            raise SystemExit(f"이어할 체크포인트가 없습니다: {ckpt_path} (--resume 없이 처음부터 실행하세요)")
        ckpt = json.loads(ckpt_path.read_text(encoding="utf-8"))
        diff = sorted(k for k in settings.keys() | ckpt["settings"].keys()
                      if settings.get(k) != ckpt["settings"].get(k))
        if diff:
            raise SystemExit(f"중단된 실행과 조건이 다릅니다 ({', '.join(diff)}). --resume 없이 처음부터 실행하세요.")
        done = ckpt["done"]
    else:
        if ckpt_path.exists():
            print(f"이전에 중단된 적재 기록이 있습니다 ({ckpt_path.name}). --resume 이 없으므로 처음부터 다시 처리합니다.")
        ckpt_path.unlink(missing_ok=True)
        part_path.unlink(missing_ok=True)

    if a.resume:   # 체크포인트의 완료 문서가 DB 에 실제로 있는지 확인 (청크 전부 + vault 토큰)
        with db.rag_conn() as rag, rag.cursor() as rag_cur, db.vault_conn() as vault, vault.cursor() as vault_cur:
            checked = verified_done(rag_cur, vault_cur, done)
        if len(checked) < len(done):
            print(f"체크포인트 문서 {len(done) - len(checked)}건이 DB에서 확인되지 않아 다시 처리합니다.")
        # pseudo(.part)를 체크포인트와 맞춘다: 확인된 문서 줄만 남기고, 줄이 없는 문서는 다시 처리한다
        kept = read_pseudo(part_path)
        done = {d: n for d, n in checked.items() if d in kept}
        write_pseudo(part_path, [kept[d] for d in done])
        print(f"이어하기: 완료 확인 {len(done)}건 건너뜀, 남은 {len(docs) - len(done)}건 처리")

    size = (len(docs) or 1) if a.reset else a.batch_size   # --reset: 지금처럼 한 트랜잭션
    todo = [d for d in docs if d["id"] not in done]
    batches = [todo[i:i + size] for i in range(0, len(todo), size)]
    t_mask = t_embed = 0.0
    n_chunks, tokens_seen, n_batches = 0, set(), 0

    # 첫 배치는 DB 를 열기 전에 준비한다: 문서 간 토큰 충돌이면 임베딩·DB 전에 멈춘다 (기존 동작)
    t0 = time.perf_counter()
    first = prepare(batches[0], a, region_on, role_on, rules) if batches else None
    t_mask += time.perf_counter() - t0

    if batches:
        with db.rag_conn() as rag, rag.cursor() as rag_cur:
            # Vault의 오류가 RAG 커밋 전에 전파되도록 한다. 두 DB 연결의 완전한 원자성은 별도 과제다.
            with db.vault_conn() as vault, vault.cursor() as vault_cur:
                if not a.reset:
                    check_embedding_model(rag_cur)
                for i, batch in enumerate(batches):
                    if i == 0:
                        rows, mapping, pseudo, counts = first
                    else:
                        t0 = time.perf_counter()
                        rows, mapping, pseudo, counts = prepare(batch, a, region_on, role_on, rules)
                        t_mask += time.perf_counter() - t0

                    t1 = time.perf_counter()
                    # 임베딩에는 토큰을 라벨 이름으로 바꾼 사본만 넣는다 (DB에는 masked_text 그대로 저장)
                    vecs = embed([pii_pipeline.embedding_text(r["masked_text"]) for r in rows])
                    t_embed += time.perf_counter() - t1

                    if a.reset:
                        rag_cur.execute("TRUNCATE rag.chunks CASCADE")
                    rag_cur.executemany(INSERT_SQL, [r | {"embedding": v} for r, v in zip(rows, vecs)])
                    if a.reset:
                        vault_cur.execute("TRUNCATE vault.token_map")
                    save_mappings(vault_cur, mapping)
                    vault.commit()       # 1) vault 먼저
                    rag.commit()         # 2) 그다음 rag
                    append_pseudo(part_path, pseudo)                                      # 3) pseudo 줄
                    done.update(counts)
                    write_json_atomic(ckpt_path, {"settings": settings, "done": done})   # 4) 완료 기록
                    n_chunks += len(rows)
                    tokens_seen |= mapping.keys()
                    n_batches += 1
                    print(f"  배치 {n_batches}/{len(batches)} 저장: 문서 {len(done)}/{len(docs)}", flush=True)

    # 전체 완료: pseudo 를 입력 문서 순서대로 정리해 원래 이름으로 바꾸고 체크포인트를 지운다
    rows_by_id = read_pseudo(part_path)
    lost = [d["id"] for d in docs if d["id"] not in rows_by_id]
    if lost:
        raise SystemExit(f"가명처리 결과가 없는 문서 {len(lost)}건이 있습니다. --resume 으로 다시 실행하세요.")
    write_pseudo(pseudo_path, [rows_by_id[d["id"]] for d in docs])
    part_path.unlink(missing_ok=True)
    ckpt_path.unlink(missing_ok=True)

    n = len(docs)
    print(f"문서 {n}건 (이번 실행 처리 {len(todo)}건, 배치 {n_batches}개) → 청크 {n_chunks}개, 토큰 {len(tokens_seen)}종 적재")
    if todo:
        print(f"탐지+가명처리 {t_mask * 1000 / len(todo):.1f} ms/문서, "
              f"임베딩 {t_embed * 1000 / len(todo):.1f} ms/문서 ({config.EMBED_PROVIDER})")
    print(f"가명처리 결과: {pseudo_path}")


if __name__ == "__main__":
    main()
