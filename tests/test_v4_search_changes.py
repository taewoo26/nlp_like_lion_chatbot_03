"""v4 변경: 토큰 12자리, 지역명 이름 오탐 제거, 임베딩용 토큰 제거, boost 검색 옵션."""
import re
from unittest.mock import Mock

import pytest

from starter import pii_pipeline as pii
from starter import search


def _person(text, word):
    i = text.index(word)
    return {"label": "person", "start": i, "end": i + len(word)}


def test_token_is_12_hex():
    assert re.fullmatch(r"<PHONE_NUMBER_[0-9a-f]{12}>", pii.make_token("phone_number", "01012345678"))


def test_region_names_after_sido_are_not_persons():
    text = "충남 천안시 서북구 쪽 사는 고객들 문서 다 보여줘"
    spans = [_person(text, "천안시"), _person(text, "서북구")]
    assert pii.drop_region_names(text, spans) == []


@pytest.mark.parametrize("text, word", [
    ("담당 김민구 과장에게 전달", "김민구"),
    ("서울 박성구 고객 문의", "박성구"),
    ("천안시 담당자 문의", "천안시"),          # 지역 나열 문맥이 아니면 유지 (앞뒤가 지역명이 아님)
])
def test_real_names_and_lone_words_are_kept(text, word):
    span = _person(text, word)
    assert pii.drop_region_names(text, [span]) == [span]


def test_other_labels_are_untouched():
    text = "충남 천안시 서북구 쪽"
    span = {"label": "address", "start": 0, "end": len(text)}
    assert pii.drop_region_names(text, [span]) == [span]


def test_embedding_text_keeps_label_only():
    masked = "대구광역시 수성구 <ADDRESS_0123456789ab> <PERSON_abcdefabcdef> 고객"
    assert pii.embedding_text(masked) == "대구광역시 수성구 <ADDRESS> <PERSON> 고객"


def _conn(fetches):
    cursor = Mock()
    cursor.__enter__ = Mock(return_value=cursor)
    cursor.__exit__ = Mock(return_value=False)
    cursor.fetchall.side_effect = fetches
    conn = Mock()
    conn.cursor.return_value = cursor
    return conn, cursor


def test_search_embeds_token_free_query(monkeypatch):
    seen = []
    monkeypatch.setattr(search, "mask_query", lambda q: ("<PERSON_abcdefabcdef> 고객", []))
    monkeypatch.setattr(search, "embed", lambda texts: seen.extend(texts) or [[1.0]])
    monkeypatch.setenv("SEARCH_MODE", "and")
    conn, _ = _conn([[]])
    search.search("입력", conn=conn)
    assert seen == ["<PERSON> 고객"]


def test_boost_ranks_token_docs_first_without_dropping_others(monkeypatch):
    tok = "<PERSON_abcdefabcdef>"
    monkeypatch.setattr(search, "mask_query", lambda q: (f"{tok} 문의", [tok]))
    monkeypatch.setattr(search, "embed", lambda texts: [[1.0]])
    monkeypatch.setenv("SEARCH_MODE", "boost")
    hits = [("H2", "H2#0", "내용", [], 0.05),            # 의미는 가깝지만 토큰 없음
            ("H1", "H1#0", f"{tok} 내용", [tok], 0.40)]  # 토큰 일치
    conn, cursor = _conn([hits, [("H1", [tok])]])
    _, _, docs = search.search("입력", k=5, conn=conn)
    assert [d["doc_id"] for d in docs] == ["H1", "H2"]
    assert cursor.execute.call_count == 2


def test_unknown_search_mode_is_rejected(monkeypatch):
    monkeypatch.setattr(search, "mask_query", lambda q: ("질의", []))
    monkeypatch.setattr(search, "embed", lambda texts: [[1.0]])
    monkeypatch.setenv("SEARCH_MODE", "loose")
    with pytest.raises(ValueError):
        search.search("입력", conn=_conn([[]])[0])


def test_embedding_text_keep_mode(monkeypatch):
    monkeypatch.setenv("EMBED_TOKENS", "keep")
    masked = "<PERSON_abcdefabcdef> 고객"
    assert pii.embedding_text(masked) == masked


def test_token_filter_is_applied_before_vector_ordering(monkeypatch):
    """필터를 MATERIALIZED 후보 안에서 정렬해야 HNSW 근사 인덱스가 정답을 먼저 잘라내지 않는다."""
    monkeypatch.setattr(search, "mask_query", lambda q: ("질의", ["<PERSON_abcdefabcdef>"]))
    monkeypatch.setattr(search, "embed", lambda texts: [[1.0]])
    monkeypatch.setenv("SEARCH_MODE", "and")
    conn, cursor = _conn([[]])
    search.search("입력", k=3, conn=conn)
    sql = cursor.execute.call_args.args[0]
    assert "AS MATERIALIZED" in sql and sql.index("NOT EXISTS") < sql.index("FROM cand ORDER BY")