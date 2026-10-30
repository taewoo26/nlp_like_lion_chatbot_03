from unittest.mock import Mock

import pytest

from starter import search


@pytest.mark.parametrize('tokens', [[], ['<PERSON_a>'], ['<PERSON_a>', '<PHONE_NUMBER_b>']])
def test_search_binds_all_query_tokens_and_does_not_fall_back(monkeypatch, tokens):
    monkeypatch.setattr(search, 'mask_query', lambda query: ('가명 질의', tokens))
    monkeypatch.setattr(search, 'embed', lambda texts: [[1., 0., 0.]])
    cursor = Mock()
    cursor.__enter__ = Mock(return_value=cursor)
    cursor.__exit__ = Mock(return_value=False)
    cursor.fetchall.return_value = []
    conn = Mock()
    conn.cursor.return_value = cursor
    masked, returned_tokens, docs = search.search('입력', conn=conn)
    assert (masked, returned_tokens, docs) == ('가명 질의', tokens, [])
    cursor.execute.assert_called_once()  # 후보가 없을 때 전체 검색을 재시도하지 않는다.
    sql, args = cursor.execute.call_args.args
    if tokens:
        assert args[1] == tokens
        assert 'matched.doc_id = chunks.doc_id' in sql  # 토큰이 다른 청크에 있어도 같은 문서 기준
        assert 'unnest(%s::text[])' in sql
    else:
        assert len(args) == 2
        assert 'WHERE' not in sql
