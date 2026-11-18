"""격리된 DB에서만 실행: PERSON_TEST_DSN=... pytest ...

실제 infra 테이블·권한·SQL을 사용한다. 탐지/임베딩만 고정해 모델 다운로드 없이 연결을 검증한다.
PERSON_TEST_DSN은 반드시 전용 테스트 DB를 가리켜야 한다. 테스트 후 자신이 넣은 데이터를 지운다.
"""
import json
import os
import re
import sys
from pathlib import Path

import numpy as np
import psycopg
import pytest

from starter import ingest, person_ingest, pii_pipeline, search, db

DSN = os.getenv('PERSON_TEST_DSN')
pytestmark = pytest.mark.skipif(not DSN, reason='전용 PERSON_TEST_DSN 필요')


def detect(text):
    out = []
    for pattern, label in [(r'김민준|민준|이서연|서연', 'person'), (r'010-\d{4}-\d{4}', 'phone_number')]:
        for m in re.finditer(pattern, text):
            out.append({'label': label, 'start': m.start(), 'end': m.end()})
    return sorted(out, key=lambda s: s['start'])


def test_ingest_search_rollback_and_permissions(tmp_path, monkeypatch):
    # 환경의 기존 DB를 실수로 사용하지 않도록 이름과 비어 있는지 모두 검사한다.
    from psycopg.conninfo import conninfo_to_dict, make_conninfo
    options = conninfo_to_dict(DSN)
    assert options['dbname'].endswith('_test')
    with psycopg.connect(DSN) as admin:
        with admin.cursor() as cur:
            cur.execute('SELECT count(*) FROM rag.chunks')
            assert cur.fetchone()[0] == 0, '빈 전용 테스트 DB에서만 실행하세요.'
            cur.execute(Path('infra/init/03_person_dict.sql').read_text())
            cur.execute(Path('infra/init/03_person_dict.sql').read_text())  # 이관 재실행 가능
            cur.execute("ALTER ROLE app_ingest LOGIN PASSWORD 'integration-only'")
            cur.execute("ALTER ROLE app_search LOGIN PASSWORD 'integration-only'")
            cur.execute("""DO $$ BEGIN
                IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='app_reset') THEN
                    CREATE ROLE app_reset LOGIN PASSWORD 'integration-only';
                END IF;
                END $$""")
            cur.execute('GRANT pii_ingest TO app_reset')
            cur.execute('GRANT TRUNCATE ON rag.chunks, rag.look_up, vault.token_map, vault.person_info, vault.person_registry TO app_reset')
    ingest_dsn = make_conninfo(DSN, user='app_ingest', password='integration-only')
    search_dsn = make_conninfo(DSN, user='app_search', password='integration-only')
    monkeypatch.setenv('INGEST_DSN', ingest_dsn)
    monkeypatch.setenv('RESET_DSN', make_conninfo(DSN, user='app_reset', password='integration-only', options='-c statement_timeout=10000'))
    monkeypatch.setenv('PERSON_DICT', 'on')
    monkeypatch.setenv('NAME_LINK', 'off')
    monkeypatch.setenv('SEARCH_MODE', 'and')
    monkeypatch.setenv('RERANK_TOP', '0')
    monkeypatch.setenv('PERSON_LINK_UNIQUE', 'off')
    monkeypatch.setenv('PERSON_LINK_GIVEN', 'off')
    monkeypatch.setattr(pii_pipeline.config, 'HMAC_KEY', 'integration-synthetic-key')
    pii_pipeline._key.cache_clear()
    monkeypatch.setattr(pii_pipeline, 'detect', detect)
    monkeypatch.setattr(ingest, 'embed', lambda texts, **kw: [np.array([1., 0., 0.])] * len(texts))
    monkeypatch.setattr(search, 'embed', lambda texts, **kw: [np.array([1., 0., 0.])] * len(texts))
    search._rules_cache.clear()
    docs = [
        {'id': 'A', 'text': '고객 김민준 전화 010-1111-1111\n' + '일반 안내입니다. ' * 15},
        {'id': 'B', 'text': '고객 김민준 전화 010-1111-1111'},
        {'id': 'C', 'text': '김민준 과장 전화 010-2222-2222'},
        {'id': 'D', 'text': '상담원 이서연 안내\n서연 상담원 확인'},
    ]
    path = tmp_path / 'synthetic.jsonl'
    path.write_text('\n'.join(json.dumps(d, ensure_ascii=False) for d in docs))
    monkeypatch.setattr(ingest.config, 'OUT_DIR', tmp_path / 'out')
    monkeypatch.setattr(sys, 'argv', ['ingest', '--data', str(path), '--max-chars', '80'])
    try:
        ingest.main()   # 실제 CLI 진입점 → 후보 생성·판정·DB 커밋
        with psycopg.connect(DSN) as conn, conn.cursor() as cur:
            cur.execute('SELECT doc_id, reg_id, resolution_status FROM vault.person_info ORDER BY doc_id')
            rows = cur.fetchall()
            assert len(rows) == 5
            assert rows[0][0] == ['A'] and rows[1][0] == ['B']
            assert rows[0][1] == rows[1][1] and rows[0][1]
            cur.execute("SELECT count(*) FROM rag.look_up WHERE chunk_id = 'A#1'")
            assert cur.fetchone()[0] == 0  # 이름이 없는 후속 청크는 연결하지 않는다
            cur.execute("SELECT DISTINCT token FROM vault.person_info WHERE doc_id && ARRAY['A','B','C']")
            assert [r[0] for r in cur.fetchall()] == [pii_pipeline.make_token('person', '김민준')]
        with psycopg.connect(search_dsn) as conn:
            from pgvector.psycopg import register_vector
            register_vector(conn)
            customer = search.search('고객 김민준', conn=conn, with_meta=True)
            assert {d['doc_id'] for d in customer[2]} == {'A', 'B'}
            assert all(d['chunk_id'] != 'A#1' for d in customer[2])
            staff = search.search('직원 김민준', conn=conn)
            assert [d['doc_id'] for d in staff[2]] == ['C']
            ambiguous = search.search('김민준', conn=conn, with_meta=True)
            assert {d['doc_id'] for d in ambiguous[2]} == {'A', 'B', 'C'}
            assert ambiguous[3]['ask_person']
            assert search.search('민준', conn=conn)[2] == []  # 미등록 이름: fallback 금지
            assert [d['doc_id'] for d in search.search('서연', conn=conn)[2]] == ['D']
            # pending 후보가 chunks에만 있으면 인물 검색으로 노출하지 않는다.
        with psycopg.connect(DSN) as conn, conn.cursor() as cur:
            cur.execute('SELECT chunk_id, masked_text, embedding::text FROM rag.chunks ORDER BY chunk_id')
            original_chunks = cur.fetchall()
            cur.execute('SELECT person_id, created_at FROM vault.person_info ORDER BY person_id')
            original_people = cur.fetchall()
        # 추가 문서는 기존 후보와 비교하지만 기존 본문·임베딩·후보 ID는 그대로 둔다.
        extra = tmp_path / 'extra.jsonl'
        extra.write_text('\n'.join(json.dumps(d, ensure_ascii=False) for d in [
            {'id': 'E', 'text': '고객 김민준 전화 010-1111-1111'},
            {'id': 'F', 'text': '고객 김민준 안내'},
        ]))
        monkeypatch.setattr(sys, 'argv', ['ingest', '--data', str(extra)])
        ingest.main()
        with psycopg.connect(DSN) as conn, conn.cursor() as cur:
            cur.execute("SELECT chunk_id, masked_text, embedding::text FROM rag.chunks WHERE doc_id <> ALL(ARRAY['E','F']) ORDER BY chunk_id")
            assert cur.fetchall() == original_chunks
            cur.execute('SELECT person_id, created_at FROM vault.person_info WHERE person_id = ANY(%s) ORDER BY person_id',
                        ([p[0] for p in original_people],))
            assert cur.fetchall() == original_people
            cur.execute("SELECT reg_id FROM vault.person_info WHERE doc_id && ARRAY['A','B','E']")
            assert len({r[0] for r in cur.fetchall()}) == 1
            # F와 직원 C는 none이다. 새 근거로 다른 고객과 이어져도 C와는 분리해야 한다.
            cur.execute("""UPDATE vault.person_info SET info_label=(
                           SELECT info_label FROM vault.person_info WHERE doc_id=ARRAY['A'])
                           WHERE doc_id=ARRAY['F']""")
            cur.execute('SELECT chunk_id, masked_text, embedding::text FROM rag.chunks ORDER BY chunk_id')
            before_rejudge = cur.fetchall()
        # 재판정은 탐지·임베딩을 호출할 수 없다.
        with monkeypatch.context() as isolated:
            def forbidden(*args, **kwargs):
                raise AssertionError('재판정에서 본문을 재처리하면 안 됩니다.')
            isolated.setattr(ingest, 'embed', forbidden)
            isolated.setattr(pii_pipeline, 'detect', forbidden)
            person_ingest.rejudge()
        with psycopg.connect(DSN) as conn, conn.cursor() as cur:
            cur.execute('SELECT chunk_id, masked_text, embedding::text FROM rag.chunks ORDER BY chunk_id')
            assert cur.fetchall() == before_rejudge
            cur.execute("SELECT reg_id FROM vault.person_info WHERE doc_id && ARRAY['A','F']")
            assert len({r[0] for r in cur.fetchall()}) == 1
            cur.execute('SELECT count(*) FROM rag.look_up l JOIN vault.person_info p USING(person_id) WHERE l.reg_id IS DISTINCT FROM p.reg_id OR l.token <> p.token')
            assert cur.fetchone()[0] == 0
        with psycopg.connect(DSN) as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM rag.look_up WHERE chunk_id = 'D#0'")
            cur.execute("UPDATE vault.person_info SET resolution_status='pending', reg_id=NULL WHERE doc_id=ARRAY['D']")
        with psycopg.connect(search_dsn) as conn:
            from pgvector.psycopg import register_vector
            register_vector(conn)
            assert search.search('이서연', conn=conn)[2] == []
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute('SELECT * FROM vault.person_info')
            conn.rollback()
        # 명시적 reset 없이는 기존 데이터를 덮어쓰지 않는다.
        with pytest.raises(ValueError, match='기존 데이터'):
            ingest.main()
        # 연결표까지 저장한 뒤 실패해도 두 테이블 모두 이전 상태로 돌아가야 한다.
        with psycopg.connect(DSN) as conn:
            before_people = conn.execute('SELECT * FROM vault.person_info ORDER BY person_id').fetchall()
            before_lookup = conn.execute('SELECT * FROM rag.look_up ORDER BY chunk_id, person_id').fetchall()
        save = person_ingest.save_resolution
        with monkeypatch.context() as isolated:
            def fail_after_save(*args):
                save(*args)
                raise RuntimeError('after save')
            isolated.setattr(person_ingest, 'save_resolution', fail_after_save)
            with pytest.raises(RuntimeError, match='after save'):
                person_ingest.rejudge()
        with psycopg.connect(DSN) as conn:
            assert conn.execute('SELECT * FROM vault.person_info ORDER BY person_id').fetchall() == before_people
            assert conn.execute('SELECT * FROM rag.look_up ORDER BY chunk_id, person_id').fetchall() == before_lookup
        # reset 후 저장 실패는 삭제까지 원상 복구한다.
        def fail(*args):
            raise RuntimeError('injected failure')
        monkeypatch.setattr(person_ingest, 'save_resolution', fail)
        monkeypatch.setattr(sys, 'argv', ['ingest', '--data', str(path), '--reset'])
        with pytest.raises(RuntimeError, match='injected'):
            ingest.main()
        with psycopg.connect(DSN) as conn:
            assert conn.execute('SELECT count(*) FROM vault.person_info').fetchone()[0] == 7
        monkeypatch.setattr(person_ingest, 'save_resolution', save)
        # off --reset: 한 연결에서 적재 데이터를 교체하고 policy/audit는 보존한다.
        with psycopg.connect(DSN) as conn:
            protected = [conn.execute(f'SELECT count(*) FROM {table}').fetchone()[0]
                         for table in ('policy.users', 'audit.query_log', 'audit.restore_log')]
            snapshot = conn.execute('SELECT * FROM rag.chunks ORDER BY chunk_id').fetchall()
        monkeypatch.setenv('PERSON_DICT', 'off')
        # TRUNCATE 이후 예외가 발생해도 원래 데이터가 돌아온다.
        with monkeypatch.context() as isolated:
            isolated.setattr(ingest, 'save_mappings', fail)
            with pytest.raises(RuntimeError, match='injected'):
                ingest.main()
        with psycopg.connect(DSN) as conn:
            # 벡터의 numpy 배열 비교를 피하려고 문자열 표현으로 비교한다.
            assert str(conn.execute('SELECT * FROM rag.chunks ORDER BY chunk_id').fetchall()) == str(snapshot)
        ingest.main()
        with psycopg.connect(DSN) as conn:
            for table in ('vault.person_info', 'vault.person_registry', 'rag.look_up'):
                assert conn.execute(f'SELECT count(*) FROM {table}').fetchone()[0] == 0
            assert [conn.execute(f'SELECT count(*) FROM {table}').fetchone()[0]
                    for table in ('policy.users', 'audit.query_log', 'audit.restore_log')] == protected
        monkeypatch.setenv('PERSON_DICT', 'on')
        ingest.main()  # on --reset도 같은 파일을 충돌 없이 다시 적재
        with psycopg.connect(DSN) as conn:
            assert conn.execute('SELECT count(*) FROM vault.person_info').fetchone()[0] == 5
    finally:
        search._rules_cache.clear()
        pii_pipeline._key.cache_clear()
        with psycopg.connect(DSN) as conn:
            conn.execute('DELETE FROM rag.look_up')
            conn.execute('DELETE FROM vault.person_info')
            conn.execute('DELETE FROM vault.person_registry')
            conn.execute('DELETE FROM rag.chunks')
            conn.execute('DELETE FROM vault.token_map')


def test_created_at_survives_reload_and_merge():
    from psycopg.conninfo import conninfo_to_dict
    from starter.person_resolve import PersonResolver, new_evidence
    assert conninfo_to_dict(DSN)['dbname'].endswith('_test')
    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        assert cur.execute('SELECT count(*) FROM vault.person_info').fetchone()[0] == 0
        cur.execute(Path('infra/init/03_person_dict.sql').read_text())
        # 알파벳·UUID 정렬과 반대 순서로, 동일 트랜잭션에서 등록한다.
        regs = [{'reg_id': rid, 'created_at': f'2026-11-17T00:00:00.00000{i}+00:00', 'reason': {'reason_code': 'H1'}}
                for i, rid in enumerate(['reg_z', 'reg_a'], 1)]
        rows = []
        for pid, rid in [('tmp_z', 'reg_z'), ('tmp_a', 'reg_a')]:
            ev = new_evidence()
            ev.update(level='high', rule='H1')
            rows.append(dict(person_id=pid, doc_id=pid, token='<PERSON_ab>', role='customer',
                             info_label=['<RRN_ab>'], evidence=ev, reg_id=rid, resolution_status='resolved'))
        person_ingest.save_resolution(cur, dict(registry=regs, rows=rows, look_up=[]))
        cur.execute('SELECT count(DISTINCT created_at) FROM vault.person_registry')
        assert cur.fetchone()[0] == 2
        loaded, registry, _ = person_ingest.load_state(cur)
        assert [r['reg_id'] for r in registry] == ['reg_z', 'reg_a']
        assert [r['person_id'] for r in loaded] == ['tmp_z', 'tmp_a']
        newcomer = dict(person_id='tmp_new', doc_id='new', token='<PERSON_ab>', role='customer',
                        info_label=['<RRN_ab>'], evidence=new_evidence(), reg_id=None, resolution_status='pending')
        # 로더 정렬만 믿지 않고 병합 함수 자체도 순번을 사용하는지 확인한다.
        result = PersonResolver(loaded + [newcomer], list(reversed(registry)),
                                token_info={'<RRN_ab>': {'label': 'resident_registration_number'}}).resolve()
        assert {r['reg_id'] for r in result['rows']} == {'reg_z'}
        person_ingest.save_resolution(cur, result)
        loaded, registry, _ = person_ingest.load_state(cur)
        assert {r['reg_id'] for r in loaded} == {'reg_z'}
        assert next(r for r in registry if r['reg_id'] == 'reg_a')['merged_into'] == 'reg_z'
        assert [r['person_id'] for r in loaded] == ['tmp_z', 'tmp_a', 'tmp_new']
        again = PersonResolver(loaded, registry).resolve(rejudge_unresolved=True)
        person_ingest.save_resolution(cur, again)
        assert [r['person_id'] for r in person_ingest.load_state(cur)[0]] == ['tmp_z', 'tmp_a', 'tmp_new']
        conn.rollback()  # 이 테스트의 등록 행을 남기지 않는다.


def test_legacy_ties_and_future_max_keep_schema_and_timestamps():
    from psycopg.conninfo import conninfo_to_dict
    assert conninfo_to_dict(DSN)['dbname'].endswith('_test')
    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        assert cur.execute('SELECT count(*) FROM vault.person_registry').fetchone()[0] == 0
        columns = cur.execute("SELECT table_name, column_name, data_type FROM information_schema.columns WHERE table_schema='vault' ORDER BY 1,2").fetchall()
        assert not any(c[1] == 'registration_order' for c in columns)
        cur.execute("INSERT INTO vault.person_registry(reg_id, reason, created_at) VALUES ('reg_z', '{}', '2099-01-01 00:00:00+00'), ('reg_a', '{}', '2099-01-01 00:00:00+00')")
        rows, registry, _ = person_ingest.load_state(cur)
        assert [r['reg_id'] for r in registry] == ['reg_a', 'reg_z']  # 과거 동률은 ID로 안정 정렬만 가능
        original = {r['reg_id']: r['created_at'] for r in registry}
        registry.append({'reg_id': 'reg_new', 'reason': {}})
        result = dict(rows=rows, registry=registry, look_up=[])
        person_ingest.save_resolution(cur, result)
        assert registry[-1]['created_at'] > original['reg_z']
        saved = cur.execute('SELECT reg_id, created_at FROM vault.person_registry ORDER BY created_at, reg_id').fetchall()
        person_ingest.save_resolution(cur, result)
        assert cur.execute('SELECT reg_id, created_at FROM vault.person_registry ORDER BY created_at, reg_id').fetchall() == saved
        assert dict(saved)['reg_a'] == original['reg_a'] and dict(saved)['reg_z'] == original['reg_z']
        assert cur.execute("SELECT table_name, column_name, data_type FROM information_schema.columns WHERE table_schema='vault' ORDER BY 1,2").fetchall() == columns
        conn.rollback()


def test_concurrent_registration_waits_for_committed_max():
    import time
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from psycopg.conninfo import conninfo_to_dict
    assert conninfo_to_dict(DSN)['dbname'].endswith('_test')
    ready = Event()
    backend = []

    def save_second():
        with psycopg.connect(DSN) as conn, conn.cursor() as cur:
            backend.append(conn.info.backend_pid)
            ready.set()
            person_ingest.save_resolution(cur, dict(rows=[], registry=[{'reg_id': 'reg_a', 'reason': {}}], look_up=[]))

    with psycopg.connect(DSN) as first, ThreadPoolExecutor(max_workers=1) as pool:
        try:
            assert first.execute('SELECT count(*) FROM vault.person_registry').fetchone()[0] == 0
            with first.cursor() as cur:
                person_ingest.save_resolution(cur, dict(rows=[], registry=[{'reg_id': 'reg_z', 'reason': {}}], look_up=[]))
            future = pool.submit(save_second)
            try:
                assert ready.wait(5)
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    blocked = first.execute('SELECT %s = ANY(pg_blocking_pids(%s))',
                                            (first.info.backend_pid, backend[0])).fetchone()[0]
                    if blocked:
                        break
                    time.sleep(.01)
                assert blocked, '두 번째 연결이 최대 시각 조회 전에 같은 트랜잭션 잠금에서 대기해야 함'
            finally:
                first.commit()  # 실패한 검사도 두 번째 연결을 해제해 테스트가 멈추지 않게 한다.
            future.result(timeout=10)
            saved = first.execute('SELECT reg_id, created_at FROM vault.person_registry ORDER BY created_at').fetchall()
            assert [r[0] for r in saved] == ['reg_z', 'reg_a']
            assert saved[0][1] < saved[1][1]
        finally:
            first.rollback()
            first.execute("DELETE FROM vault.person_registry WHERE reg_id IN ('reg_z', 'reg_a')")
            first.commit()


