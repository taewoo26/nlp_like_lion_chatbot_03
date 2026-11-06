"""search() 의 역할·지역 조건 처리 (가짜 DB 연결). 조건 없는 질의는 기존 SQL 그대로인지도 확인."""
from unittest.mock import Mock

import pytest

from starter import chat, pii_pipeline as pii, search


@pytest.fixture(autouse=True)
def setup(monkeypatch):
    monkeypatch.setattr(pii.config, "HMAC_KEY", "search-meta-test-key")
    pii._key.cache_clear()
    search._masked_records.cache_clear()
    monkeypatch.setenv("ROLE_MASTER", "file")
    monkeypatch.delenv("SEARCH_METADATA", raising=False)
    monkeypatch.delenv("SEARCH_MODE", raising=False)
    monkeypatch.setenv("RERANK_TOP", "0")
    monkeypatch.setattr(search, "embed", lambda texts, **kw: [[1.0, 0.0]])
    names = ("김민준", "홍길동")
    def detect(text):
        out = []
        for n in names:
            i = text.find(n)
            if i >= 0:
                out.append({"label": "person", "start": i, "end": i + len(n)})
        return out
    monkeypatch.setattr(pii, "detect", detect)
    yield
    pii._key.cache_clear()
    search._masked_records.cache_clear()


def fake_conn(hits, persons_rows=()):
    cur = Mock()
    cur.__enter__ = Mock(return_value=cur)
    cur.__exit__ = Mock(return_value=False)
    cur.fetchall.side_effect = [hits, list(persons_rows)]
    conn = Mock()
    conn.cursor.return_value = cur
    return conn, cur


def hit(doc, dist):
    return (doc, f"{doc}#0", f"본문 {doc}", [], dist)


T = lambda n: pii.make_token("person", n)


def test_customer_query_orders_match_then_unknown_and_excludes_mismatch():
    tok = T("김민준")
    hits = [hit("EMP", 0.10), hit("UNK", 0.20), hit("CUS", 0.30), hit("NOMETA", 0.40)]
    persons = [("EMP", [{"token": tok, "role": "employee"}]),
               ("UNK", [{"token": tok, "role": "unknown"}]),
               ("CUS", [{"token": tok, "role": "customer"}, {"token": tok, "role": "unknown"}])]
    conn, cur = fake_conn(hits, persons)
    _, q_tokens, docs = search.search("김민준 고객의 상담 기록", k=5, conn=conn)
    assert q_tokens == [tok]
    assert [d["doc_id"] for d in docs] == ["CUS", "UNK", "NOMETA"]             # 1 → 2(원래 순서)
    assert [d["role_tier"] for d in docs] == [1, 2, 2]
    assert any("1건은 제외" in n for n in docs.info["notices"])
    sql, args = cur.execute.call_args_list[0].args
    assert "matched.tokens @> ARRAY[q.token]" in sql and "unnest(rg.regions)" not in sql   # 토큰 필터 유지


def test_staff_query_keeps_agent_documents():                                     # ROLE-46 (초안)
    tok = T("김민준")
    conn, _ = fake_conn([hit("AGT", 0.1), hit("CUS", 0.2)],
                        [("AGT", [{"token": tok, "role": "agent"}]), ("CUS", [{"token": tok, "role": "customer"}])])
    _, _, docs = search.search("직원 김민준의 업무 보고서", k=5, conn=conn)
    assert [d["doc_id"] for d in docs] == ["AGT"]


def test_exclusion_happens_before_cutting_k():
    tok = T("김민준")
    hits = [hit("E1", 0.1), hit("E2", 0.2), hit("C1", 0.3)]
    persons = [("E1", [{"token": tok, "role": "employee"}]), ("E2", [{"token": tok, "role": "employee"}]),
               ("C1", [{"token": tok, "role": "customer"}])]
    conn, _ = fake_conn(hits, persons)
    _, _, docs = search.search("김민준 고객", k=1, conn=conn)
    assert [d["doc_id"] for d in docs] == ["C1"]


def test_region_query_adds_region_filter_without_tokens():
    conn, cur = fake_conn([hit("D1", 0.1)])
    _, q_tokens, docs = search.search("대구 수성구 관련 문서", k=5, conn=conn)
    sql, args = cur.execute.call_args_list[0].args
    assert q_tokens == [] and "MATERIALIZED" in sql and "unnest(rg.regions)" in sql
    assert args[1] == ["대구광역시 수성구"] and args[2] == ["대구광역시 수성구 %"]
    assert [d["doc_id"] for d in docs] == ["D1"]
    cur.fetchall.side_effect = None


def test_region_and_token_filters_together():
    conn, cur = fake_conn([])
    _, _, docs = search.search("대구 수성구 사는 홍길동", k=5, conn=conn)
    sql, args = cur.execute.call_args_list[0].args
    assert "matched.tokens @> ARRAY[q.token]" in sql and "unnest(rg.regions)" in sql
    assert args[1] == [T("홍길동")] and args[2] == ["대구광역시 수성구"]
    assert docs == [] and "역할·지역 조건에 맞는 문서가 없습니다." in docs.info["notices"]


def test_ambiguous_region_asks_back_without_search():                            # REG-33
    conn, cur = fake_conn([])
    _, _, docs = search.search("중구 고객", k=5, conn=conn)
    assert docs == [] and "시도를 함께" in docs.info["clarify"]
    cur.execute.assert_not_called()


def test_plain_query_uses_original_sql_once():
    conn, cur = fake_conn([hit("D1", 0.1)])
    _, _, docs = search.search("결제 알림 미수신 처리 기록", k=5, conn=conn)
    cur.execute.assert_called_once()
    sql, _ = cur.execute.call_args.args
    assert "WHERE" not in sql and docs.info == {}


def test_metadata_can_be_turned_off(monkeypatch):
    monkeypatch.setenv("SEARCH_METADATA", "off")
    conn, cur = fake_conn([hit("D1", 0.1)])
    search.search("대구 수성구 관련 문서", k=5, conn=conn)
    cur.execute.assert_called_once()
    assert "regions" not in cur.execute.call_args.args[0]


def test_chat_shows_clarify_and_notices(monkeypatch):
    monkeypatch.setattr(chat, "search", lambda *a: ("q", [], search.Docs([], {"clarify": "시도를 함께 적어 주세요.", "notices": []})))
    assert chat.answer("중구 고객")["answer"] == "시도를 함께 적어 주세요."
    monkeypatch.setattr(chat, "search", lambda *a: ("q", [], search.Docs([], {"notices": ["역할·지역 조건에 맞는 문서가 없습니다."]})))
    assert chat.answer("x")["answer"] == "일치하는 문서를 찾지 못했습니다. 역할·지역 조건에 맞는 문서가 없습니다."
