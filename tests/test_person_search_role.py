"""인물사전 검색의 역할 필터: v3 는 역할 미정(unknown) 후보를 남기고 역할이 분명히 다른 후보만 뺀다."""
from starter.search import person_candidates
from tests.test_person_search import Cursor, records


def test_v3_keeps_unknown_role_candidates(monkeypatch):
    monkeypatch.setenv('PERSON_RULES', 'v3')
    cur = Cursor([('reg_a', 'A#0')])
    person_candidates(cur, records(), roles=['customer'])
    assert "role = 'unknown'" in cur.calls[0][0]


def test_v1_requires_exact_role(monkeypatch):
    monkeypatch.delenv('PERSON_RULES', raising=False)
    cur = Cursor([('reg_a', 'A#0')])
    person_candidates(cur, records(), roles=['customer'])
    assert "role = 'unknown'" not in cur.calls[0][0]
