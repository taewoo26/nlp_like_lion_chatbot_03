"""chat.answer()의 state 판정과, 예외에 가명 질의가 붙는지 (audit.query_log 기록용)."""
from unittest.mock import Mock

import pytest

from starter import chat, search

NO_META = {'regions': [], 'ask_region': None, 'roles': [], 'excluded': 0}
TOK = '<PERSON_bd52a13d0d35>'
DOCS = [{'doc_id': 'H0001', 'masked_text': f'{TOK} 한도 상향 승인'}]


def _search_returns(monkeypatch, docs, meta=NO_META):
    monkeypatch.setattr(chat, 'search', lambda *a, **kw: (f'{TOK} 한도 결과', [TOK], docs, meta))


def _llm_says(monkeypatch, text):
    resp = Mock(status_code=200)
    resp.json.return_value = {'choices': [{'message': {'content': text}}]}
    monkeypatch.setattr(chat.config, 'LLM_API_KEY', 'synthetic-test-key')
    monkeypatch.setattr(chat.requests, 'post', Mock(return_value=resp))


def test_state_clarify(monkeypatch):
    _search_returns(monkeypatch, [], dict(NO_META, ask_region='중구'))
    assert chat.answer('입력')['state'] == 'clarify'


def test_state_no_result(monkeypatch):
    _search_returns(monkeypatch, [])
    monkeypatch.setattr(chat.config, 'LLM_API_KEY', 'synthetic-test-key')
    assert chat.answer('입력')['state'] == 'no_result'


def test_state_search_only(monkeypatch):
    _search_returns(monkeypatch, DOCS)
    monkeypatch.setattr(chat.config, 'LLM_API_KEY', '')
    res = chat.answer('입력')
    assert res['state'] == 'search_only' and res['answer'] is None


@pytest.mark.parametrize('text, state', [
    ('네, 1건 있습니다. [H0001]', 'answered'),
    ('제공된 문서에서는 확인되지 않습니다.', 'withheld'),
    ('역할이 달라 답을 보류합니다.', 'withheld'),
])
def test_state_answered_or_withheld(monkeypatch, text, state):
    _search_returns(monkeypatch, DOCS)
    _llm_says(monkeypatch, text)
    assert chat.answer('입력')['state'] == state


def test_llm_error_carries_masked_query(monkeypatch):
    _search_returns(monkeypatch, DOCS)
    monkeypatch.setattr(chat.config, 'LLM_API_KEY', 'synthetic-test-key')
    monkeypatch.setattr(chat.requests, 'post', Mock(side_effect=ConnectionError('down')))
    with pytest.raises(ConnectionError) as ei:
        chat.answer('입력')
    assert ei.value.masked_query == f'{TOK} 한도 결과'
    assert ei.value.query_tokens == [TOK]


def test_search_error_after_masking_carries_masked_query(monkeypatch):
    monkeypatch.setattr(search, 'mask_query', lambda q, with_records=False: ('<PERSON_aaaaaaaaaaaa> 대출', ['<PERSON_aaaaaaaaaaaa>']))
    monkeypatch.setattr(search, 'embed', Mock(side_effect=RuntimeError('embed down')))
    with pytest.raises(RuntimeError) as ei:
        search.search('홍길동 대출', 3, with_meta=True)
    assert ei.value.masked_query == '<PERSON_aaaaaaaaaaaa> 대출'
    assert '홍길동' not in ei.value.masked_query


def test_masking_error_has_no_masked_query(monkeypatch):
    monkeypatch.setattr(search, 'mask_query', Mock(side_effect=RuntimeError('detector down')))
    with pytest.raises(RuntimeError) as ei:
        search.search('홍길동 대출', 3, with_meta=True)
    assert getattr(ei.value, 'masked_query', None) is None


def test_tag_masked_keeps_first_value():
    exc = RuntimeError('x')
    search.tag_masked(exc, 'first', ['<A_1>'])
    search.tag_masked(exc, 'second', [])
    assert exc.masked_query == 'first' and exc.query_tokens == ['<A_1>']
