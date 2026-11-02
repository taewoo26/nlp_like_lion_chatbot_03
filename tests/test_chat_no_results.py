from unittest.mock import Mock

from starter import chat


def test_no_matching_documents_does_not_call_llm(monkeypatch):
    monkeypatch.setattr(chat, 'search', lambda *args: ('가명 질문', ['<PERSON_a>'], []))
    monkeypatch.setattr(chat.config, 'LLM_API_KEY', 'synthetic-test-key')
    post = Mock()
    monkeypatch.setattr(chat.requests, 'post', post)
    result = chat.answer('입력')
    assert result['docs'] == []
    assert result['answer'] == '일치하는 문서를 찾지 못했습니다.'
    post.assert_not_called()


def test_prompt_lists_query_tokens_and_documents(monkeypatch):
    tok = '<PHONE_NUMBER_d1fee87c1ce7>'
    docs = [{'doc_id': 'H0125', 'masked_text': f'자동이체 신청서\n연락처: {tok}'}]
    monkeypatch.setattr(chat, 'search', lambda *args: (f'{tok} 이 번호로 들어온 건 있었나요?', [tok], docs))
    monkeypatch.setattr(chat.config, 'LLM_API_KEY', 'synthetic-test-key')
    resp = Mock()
    resp.json.return_value = {'choices': [{'message': {'content': '네, 1건 있습니다.'}}]}
    post = Mock(return_value=resp)
    monkeypatch.setattr(chat.requests, 'post', post)
    result = chat.answer('입력')
    messages = post.call_args.kwargs['json']['messages']
    assert '같으면 같은 사람·같은 값' in messages[0]['content']
    assert '[H0125]' in messages[1]['content'] and f'질문에 포함된 토큰: {tok}' in messages[1]['content']
    assert result['answer'] == '네, 1건 있습니다.'