"""상위 5개 단어 겹침 재정렬: 순서만 바꾸고 구성은 유지."""
from starter import search


def _docs():
    return [
        {"doc_id": "A", "masked_text": "<PERSON_aaaaaaaaaaaa> 고객 VOC 접수, 처리 진행 중", "score": 0.80},
        {"doc_id": "B", "masked_text": "<PERSON_aaaaaaaaaaaa> 고객 VOC 해결 완료", "score": 0.79},
        {"doc_id": "C", "masked_text": "출장 보고", "score": 0.50},
        {"doc_id": "D", "masked_text": "기타", "score": 0.40},
        {"doc_id": "E", "masked_text": "기타", "score": 0.30},
        {"doc_id": "F", "masked_text": "VOC 해결 완료", "score": 0.20},   # 6위: 단어가 맞아도 위로 못 올라감
    ]


def test_keyword_overlap_reorders_only_within_top5(monkeypatch):
    monkeypatch.delenv("RERANK_TOP", raising=False)
    out = [d["doc_id"] for d in search.rerank_top("<PERSON_aaaaaaaaaaaa> VOC 해결 완료 건", _docs())]
    assert out[0] == "B"
    assert set(out[:5]) == {"A", "B", "C", "D", "E"} and out[5] == "F"


def test_rerank_can_be_turned_off(monkeypatch):
    monkeypatch.setenv("RERANK_TOP", "0")
    out = [d["doc_id"] for d in search.rerank_top("VOC 해결 완료", _docs())]
    assert out == ["A", "B", "C", "D", "E", "F"]


def test_query_without_content_words_keeps_order(monkeypatch):
    monkeypatch.delenv("RERANK_TOP", raising=False)
    out = [d["doc_id"] for d in search.rerank_top("<PERSON_aaaaaaaaaaaa>", _docs())]
    assert out == ["A", "B", "C", "D", "E", "F"]
