"""주소 표기 변형과 다른 주소의 경계, 원문 기준 치환을 검증한다."""
import pytest

from starter import pii_pipeline as pii


@pytest.fixture(autouse=True)
def test_key(monkeypatch):
    monkeypatch.setattr(pii.config, "HMAC_KEY", "address-test-key-not-for-production")
    pii._key.cache_clear()
    yield
    pii._key.cache_clear()


@pytest.mark.parametrize("left,right", [
    ("서울 송파구 도담길 125, 110동 905호", "서울특별시 송파구 도담길125 110동905호"),
    ("경기 성남시 분당구 가온로 12", "경기도 성남시 분당구 가온로12"),
    ("강원도 원주시 가온로 12", "강원특별자치도 원주시 가온로12"),
    ("(06236) 서울 강남구 테헤란로 123", "서울 강남구 테헤란로 123"),
    ("06236 서울 강남구 테헤란로 123", "서울 강남구 테헤란로 123"),
    ("110동905호", "110동 905호"),
    ("서울 강남구 역삼동 123-45번지", "서울 강남구 역삼동 123-45"),
    ("대구 수성구 범어동 123 (범어동)", "대구 수성구 범어동 123"),
    ("서울 송파구 도담길 １２５ １１０동 ９０５호", "서울 송파구 도담길125 110동905호"),
])
def test_address_equivalent_forms(left, right):
    values = [pii.normalize_value("address", v) for v in (left, right)]
    assert values[0] == values[1]
    assert pii.make_token("address", values[0]) == pii.make_token("address", values[1])


@pytest.mark.parametrize("left,right", [
    ("가온로 12 3층", "가온로 1 23층"),
    ("서울 중구 도담길 125 110동 905호", "서울 송파구 도담길 125 110동 905호"),
    ("서울 송파구 도담길 125 (가온아파트) 905호", "서울 송파구 도담길 125 (다온아파트) 905호"),
    ("서울 송파구 도담길 125 (103동) 905호", "서울 송파구 도담길 125 (104동) 905호"),
    ("대구 수성구 솔내로 15 (범어동)", "대구 수성구 솔내로 15"),
    ("도담길 125 110동 905호", "도담길 125 110-905"),
    ("서울 송파구 도담길 125 110동 905호", "110동 905호"),
    ("12345 가온아파트 905호", "가온아파트 905호"),
    ("서울숲길 12", "서울특별시 숲길 12"),
    ("서울 송파구 도담길 125 (가온,나래)", "서울 송파구 도담길 125 (가온 나래)"),
])
def test_different_address_information_is_preserved(left, right):
    values = [pii.normalize_value("address", v) for v in (left, right)]
    assert values[0] != values[1]
    assert pii.make_token("address", values[0]) != pii.make_token("address", values[1])


@pytest.mark.parametrize("address,expected_region", [
    ("(06236) 서울 강남구 테헤란로 123 (가온아파트) 905호", "서울 강남구"),
    ("서울시 강남구 테헤란로 123", "서울시 강남구"),
    ("경기 성남시 분당구 가온로 12", "경기 성남시 분당구"),
    ("성남시 분당구 가온로 12", "성남시 분당구"),
    ("110동 905호", ""),
    ("123 Main St, Los Angeles, CA 90012", ""),
    ("서울숲길 12", ""),
])
def test_address_masking_preserves_only_original_region(address, expected_region):
    text = "주소: " + address
    span = {"label": "address", "start": 4, "end": len(text)}
    masked, records = pii.pseudonymize(text, [span])
    token = records[0]["token"]
    prefix = expected_region + " " if expected_region else ""
    assert masked == f"주소: {prefix}{token}"
    assert records[0]["original"] == address
    assert records[0]["start"] == 4
    assert records[0]["end"] == len(text)
    assert pii.TOKEN_PATTERN.findall(masked) == [token]
    if "06236" in address:
        assert "06236" not in masked
        assert "06236" not in records[0]["normalized"]


@pytest.mark.parametrize("region", ["서울", "서울시", "서울 강남구", "성남시 분당구"])
def test_region_only_does_not_create_token_or_need_key(monkeypatch, region):
    monkeypatch.setattr(pii.config, "HMAC_KEY", "")
    text = "지역: " + region
    masked, records = pii.pseudonymize(text, [{"label": "address", "start": 4, "end": len(text)}])
    assert masked == text
    assert records == []


def test_unknown_building_parentheses_are_not_removed():
    value = pii.normalize_value("address", "서울 송파구 도담길 125 (가온아파트 103동) 905호")
    assert "(가온아파트 103동)" in value


def test_unknown_address_format_only_collapses_whitespace():
    assert pii.normalize_value("address", "Unknown  building,  12  3") == "Unknown building, 12 3"


@pytest.mark.parametrize('address,region', [
    ('청구 101동 905호', ''),
    ('서울 가구 101동 905호', '서울'),
    ('서울 부산진구 도담길 125', '서울'),  # 존재하는 구라도 시도가 다르면 보존하지 않음
    ('미확인시 도담길 125', ''),
])
def test_unverified_region_is_masked(address, region):
    masked, records = pii.pseudonymize(address, [{'label': 'address', 'start': 0, 'end': len(address)}])
    assert masked == (region + ' ' if region else '') + records[0]['token']
