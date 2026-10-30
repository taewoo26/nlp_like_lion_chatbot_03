"""기본형 가명처리의 값 동일성, 원문 위치, 실패 동작을 확인한다."""
import re
import subprocess
import sys

import pytest

from starter import pii_pipeline as pii


@pytest.fixture(autouse=True)
def test_key(monkeypatch):
    monkeypatch.setattr(pii.config, "HMAC_KEY", "unit-test-key-not-for-production")
    pii._key.cache_clear()
    yield
    pii._key.cache_clear()


@pytest.mark.parametrize("label,left,right,expected", [
    ("person", "윤 선영", "윤선영", "윤선영"),
    ("person", "John Smith", "JOHN-SMITH", "JOHNSMITH"),
    ("email", "User.Name@EXAMPLE.COM", "User.Name@example.com", "User.Name@example.com"),
    ("phone_number", "+82 (0)10-1234-5678", "010 1234 5678", "01012345678"),
    ("phone_number", "+1 (213) 555-0123", "+12135550123", "+12135550123"),
    ("phone_number", "+39 (02) 1234-5678", "+390212345678", "+390212345678"),
    ("phone_number", "０１０－０１２３－４５６７", "01001234567", "01001234567"),
    ("phone_number", "O1O-0l23-4567", "01001234567", "01001234567"),
    ("account_number", "001-234-567890", "001234567890", "001234567890"),
    ("account_number", "gb00 LOYD 0000 0000", "GB00LOYD00000000", "GB00LOYD00000000"),
    ("resident_registration_number", "900101-1234567", "900101 1234567", "9001011234567"),
    ("card_number", "3712-345678-90123", "371234567890123", "371234567890123"),
    ("card_number", "1234 5678 9012 3456", "1234567890123456", "1234567890123456"),
])
def test_same_value_across_formats(label, left, right, expected):
    normalized = [pii.normalize_value(label, v) for v in (left, right)]
    assert normalized == [expected, expected]
    assert pii.make_token(label, normalized[0]) == pii.make_token(label, normalized[1])


@pytest.mark.parametrize("label,left,right", [
    ("person", "윤선영", "윤서영"),
    ("person", "선영", "윤선영"),
    ("email", "User@example.com", "user@example.com"),
    ("email", "a.b+tag@example.com", "ab@example.com"),
    ("phone_number", "+12135550123", "2135550123"),
])
def test_different_values_remain_different(label, left, right):
    assert pii.make_token(label, pii.normalize_value(label, left)) != pii.make_token(
        label, pii.normalize_value(label, right))


@pytest.mark.parametrize("label,value", [
    ("phone_number", "010-1234-5678 / 010-9876-5432"),
    ("phone_number", "+82"),
    ("resident_registration_number", "900101-1******"),
    ("account_number", "123-ABC-456"),
    ("email", "User @ example.com"),
])
def test_unsupported_values_are_not_silently_merged(label, value):
    with pytest.raises(ValueError):
        pii.normalize_value(label, value)


def test_original_offsets_surrounding_text_and_repeated_tokens():
    text = "고객 윤 선영님, 윤선영 고객에게 연락했다."
    second = text.index("윤선영")
    masked, records = pii.pseudonymize(text, [
        {"label": "person", "start": second, "end": second + 3},
        {"label": "person", "start": 3, "end": 7},
    ])
    token = records[0]["token"]
    assert re.fullmatch(r"<PERSON_[0-9a-f]{64}>", token)
    assert masked == f"고객 {token}님, {token} 고객에게 연락했다."
    assert records[1]["token"] == token
    assert [(r["start"], r["end"], r["original"]) for r in records] == [
        (3, 7, "윤 선영"), (second, second + 3, "윤선영")]
    assert {r["normalized"] for r in records} == {"윤선영"}
    assert pii.TOKEN_PATTERN.findall(masked) == [token, token]


def test_determinism_in_a_new_process_and_label_separation():
    expected = pii.make_token("person", "윤선영")
    script = (
        "from starter import pii_pipeline as p; "
        "p.config.HMAC_KEY='unit-test-key-not-for-production'; "
        "print(p.make_token('person', '윤선영'))"
    )
    result = subprocess.run([sys.executable, "-c", script], check=True, capture_output=True, text=True)
    assert result.stdout.strip() == expected
    assert pii.make_token("phone_number", "1234567890") != pii.make_token("account_number", "1234567890")


@pytest.mark.parametrize("spans", [
    [{"label": "person", "start": -1, "end": 2}],
    [{"label": "person", "start": 0, "end": 4}],
    [{"label": "person", "start": 1, "end": 1}],
    [{"label": "person", "start": False, "end": 2}],
    [{"label": "unknown", "start": 0, "end": 3}],
    [{"start": 0, "end": 3}],
    [{"label": "person", "start": 0, "end": 3}, {"label": "person", "start": 2, "end": 3}],
])
def test_invalid_spans_fail_before_masking(spans):
    with pytest.raises(ValueError):
        pii.pseudonymize("윤선영", spans)


def test_missing_key_and_token_collision_are_errors(monkeypatch):
    monkeypatch.setattr(pii.config, "HMAC_KEY", "   ")
    with pytest.raises(RuntimeError):
        pii.make_token("person", "윤선영")
    monkeypatch.setattr(pii, "make_token", lambda *args: "<PERSON_same>")
    with pytest.raises(ValueError, match="충돌"):
        pii.pseudonymize("윤선영 김민준", [
            {"label": "person", "start": 0, "end": 3},
            {"label": "person", "start": 4, "end": 7},
        ])


def test_deleted_value_is_not_a_search_token_or_mapping():
    masked, records = pii.pseudonymize("면허 12-34-567890-12", [
        {"label": "driver_license", "start": 3, "end": 18},
    ])
    assert "567890" not in masked
    assert records == []
    assert pii.TOKEN_PATTERN.findall(masked) == []
