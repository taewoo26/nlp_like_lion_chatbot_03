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


def test_context_marks_lines_with_query_tokens():
    tok = '<PHONE_NUMBER_d1fee87c1ce7>'
    block = chat._doc_block({'doc_id': 'H0125', 'masked_text': f'자동이체 신청서\n연락처: {tok}\n기타'}, [tok])
    assert '(시스템 확인)' in block and f'- 연락처: {tok}' in block
    assert '(시스템 확인)' not in chat._doc_block({'doc_id': 'H1', 'masked_text': '무관'}, [tok])


def test_answer_tokens_are_restored_to_original_case():
    tok = '<PERSON_bd52a13d0d35>'
    out = chat._restore_tokens('<PERSON_BD52A13D0D35> 문서 없음', {tok})
    assert out == f'{tok} 문서 없음'
    assert chat._restore_tokens('<PERSON_ffffffffffff>', {tok}) == '<PERSON_ffffffffffff>'


def test_llm_options_only_when_set(monkeypatch):
    monkeypatch.delenv('LLM_REASONING', raising=False)
    monkeypatch.delenv('LLM_MAX_TOKENS', raising=False)
    assert chat._llm_options() == {}
    monkeypatch.setenv('LLM_REASONING', 'none')
    monkeypatch.setenv('LLM_MAX_TOKENS', '2048')
    assert chat._llm_options() == {'reasoning_effort': 'none', 'max_completion_tokens': 2048}


def test_llm_retries_on_429(monkeypatch):
    busy, ok = Mock(status_code=429), Mock(status_code=200)
    post = Mock(side_effect=[busy, ok])
    monkeypatch.setattr(chat.requests, 'post', post)
    monkeypatch.setattr(chat.time, 'sleep', lambda s: None)
    assert chat._post_llm({}) is ok and post.call_count == 2
