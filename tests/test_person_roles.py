"""PERSON 등장별 역할과 가명 청크 매핑 회귀 시험."""
import pytest

from starter import pii_pipeline as pii
from starter.ingest import chunk
from starter.person_role_mapping import map_person_roles
from starter.person_roles import role_for_mention


@pytest.fixture(autouse=True)
def synthetic_key(monkeypatch):
    monkeypatch.setattr(pii.config, "HMAC_KEY", "role-test-key-not-for-production")
    pii._key.cache_clear()
    yield
    pii._key.cache_clear()


def records_for_names(text, names):
    positions = []
    cursor = 0
    for name in names:
        start = text.index(name, cursor)
        positions.append({"label": "person", "start": start, "end": start + len(name)})
        cursor = start + len(name)
    masked, records = pii.pseudonymize(text, positions)
    return masked, records


@pytest.mark.parametrize("text,names,roles", [
    ("직원 김민준이 고객 김민준의 민원을 처리했다.", ["김민준", "김민준"], ["employee", "customer"]),
    ("김민준 고객이 상담사 이서연에게 문의했다.", ["김민준", "이서연"], ["customer", "agent"]),
    ("정하은 고객이 카드 분실을 신고했다.", ["정하은"], ["customer"]),
    ("김민준이 고객센터 시스템을 점검했다.", ["김민준"], ["unknown"]),
    ("김민준 담당자가 정하은 고객에게 연락했다.", ["김민준", "정하은"], ["staff_contact", "customer"]),
    ("김민준이 카드 분실을 신고했다.", ["김민준"], ["unknown"]),
    ("직원 김민준과 직원 김민준이 회의했다.", ["김민준", "김민준"], ["employee", "employee"]),
    ("고객 김민준과 직원 김민준이 한 청크에 있다.", ["김민준", "김민준"], ["customer", "employee"]),
])
def test_roles_are_per_mention(text, names, roles):
    masked, records = records_for_names(text, names)
    rows = map_person_roles("D1", text, masked, records, chunk(masked))
    assert [row["role"] for row in rows] == roles
    assert [row["mention_index"] for row in rows] == list(range(len(names)))
    assert len({row["person_token"] for row in rows}) <= len(set(names))
    assert all("original" not in row and "person_id" not in row and "mention_start" not in row
               for row in rows)


def test_chunk_assignment_uses_masked_positions_not_original_offsets():
    text = "직원 김민준이 회의했다.\n고객 김민준이 문의했다."
    masked, records = records_for_names(text, ["김민준", "김민준"])
    parts = chunk(masked, max_chars=80)
    assert len(parts) == 2
    rows = map_person_roles("D2", text, masked, records, parts)
    assert [(r["chunk_id"], r["mention_index"], r["role"]) for r in rows] == [
        ("D2#0", 0, "employee"), ("D2#1", 0, "customer")]


def test_mismatched_token_sequence_fails_instead_of_guessing():
    text = "고객 김민준"
    masked, records = records_for_names(text, ["김민준"])
    with pytest.raises(ValueError, match="토큰 순서"):
        map_person_roles("D3", text, "<PERSON_" + "0" * 64 + "> " + masked, records,
                         chunk("<PERSON_" + "0" * 64 + "> " + masked))


def test_conflicting_roles_are_unknown():
    text = "직원 김민준 고객"
    start = text.index("김민준")
    assert role_for_mention(text, start, start + 3) == "unknown"


def test_speaker_label_is_not_the_named_persons_role():
    text = "상담사: 김민준 고객님, 확인하겠습니다."
    start = text.index("김민준")
    assert role_for_mention(text, start, start + 3) == "customer"


@pytest.mark.parametrize("text", ["피해 고객: 김민준", "대상 고객: 김민준", "고객명: 김민준"])
def test_explicit_customer_fields(text):
    start = text.index("김민준")
    assert role_for_mention(text, start, start + 3) == "customer"
