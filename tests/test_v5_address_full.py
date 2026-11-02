"""v5: 주소 전체 가명처리(ADDRESS_MODE=full 기본)와 region 비교 모드."""
import pytest

from starter import pii_pipeline as pii

TEXT = "주소: 대구광역시 수성구 한빛로 174, 502호"


def _span(text, value):
    start = text.index(value)
    return [{"start": start, "end": start + len(value), "label": "address"}]


@pytest.fixture(autouse=True)
def _key(monkeypatch):
    monkeypatch.setattr(pii.config, "HMAC_KEY", "address-full-test-key")
    pii._key.cache_clear()
    yield
    pii._key.cache_clear()


def _mask(monkeypatch, mode, text=TEXT, value="대구광역시 수성구 한빛로 174, 502호"):
    if mode is None:
        monkeypatch.delenv("ADDRESS_MODE", raising=False)
    else:
        monkeypatch.setenv("ADDRESS_MODE", mode)
    return pii.pseudonymize(text, _span(text, value))


def test_full_is_default_and_hides_region(monkeypatch):
    masked, records = _mask(monkeypatch, None)
    assert "수성구" not in masked and "대구" not in masked
    assert masked.startswith("주소: <ADDRESS_") and masked.count("<ADDRESS_") == 1
    assert records[0]["original"] == "대구광역시 수성구 한빛로 174, 502호"


def test_region_mode_keeps_region_and_same_token(monkeypatch):
    full, rf = _mask(monkeypatch, "full")
    region, rr = _mask(monkeypatch, "region")
    assert region.startswith("주소: 대구광역시 수성구 <ADDRESS_")
    assert rf[0]["token"] == rr[0]["token"]          # 모드가 달라도 같은 주소 = 같은 토큰


def test_query_and_doc_variants_share_token(monkeypatch):
    _, doc = _mask(monkeypatch, "full")
    q = "대구 수성구 한빛로 174, 502호 고객"
    _, query = _mask(monkeypatch, "full", q, "대구 수성구 한빛로 174, 502호")
    assert doc[0]["token"] == query[0]["token"]


def test_region_only_mention_stays_text(monkeypatch):
    masked, records = _mask(monkeypatch, "full", "대구 수성구 고객", "대구 수성구")
    assert masked == "대구 수성구 고객" and records == []


def test_bad_mode(monkeypatch):
    with pytest.raises(ValueError):
        _mask(monkeypatch, "city")
