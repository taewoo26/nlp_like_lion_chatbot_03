"""현재 파이프라인을 PostgreSQL 세션 임시 테이블에서 평가한다.

기존 rag.chunks/vault.token_map과 결과 파일을 덮어쓰지 않는다.
원본 검색 채점기는 수정하지 않는다. local(1536)과 bge-m3(1024)를 같은 임시 DB 구조에서 평가한다.
"""
import argparse
from contextlib import redirect_stdout
from datetime import datetime
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from unittest.mock import patch
from zoneinfo import ZoneInfo


class TemporaryCursor:
    def __init__(self, cursor):
        self.cursor = cursor

    @staticmethod
    def rewrite(sql):
        return (sql.replace('rag.chunks', 'pg_temp.eval_chunks')
                .replace('rag.person_roles', 'pg_temp.eval_person_roles')
                .replace('vault.token_map', 'pg_temp.eval_token_map'))

    def __enter__(self):
        self.cursor.__enter__()
        return self

    def __exit__(self, *args):
        return self.cursor.__exit__(*args)

    def execute(self, sql, params=None):
        return self.cursor.execute(self.rewrite(sql), params)

    def executemany(self, sql, params):
        return self.cursor.executemany(self.rewrite(sql), params)

    def __getattr__(self, name):
        return getattr(self.cursor, name)


class TemporaryConnection:
    def __init__(self, connection):
        self.connection = connection

    def __enter__(self):
        return self

    def __exit__(self, error_type, *_):
        if error_type is None:
            self.connection.commit()
        else:
            self.connection.rollback()

    def cursor(self):
        return TemporaryCursor(self.connection.cursor())

    def close(self):
        # 모든 질의가 같은 임시 표를 사용한다. 실제 연결은 main의 finally에서 닫는다.
        pass


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--data', default='data/holdout/holdout_labeled.jsonl')
    ap.add_argument('--queries', default='data/holdout/holdout_retrieval_queries.jsonl')
    ap.add_argument('--gold', default='data/holdout/holdout_retrieval_queries_gold.jsonl')
    ap.add_argument('--persons-gold', default='data/holdout/holdout_persons_gold.jsonl')
    ap.add_argument('--out-dir', required=True)
    ap.add_argument('-k', type=int, default=20)
    ap.add_argument('--embed-provider', choices=('local', 'bge-m3'), default='local')
    ap.add_argument('--exact-vector-scan', action='store_true',
                    help='HNSW 근사 탐색을 끄고 필터링된 모든 청크의 코사인 거리를 비교')
    a = ap.parse_args()
    os.environ['EMBED_PROVIDER'] = a.embed_provider
    os.environ['EMBED_DIM'] = '1024' if a.embed_provider == 'bge-m3' else '1536'
    if a.embed_provider == 'bge-m3':
        os.environ['EMBED_MODEL'] = 'BAAI/bge-m3'
    from starter import config, db, ingest, pii_pipeline, run_queries

    out = Path(a.out_dir).resolve()
    out.mkdir(parents=True, exist_ok=False)
    root = config.ROOT
    tracked = ['starter/pii_pipeline.py', 'starter/address.py', 'starter/admin_regions.json',
               'starter/person_roles.py', 'starter/person_role_config.json',
               'starter/person_role_mapping.py', 'infra/init/02_person_roles.sql',
               'starter/email_normalization.py', 'starter/ingest.py', 'starter/search.py',
               'starter/embed.py', 'detection/combo.py', 'detection/extra_rules.py',
               'detection/iban.py', 'eval/eval_holdout_retrieval.py', 'eval/run_isolated_pipeline.py']
    inputs = [a.data, a.queries, a.gold, a.persons_gold]
    sha = lambda p: hashlib.sha256(Path(p).read_bytes()).hexdigest()
    manifest = {
        'status': 'running', 'policy_version': pii_pipeline.POLICY_VERSION,
        'started_at': datetime.now(ZoneInfo('Asia/Seoul')).isoformat(),
        'git_head': subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip(),
        'embed_provider': config.EMBED_PROVIDER, 'embed_dim': config.EMBED_DIM,
        'embed_model': config.EMBED_MODEL if a.embed_provider == 'bge-m3' else 'character-ngram-hash',
        'key_id': config.env('HMAC_KEY_ID') or 'not_configured',
        'storage': 'PostgreSQL session temporary tables; indexes copied; ANALYZE after ingest',
        'k': a.k, 'code_sha256': {p: sha(root / p) for p in tracked},
        'exact_vector_scan': a.exact_vector_scan,
        'input_sha256': {p: sha(p) for p in inputs},
    }
    (out / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
    rag = vault = None
    try:
        rag, vault = db.rag_conn(), db.vault_conn()
        manifest['original_counts_before'] = {
            'rag_chunks': rag.execute('SELECT count(*) FROM rag.chunks').fetchone()[0],
            'vault_tokens': vault.execute('SELECT count(*) FROM vault.token_map').fetchone()[0],
        }
        existing_roles = rag.execute("SELECT to_regclass('rag.person_roles')").fetchone()[0]
        manifest['original_counts_before']['rag_person_roles'] = (
            rag.execute('SELECT count(*) FROM rag.person_roles').fetchone()[0] if existing_roles else None)
        # 영구 rag.chunks의 vector(1536) 및 인덱스를 건드리지 않고 임시 테이블만 차원을 바꾼다.
        rag.execute(f'''CREATE TEMP TABLE eval_chunks (
            chunk_id text PRIMARY KEY, doc_id text NOT NULL, chunk_idx int NOT NULL,
            doc_type text, masked_text text NOT NULL, tokens text[] NOT NULL DEFAULT '{{}}',
            embedding vector({config.EMBED_DIM}) NOT NULL,
            created_at timestamptz DEFAULT now()
        )''')
        rag.execute('CREATE INDEX ON pg_temp.eval_chunks USING gin (tokens)')
        rag.execute('CREATE INDEX ON pg_temp.eval_chunks USING hnsw (embedding vector_cosine_ops)')
        rag.execute('CREATE INDEX ON pg_temp.eval_chunks (doc_id)')
        # 실제 마이그레이션 정의를 임시 테이블로 치환해 구문·제약도 함께 검증한다.
        role_schema = (root / 'infra/init/02_person_roles.sql').read_text(encoding='utf-8')
        role_schema = (role_schema.replace('CREATE TABLE IF NOT EXISTS rag.person_roles',
                                           'CREATE TEMP TABLE eval_person_roles')
                       .replace('rag.person_roles', 'pg_temp.eval_person_roles')
                       .replace('rag.chunks', 'pg_temp.eval_chunks'))
        for statement in role_schema.split(';'):
            if statement.strip():
                rag.execute(statement)
        vault.execute('CREATE TEMP TABLE eval_token_map (LIKE vault.token_map INCLUDING ALL)')
        rag.commit()
        vault.commit()
        log = io.StringIO()
        started = time.perf_counter()
        with patch.object(config, 'OUT_DIR', out), \
             patch.object(db, 'rag_conn', return_value=TemporaryConnection(rag)), \
             patch.object(db, 'vault_conn', return_value=TemporaryConnection(vault)), \
             redirect_stdout(log):
            with patch.object(sys, 'argv', ['ingest', '--data', a.data]):
                ingest.main()
            (out / 'ingest.txt').write_text(log.getvalue())
            rag.execute('ANALYZE pg_temp.eval_chunks')
            rag.commit()
            manifest['chunks'] = rag.execute('SELECT count(*) FROM pg_temp.eval_chunks').fetchone()[0]
            role_rows = rag.execute('''SELECT chunk_id, mention_index, doc_id, person_token, role
                                       FROM pg_temp.eval_person_roles
                                       ORDER BY doc_id, chunk_id, mention_index''').fetchall()
            with (out / 'person_roles.jsonl').open('w', encoding='utf-8') as role_file:
                for chunk_id, mention_index, doc_id, person_token, role in role_rows:
                    role_file.write(json.dumps({
                        'doc_id': doc_id, 'chunk_id': chunk_id, 'mention_index': mention_index,
                        'person_token': person_token, 'role': role,
                    }, ensure_ascii=False) + '\n')
            manifest['person_role_mentions'] = len(role_rows)
            manifest['person_role_unknown'] = sum(role == 'unknown' for *_, role in role_rows)
            manifest['person_multi_role_doc_tokens'] = rag.execute('''
                SELECT count(*) FROM (
                    SELECT doc_id, person_token FROM pg_temp.eval_person_roles
                    GROUP BY doc_id, person_token HAVING count(DISTINCT role) > 1
                ) AS mixed''').fetchone()[0]
            with TemporaryConnection(rag).cursor() as role_cur:
                ingest.save_person_roles(role_cur, [
                    {'chunk_id': cid, 'mention_index': idx, 'doc_id': did,
                     'person_token': token, 'role': role}
                    for cid, idx, did, token, role in role_rows
                ], sorted({did for _, _, did, _, _ in role_rows}))
            repeat_count = rag.execute('SELECT count(*) FROM pg_temp.eval_person_roles').fetchone()[0]
            if repeat_count != len(role_rows):
                raise AssertionError('역할 메타데이터 재적재에서 중복 또는 누락 발생')
            manifest['person_role_repeat_count'] = repeat_count
            rag.commit()
            if a.exact_vector_scan:
                rag.execute('SET enable_indexscan = off')
            with patch.object(sys, 'argv', ['run_queries', '--queries', a.queries,
                                          '-k', str(a.k), '--out', str(out / 'run.jsonl')]):
                run_queries.main()
        (out / 'pipeline.txt').write_text(log.getvalue())
        print(log.getvalue(), end='')
        pseudo = out / f'pseudo_{Path(a.data).stem}.jsonl'
        for script, args, filename in [
            ('eval/eval_holdout_retrieval.py', ['--gold', a.gold, '--run', str(out / 'run.jsonl')], 'retrieval.txt'),
            ('eval/check_token_consistency.py', ['--gold', a.persons_gold, '--pred', str(pseudo),
                                               '--out', str(out / 'token_consistency_cases.csv')], 'consistency.txt'),
        ]:
            result = subprocess.run([sys.executable, script, *args], check=True, capture_output=True, text=True)
            (out / filename).write_text(result.stdout)
            print(result.stdout, end='')
        manifest.update(status='complete', elapsed_seconds=round(time.perf_counter() - started, 3))
        manifest['original_counts_after'] = {
            'rag_chunks': rag.execute('SELECT count(*) FROM rag.chunks').fetchone()[0],
            'vault_tokens': vault.execute('SELECT count(*) FROM vault.token_map').fetchone()[0],
            'rag_person_roles': (rag.execute('SELECT count(*) FROM rag.person_roles').fetchone()[0]
                                 if existing_roles else None),
        }
    except Exception as error:
        manifest.update(status='failed', error_type=type(error).__name__)
        raise
    finally:
        (out / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
        for conn in (rag, vault):
            if conn is not None:
                conn.close()


if __name__ == '__main__':
    main()
