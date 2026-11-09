"""문서 단위 배치 적재·이어하기 (DB 없이 가짜 연결로 확인). 실제 DB 격리 테스트는 PR 설명 참고."""
import json
import sys
from unittest.mock import MagicMock

import numpy as np
import pytest

from starter import ingest, pii_pipeline


def _setup(tmp_path, monkeypatch, n_docs, argv=()):
    source = tmp_path / 'synthetic.jsonl'
    source.write_text('\n'.join(json.dumps({'id': f'D{i}', 'text': f'문서 {i}'}) for i in range(n_docs)))
    monkeypatch.setattr(sys, 'argv', ['ingest', '--data', str(source), *argv])
    monkeypatch.setattr(ingest.config, 'OUT_DIR', tmp_path / 'out')
    monkeypatch.setenv('REGION_META', 'off')
    monkeypatch.setenv('ROLE_META', 'off')
    monkeypatch.setattr(pii_pipeline, 'detect', lambda text: [])
    calls = {'embed': [], 'commit_rag': 0, 'commit_vault': 0}

    def embed(texts, kind='passage'):
        calls['embed'].append(len(texts))
        return [np.ones(2)] * len(texts)
    monkeypatch.setattr(ingest, 'embed', embed)
    rag, vault = MagicMock(), MagicMock()
    rag.__enter__.return_value.commit.side_effect = lambda: calls.__setitem__('commit_rag', calls['commit_rag'] + 1)
    vault.__enter__.return_value.commit.side_effect = lambda: calls.__setitem__('commit_vault', calls['commit_vault'] + 1)
    monkeypatch.setattr(ingest.db, 'rag_conn', lambda: rag)
    monkeypatch.setattr(ingest.db, 'vault_conn', lambda: vault)
    return source, calls


def test_batches_by_document_count_and_commits_each_batch(tmp_path, monkeypatch):
    _, calls = _setup(tmp_path, monkeypatch, 7, ['--batch-size', '3'])
    ingest.main()
    assert calls['embed'] == [3, 3, 1]                       # 문서 3·3·1건씩 임베딩
    assert calls['commit_vault'] == calls['commit_rag'] == 3  # 배치마다 vault·rag 커밋
    out = tmp_path / 'out'
    assert len((out / 'pseudo_synthetic.jsonl').read_text().splitlines()) == 7
    assert not (out / 'ingest_checkpoint_synthetic.json').exists()
    assert not (out / 'pseudo_synthetic.jsonl.part').exists()


def test_batch_size_from_env(tmp_path, monkeypatch):
    monkeypatch.setenv('INGEST_BATCH', '4')
    _, calls = _setup(tmp_path, monkeypatch, 7)
    ingest.main()
    assert calls['embed'] == [4, 3]


def test_reset_is_one_transaction(tmp_path, monkeypatch):
    _, calls = _setup(tmp_path, monkeypatch, 7, ['--reset', '--batch-size', '3'])
    ingest.main()
    assert calls['embed'] == [7] and calls['commit_rag'] == 1


def test_failure_keeps_checkpoint_of_committed_batches(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, 7, ['--batch-size', '3'])
    real = ingest.prepare

    def prepare(batch, *args):
        if batch[0]['id'] == 'D3':
            raise RuntimeError('injected')
        return real(batch, *args)
    monkeypatch.setattr(ingest, 'prepare', prepare)
    with pytest.raises(RuntimeError):
        ingest.main()
    out = tmp_path / 'out'
    ckpt = json.loads((out / 'ingest_checkpoint_synthetic.json').read_text())
    assert sorted(ckpt['done']) == ['D0', 'D1', 'D2']
    assert len((out / 'pseudo_synthetic.jsonl.part').read_text().splitlines()) == 3
    assert not (out / 'pseudo_synthetic.jsonl').exists()


def test_reset_and_resume_cannot_be_combined(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, 2, ['--reset', '--resume'])
    with pytest.raises(SystemExit, match='함께 쓸 수 없습니다'):
        ingest.main()


def test_resume_refuses_changed_settings(tmp_path, monkeypatch):
    source, _ = _setup(tmp_path, monkeypatch, 2, ['--resume'])
    out = tmp_path / 'out'
    out.mkdir()
    (out / 'ingest_checkpoint_synthetic.json').write_text(json.dumps({'settings': {'max_chars': 1}, 'done': {}}))
    with pytest.raises(SystemExit, match='조건이 다릅니다'):
        ingest.main()


def test_verified_done_drops_partial_chunks_and_missing_vault_tokens():
    rag_cur, vault_cur = MagicMock(), MagicMock()
    rag_cur.fetchall.return_value = [
        ('A', 0, '<PERSON_aaaaaaaaaaaa> 님'), ('A', 1, '본문'),   # A: 청크 2개 모두 있음
        ('B', 0, '본문'),                                          # B: 청크 2개 중 1개만 있음
        ('C', 0, '<PERSON_cccccccccccc> 님'),                      # C: vault 에 토큰 없음
    ]
    vault_cur.fetchall.return_value = [('<PERSON_aaaaaaaaaaaa>',)]
    assert ingest.verified_done(rag_cur, vault_cur, {'A': 2, 'B': 2, 'C': 1}) == {'A': 2}


def test_model_check_stops_on_different_vectors(monkeypatch):
    cur = MagicMock()
    cur.fetchall.return_value = [('본문', np.array([1.0, 0.0]))]
    monkeypatch.setattr(ingest, 'embed', lambda texts: [np.array([0.0, 1.0])])
    with pytest.raises(SystemExit, match='--reset'):
        ingest.check_embedding_model(cur)
    monkeypatch.setattr(ingest, 'embed', lambda texts: [np.array([1.0, 0.0])])
    ingest.check_embedding_model(cur)   # 같은 벡터면 통과
