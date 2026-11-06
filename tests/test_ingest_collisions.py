import json
import sys
from unittest.mock import MagicMock, Mock

import pytest

from starter import ingest, pii_pipeline


def test_cross_document_collision_stops_before_embedding_or_database(tmp_path, monkeypatch):
    source = tmp_path / 'synthetic.jsonl'
    source.write_text('\n'.join(json.dumps({'id': str(i), 'text': name})
                                for i, name in enumerate(['윤선영', '김민준'])))
    monkeypatch.setattr(sys, 'argv', ['ingest', '--data', str(source)])
    monkeypatch.setattr(ingest.config, 'OUT_DIR', tmp_path / 'out')
    monkeypatch.setattr(pii_pipeline, 'detect', lambda text: [{'label': 'person', 'start': 0, 'end': len(text)}])
    monkeypatch.setattr(pii_pipeline, 'make_token', lambda *args: '<PERSON_forced>')
    embed, rag, vault = Mock(), Mock(), Mock()
    monkeypatch.setattr(ingest, 'embed', embed)
    monkeypatch.setattr(ingest.db, 'rag_conn', rag)
    monkeypatch.setattr(ingest.db, 'vault_conn', vault)
    with pytest.raises(ValueError, match='문서 간 토큰 충돌'):
        ingest.main()
    for method in (embed, rag, vault):
        method.assert_not_called()


def test_identical_value_variants_are_kept_but_different_labels_are_rejected():
    mapping = {}
    record = {'token': '<PERSON_test>', 'label': 'person', 'normalized': '윤선영', 'original': '윤 선영'}
    ingest.collect_mapping(mapping, record)
    ingest.collect_mapping(mapping, record | {'original': '윤선영'})
    assert mapping[record['token']]['originals'] == {'윤 선영', '윤선영'}
    with pytest.raises(ValueError, match='충돌'):
        ingest.collect_mapping(mapping, record | {'label': 'email'})


def test_existing_vault_identity_mismatch_raises():
    cur = Mock()
    cur.fetchone.return_value = None  # upsert의 identity 조건이 거부한 행
    with pytest.raises(ValueError, match='기존 Vault'):
        ingest.save_mappings(cur, {'<PERSON_test>': {'label': 'person', 'normalized': '윤선영', 'originals': {'윤선영'}}})


def test_vault_failure_rolls_back_pending_rag_transaction(tmp_path, monkeypatch):
    source = tmp_path / 'synthetic.jsonl'
    source.write_text(json.dumps({'id': 'R1', 'text': '윤선영'}))
    monkeypatch.setattr(sys, 'argv', ['ingest', '--data', str(source)])
    monkeypatch.setattr(ingest.config, 'OUT_DIR', tmp_path / 'out')
    monkeypatch.setattr(pii_pipeline, 'detect', lambda text: [{'label': 'person', 'start': 0, 'end': len(text)}])
    monkeypatch.setattr(pii_pipeline, 'make_token', lambda *args: '<PERSON_test>')
    monkeypatch.setattr(ingest, 'embed', lambda texts: [[0.]])
    rag, vault = MagicMock(), MagicMock()
    monkeypatch.setattr(ingest.db, 'rag_conn', lambda: rag)
    monkeypatch.setattr(ingest.db, 'vault_conn', lambda: vault)
    vault.__enter__.return_value.cursor.return_value.__enter__.return_value.fetchone.return_value = None
    with pytest.raises(ValueError, match='기존 Vault'):
        ingest.main()
    assert rag.__exit__.call_args.args[0] is ValueError
