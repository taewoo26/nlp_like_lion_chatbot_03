"""행정구역 PERSON 오탐을 줄이면서 실제 이름을 유지한다."""
import pytest

from detection.base import Span
from detection.extra_rules import drop_admin_region_persons
from starter import pii_pipeline


@pytest.fixture(scope="module")
def detector():
    from detection.token_cls import koelectra, veil
    for adapter in (koelectra, veil):
        if not all((adapter.MODEL_DIR / name).is_file() for name in adapter.FILES):
            pytest.skip("로컬 KoELECTRA·Veil 모델이 필요합니다.")
    return pii_pipeline._detector()


@pytest.mark.parametrize("text,expected_names", [
    ("충남 천안시 서북구 쪽 사는 고객들 문서 다 보여줘", []),
    ("충청남도 천안시 서북구에 거주하는 고객", []),
    ("천안시 서북구 소재 지점", []),
    ("서울특별시 강남구 테헤란로", []),
    ("경기도 성남시 분당구", []),
    ("천안시에서 김민준 고객이 방문했습니다.", ["김민준"]),
    ("서북구 담당자 이서연에게 연락하세요.", ["이서연"]),
    ("김민준 팀장이 천안시 지점에 방문했습니다.", ["김민준"]),
    ("정하은 고객의 주소는 충청남도 천안시 서북구입니다.", ["정하은"]),
])
def test_actual_detector_admin_context(detector, text, expected_names):
    assert [s.text for s in detector.detect(text) if s.label == "person"] == expected_names


@pytest.mark.parametrize("text,keep", [
    ("성명: 천안시", True),
    ("천안시 씨가 방문했습니다.", True),
    ("천안시에게 연락하세요.", True),
    ("행복시에서 방문했습니다.", True),
    ("천안시에서 방문했습니다.", False),
])
def test_name_evidence_and_unknown_places_are_preserved(text, keep):
    value = "행복시" if "행복시" in text else "천안시"
    start = text.index(value)
    candidate = Span(start, start + len(value), "person", value)
    assert bool(drop_admin_region_persons(text, [candidate])) is keep
