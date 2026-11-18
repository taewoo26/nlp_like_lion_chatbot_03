"""새 look_up 검색: 미확정 후보도 이름·역할 검색에 포함하고 동일인으로 합치지 않는다."""
from starter.search import person_candidates, token_conditions

NAME = '<PERSON_aaaaaaaaaaaa>'
PHONE = '<PHONE_NUMBER_cccccccccccc>'


class Cursor:
    def __init__(self, rows):
        self.rows, self.calls = rows, []

    def execute(self, sql, params):
        self.calls.append((sql, params))

    def fetchall(self):
        return self.rows


def records():
    return [{'label': 'person', 'token': NAME, 'normalized': '김민준'},
            {'label': 'phone_number', 'token': PHONE, 'normalized': '01000011001'}]


def test_name_role_lookup_returns_matching_chunks_without_plaintext():
    cur = Cursor([('reg_a', 'A#0'), ('reg_a', 'B#0')])
    assert person_candidates(cur, records()) == {NAME: ['A#0', 'B#0']}
    assert len(cur.calls) == 1 and '김민준' not in str(cur.calls)
    assert 'rag.look_up' in cur.calls[0][0] and 'vault.' not in cur.calls[0][0]


def test_unknown_name_has_no_fallback():
    assert person_candidates(Cursor([]), records()) == {NAME: []}
    where, params = token_conditions([NAME], {NAME: []})
    assert params == [[]] and 'chunk_id' in where[0]


def test_distinct_unresolved_candidates_request_clarification():
    meta = {'person_candidates': {}, 'ask_person': False}
    assert person_candidates(Cursor([('tmp_a', 'A#0'), ('tmp_b', 'B#0')]), records(), meta=meta) == {NAME: ['A#0', 'B#0']}
    assert meta['ask_person'] and meta['person_candidates'][NAME] == 2


def test_name_filters_actual_chunks_and_phone_filters_document():
    where, params = token_conditions([NAME, PHONE], {NAME: ['A#0']})
    assert 'chunks.chunk_id' in where[0] and 'matched.doc_id = chunks.doc_id' in where[1]
    assert params == [['A#0'], [PHONE]]


def test_lookup_does_not_expand_to_other_name_tokens():
    cur = Cursor([('reg_a', 'A#0')])
    person_candidates(cur, records(), roles=['customer'])
    sql, args = cur.calls[0]
    assert 'JOIN' not in sql and 'token = %s' in sql
    assert args == (NAME, ['customer'], ['customer'])


def fake_search(monkeypatch, results):
    from starter import search as module
    from unittest.mock import MagicMock
    monkeypatch.setenv('PERSON_DICT', 'on')
    monkeypatch.setenv('SEARCH_MODE', 'and')
    monkeypatch.setenv('RERANK_TOP', '0')
    monkeypatch.setattr(module, 'mask_query', lambda *a, **kw: (NAME, [NAME], records()[:1]))
    monkeypatch.setattr(module, 'embed', lambda *a, **kw: [[1., 0., 0.]])
    from starter.roles import default_rules
    monkeypatch.setattr(module, '_rules', lambda cur: default_rules())
    conn = MagicMock()
    cur = conn.cursor.return_value.__enter__.return_value
    cur.fetchall.side_effect = results
    return module, conn, cur


def test_search_keeps_documents_for_multiple_unresolved_candidates(monkeypatch):
    module, conn, cur = fake_search(monkeypatch, [
        [('tmp_a', 'A#0'), ('tmp_b', 'B#0')],
        [('A', 'A#0', NAME, [NAME], 0.1), ('B', 'B#0', NAME, [NAME], 0.2)],
    ])
    _, _, docs, meta = module.search('가명 질문', conn=conn, with_meta=True)
    assert [d['doc_id'] for d in docs] == ['A', 'B']
    assert meta['ask_person'] and meta['person_candidates'][NAME] == 2
    assert cur.execute.call_count == 2  # 후보가 여러 명이어도 벡터 검색 실행


def test_search_does_not_fallback_for_missing_or_pending_candidates(monkeypatch):
    module, conn, cur = fake_search(monkeypatch, [[]])
    assert module.search('가명 질문', conn=conn)[2] == []
    assert cur.execute.call_count == 1


def test_explicit_registry_search_uses_registry_without_name_or_role_filter(monkeypatch):
    module, conn, cur = fake_search(monkeypatch, [
        [('A#0',), ('C#0',)],
        [('A', 'A#0', NAME, [NAME], 0.1), ('C', 'C#0', '<PERSON_other>', [], 0.2)],
    ])
    _, _, docs, meta = module.search('가명 질문', conn=conn, with_meta=True, reg_id='reg_selected')
    sql, args = cur.execute.call_args_list[0].args
    assert 'WHERE reg_id = %s' in sql and args == ('reg_selected',)
    assert 'token =' not in sql and 'role =' not in sql
    assert [d['doc_id'] for d in docs] == ['A', 'C']
    assert not meta['ask_person'] and meta['person_scope'] == 'registry'
