"""문서별 후보 생성 → 판정 → DB 저장. 테이블은 infra/init/03_person_dict.sql 기준.

판정 규칙은 「person_info 규칙 정의서」(jungha/종합/인물사전/)를 따른다.
person_info.doc_id(TEXT[])에는 [문서 ID] 1개를 저장하고, 통일된 reg_id 이력은 registry.reason.merged_into 에 둔다.
최초·증분 적재와 연결만 재판정하는 배치를 지원한다. 토큰 원문 매핑은 삭제하지 않는다.
"""
import json

from . import config, db, pii_pipeline
from .person_candidates import build_doc_candidates
from .person_resolve import PersonResolver, check_look_up, check_rows, creation_key, next_created_at
from .roles import load_rules


def lock_resolution(cur):
    """기존 상태를 읽기 전 잠근다. 적재·재판정의 모든 쓰기는 같은 트랜잭션 안에서 실행한다."""
    cur.execute("SELECT pg_advisory_xact_lock(783421903)")


def load_state(cur):
    from psycopg.rows import dict_row
    # 호출자의 cursor row factory에 영향을 주지 않는다.
    with cur.connection.cursor(row_factory=dict_row) as reader:
        reader.execute('SELECT * FROM vault.person_registry ORDER BY created_at, reg_id')
        registry = list(reader.fetchall())
        for reg in registry:
            reg['merged_into'] = reg['reason'].get('merged_into')
        reader.execute('SELECT * FROM vault.person_info ORDER BY created_at, person_id')
        rows = list(reader.fetchall())
        for row in rows:
            if len(row['doc_id']) != 1:
                raise ValueError('person_info.doc_id에는 문서 ID 하나만 있어야 합니다.')
            row['doc_id'] = row['doc_id'][0]
            row['info_label'] = row['info_label'] or []
            if not row['evidence']:
                raise ValueError('판정 근거가 없는 기존 후보입니다.')
        reader.execute('SELECT token, label, normalized FROM vault.token_map')
        mapping = {r['token']: r for r in reader.fetchall()}
    return rows, registry, mapping


def persisted_links(rows):
    """원문을 다시 읽지 않고 문서 내 M4 근거를 재사용한다."""
    return [(r['person_id'], peer, 'M4') for r in rows
            for peer in r['evidence'].get('document_links',
                r['evidence'].get('matched', []) if r['evidence'].get('rule') == 'M4' else [])]


def validate_resolution(cur, result, mapping):
    cur.execute('SELECT person_id FROM vault.person_info')
    if {pid for pid, in cur.fetchall()} - {r['person_id'] for r in result['rows']}:
        raise ValueError('기존 후보가 판정 결과에서 누락되었습니다.')
    check_rows(result['rows'], result['registry'], token_info=mapping)
    cur.execute('SELECT chunk_id, doc_id FROM rag.chunks')
    chunks = [{'chunk_id': cid, 'doc_id': did} for cid, did in cur.fetchall()]
    check_look_up(result['rows'], result['look_up'], chunks)


def prepare_candidates(docs, max_chars, rules, detected=None):
    """탐지 결과 주입은 테스트용. 기본 실행은 정답 라벨을 읽지 않는다."""
    from .ingest import collect_mapping, process_doc
    chunks, mapping, pseudo, candidates, links = [], {}, [], [], []
    if len({d['id'] for d in docs}) != len(docs):
        raise ValueError('입력 문서 ID가 중복됩니다.')
    for doc in docs:
        result = process_doc(doc, max_chars, config.meta_on('REGION_META'),
                             config.meta_on('ROLE_META'), rules,
                             detected=detected.get(doc['id']) if detected else None)
        masked_parts = [r['masked_text'] for r in result['rows']]
        masked = result['masked_text']
        rows, doc_links = build_doc_candidates(doc['id'], doc['text'], masked,
                                               result['records'], masked_parts, rules)
        candidates.extend(rows)
        links.extend(doc_links)
        chunks.extend(result['rows'])
        pseudo.append(result['pseudo'])
        for record in result['records']:
            collect_mapping(mapping, record)
    return chunks, mapping, pseudo, candidates, links


def assign_created_times(cur, rows, table, id_column):
    """공통 트랜잭션 잠금 아래에서 신규 행의 시각만 발급한다. 기존 시각은 보존한다."""
    from psycopg import sql
    cur.execute(sql.SQL('SELECT {}, created_at FROM vault.{}').format(
        sql.Identifier(id_column), sql.Identifier(table)))
    existing = dict(cur.fetchall())
    latest = max(existing.values(), default=None)
    for row in rows:
        if row[id_column] in existing:
            row['created_at'] = existing[row[id_column]]
        else:
            latest = next_created_at(latest)
            row['created_at'] = latest


def save_resolution(cur, result):
    """DDL(03_person_dict.sql) 그대로 저장한다. 커밋은 호출자 책임.

    - person_info.doc_id 는 TEXT[] 이고 문서별 후보이므로 {doc_id} 1개만 담는다.
    - DDL 에 merged_into 컬럼이 없으므로 통일돼 사라진 reg_id 는 reason.merged_into 에 남긴다.
    """
    lock_resolution(cur)
    # 최대 시각 조회와 INSERT는 같은 잠금을 커밋까지 유지한다.
    registry = [r for _, r in sorted(enumerate(result['registry']), key=lambda pair:
                (0, creation_key(pair[1], 'reg_id')) if pair[1].get('created_at') else (1, pair[0]))]
    assign_created_times(cur, registry, 'person_registry', 'reg_id')
    assign_created_times(cur, result['rows'], 'person_info', 'person_id')
    for reg in registry:
        reason = dict(reg['reason'])
        if reg.get('merged_into'):
            reason['merged_into'] = reg['merged_into']
        cur.execute("""INSERT INTO vault.person_registry (reg_id, reason, created_at)
                       VALUES (%s, %s::jsonb, %s)
                       ON CONFLICT (reg_id) DO UPDATE SET reason = EXCLUDED.reason
                       WHERE person_registry.reason IS DISTINCT FROM EXCLUDED.reason""",
                    (reg['reg_id'], json.dumps(reason), reg['created_at']))
    for row in result['rows']:
        cur.execute("""INSERT INTO vault.person_info
                    (person_id, doc_id, token, role, info_label, evidence, reg_id, resolution_status, created_at)
                    VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s)
                    ON CONFLICT (person_id) DO UPDATE SET
                        info_label = EXCLUDED.info_label, evidence = EXCLUDED.evidence,
                        reg_id = EXCLUDED.reg_id, resolution_status = EXCLUDED.resolution_status
                    WHERE (person_info.info_label, person_info.evidence, person_info.reg_id,
                           person_info.resolution_status) IS DISTINCT FROM
                          (EXCLUDED.info_label, EXCLUDED.evidence, EXCLUDED.reg_id,
                           EXCLUDED.resolution_status)""",
                    (row['person_id'], [row['doc_id']], row['token'], row['role'], row['info_label'],
                     json.dumps(row['evidence']), row['reg_id'], row['resolution_status'], row['created_at']))
    # 전체 후보의 검증된 스냅샷. 검색에는 커밋 전의 이전 상태 또는 새 상태만 보인다.
    cur.execute('DELETE FROM rag.look_up')
    cur.executemany("""INSERT INTO rag.look_up (chunk_id, person_id, token, reg_id, role)
                       VALUES (%(chunk_id)s, %(person_id)s, %(token)s, %(reg_id)s, %(role)s)""",
                    result['look_up'])


def store(cur, chunks, mapping, result, *, reset=False):
    from .ingest import INSERT_SQL, save_mappings
    # 전체 판정은 직렬화한다. 실패하면 삭제를 포함해 모두 롤백한다.
    lock_resolution(cur)
    if not reset:
        cur.execute('SELECT DISTINCT doc_id FROM rag.chunks WHERE doc_id = ANY(%s)',
                    (list({c['doc_id'] for c in chunks}),))
        if cur.fetchone():
            raise ValueError('기존 데이터와 문서 ID가 중복됩니다. 새 문서만 입력하세요.')
    if reset:
        cur.execute('DELETE FROM rag.look_up')
        cur.execute('DELETE FROM vault.person_info')
        cur.execute('DELETE FROM vault.person_registry')
        cur.execute('DELETE FROM rag.chunks')
    save_mappings(cur, mapping)
    cur.executemany(INSERT_SQL, chunks)
    _, _, all_mapping = load_state(cur)
    validate_resolution(cur, result, all_mapping)
    save_resolution(cur, result)


def run(docs, args, pseudo_path):
    from .ingest import embed, write_pseudo, make_pool, _worker_detect, check_embedding_model
    if args.resume:
        raise ValueError('PERSON_DICT=on은 한 트랜잭션으로 처리합니다. 실패한 입력은 --resume 없이 다시 실행하세요.')
    if (config.env('NAME_LINK', 'off') or 'off').lower() != 'off':
        raise ValueError('문서별 이름 후보를 보존하려면 NAME_LINK=off로 실행하세요.')
    with db.ingest_conn() as conn, conn.cursor() as cur:
        lock_resolution(cur)
        existing, registry, mapping_before = load_state(cur) if not args.reset else ([], [], {})
        if not args.reset:
            cur.execute('SELECT DISTINCT doc_id FROM rag.chunks WHERE doc_id = ANY(%s)',
                        ([d['id'] for d in docs],))
            if cur.fetchone() or {r['doc_id'] for r in existing} & {d['id'] for d in docs}:
                raise ValueError('기존 데이터와 문서 ID가 중복됩니다. 새 문서만 입력하세요.')
            check_embedding_model(cur)
        rules = load_rules(cur)
        pool = make_pool(args.workers, args, config.meta_on('REGION_META'), config.meta_on('ROLE_META'), rules)
        try:
            detected = dict(zip((d['id'] for d in docs), pool.map(_worker_detect, docs))) if pool else None
            chunks, mapping, pseudo, rows, links = prepare_candidates(docs, args.max_chars, rules, detected)
        finally:
            if pool:
                pool.terminate()
                pool.join()
        # save_mappings가 원문 정규화 충돌을 검사한다. 실패하면 전체 롤백한다.
        from .ingest import save_mappings
        save_mappings(cur, mapping)
        assign_created_times(cur, rows, 'person_info', 'person_id')
        result = PersonResolver(existing + rows, registry, token_info=mapping_before | mapping,
                                role_group=rules.role_group,
                                links=persisted_links(existing) + links).resolve()
        check_rows(result['rows'], result['registry'])
        for start in range(0, len(chunks), args.batch_size):
            batch = chunks[start:start + args.batch_size]
            vectors = embed([pii_pipeline.embedding_text(c['masked_text']) for c in batch])
            if len(vectors) != len(batch):
                raise ValueError('임베딩 개수가 청크 개수와 다릅니다.')
            for chunk, vector in zip(batch, vectors):
                chunk['embedding'] = vector
        store(cur, chunks, mapping, result, reset=args.reset)
    write_pseudo(pseudo_path, pseudo)
    report = pseudo_path.with_name('person_resolution_' + pseudo_path.stem + '.json')
    report.write_text(json.dumps({'candidates': len(rows), 'resolved': sum(r['reg_id'] is not None for r in rows),
                                 'registry': len(result['registry']), 'look_up': len(result['look_up']),
                                 'review': result['review']}, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f"인물사전 적재 완료: 문서 {len(docs)}, 후보 {len(rows)}, 검색 연결 {len(result['look_up'])}")
    print(f"판정 요약: {report}")


def rejudge():
    """저장된 후보의 근거만 재판정한다. 탐지·가명처리·임베딩 및 chunks 쓰기를 하지 않는다."""
    with db.ingest_conn() as conn, conn.cursor() as cur:
        lock_resolution(cur)
        rows, registry, mapping = load_state(cur)
        rules = load_rules(cur)
        result = PersonResolver(rows, registry, mapping, role_group=rules.role_group,
                                links=persisted_links(rows)).resolve(rejudge_unresolved=True)
        validate_resolution(cur, result, mapping)
        save_resolution(cur, result)
    return result


def main():
    import argparse
    parser = argparse.ArgumentParser(description='본문·임베딩을 유지하고 미확정 인물 후보를 재판정합니다.')
    parser.parse_args()
    result = rejudge()
    print(f"재판정 완료: 후보 {len(result['rows'])}, 검토 {len(result['review'])}건")


if __name__ == '__main__':
    main()
