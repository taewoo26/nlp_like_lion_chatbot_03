"""검색 단계 인물 사전 연결 (PERSON_DICT=on): 이름 → rag.person_lookup 후보 토큰."""
import pytest

from starter import person_dict as pd
from starter import pii_pipeline as pii
from starter.search import person_candidates, token_conditions

NAME = "<PERSON_aaaaaaaaaaaa>"
STAFF = "<PERSON_bbbbbbbbbbbb>"
PHONE = "<PHONE_NUMBER_cccccccccccc>"


@pytest.fixture(autouse=True)
def synthetic_key(monkeypatch):
    monkeypatch.setattr(pii.config, "HMAC_KEY", "person-search-test-key-not-for-production")
    pii._key.cache_clear()
    yield
    pii._key.cache_clear()


class FakeCursor:
    """rag.person_lookup 만 흉내 낸다. 받은 SQL·파라미터를 기록한다."""

    def __init__(self, table):
        self.table, self.calls, self.rows = table, [], []

    def execute(self, sql, params):
        self.calls.append((sql, params))
        self.rows = [(t,) for t in self.table.get(params[0], [])]

    def fetchall(self):
        return self.rows


def test_candidates_include_query_token_and_lookup_tokens():
    cur = FakeCursor({pd.name_hmac("김민준"): [STAFF, NAME]})
    records = [{"label": "person", "token": NAME, "normalized": "김민준"},
               {"label": "phone_number", "token": PHONE, "normalized": "01000011001"}]
    assert person_candidates(cur, records) == {NAME: sorted([NAME, STAFF])}
    assert len(cur.calls) == 1                                          # 전화번호는 조회하지 않음
    assert "김민준" not in str(cur.calls)                               # 원문 이름은 DB로 보내지 않음


def test_unknown_name_keeps_only_query_token():
    assert person_candidates(FakeCursor({}), [{"label": "person", "token": NAME, "normalized": "박서준"}]) == \
        {NAME: [NAME]}


def test_without_candidates_condition_is_todays_and_filter():
    where, params = token_conditions([NAME, PHONE], {NAME: [NAME]})
    assert len(where) == 1 and "@> ARRAY[q.token]" in where[0] and params == [[NAME, PHONE]]


def test_candidates_become_or_within_name_and_across_conditions():
    where, params = token_conditions([NAME, PHONE], {NAME: [NAME, STAFF]})
    assert len(where) == 2 and all("&&" in w for w in where)
    assert params == [[NAME, STAFF], [PHONE]]
