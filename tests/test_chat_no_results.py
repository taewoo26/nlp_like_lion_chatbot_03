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
