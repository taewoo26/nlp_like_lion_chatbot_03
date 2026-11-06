"""지역 메타데이터 추출 (메타데이터 규칙 정의서 v0.2 REG). 가상 주소만 사용."""
import pytest

from starter import pii_pipeline as pii
from starter.address import extract_regions, region_detail, standard_region


@pytest.mark.parametrize("address,expected", [
    ("대구 수성구 한빛로 12, 302호", "대구광역시 수성구"),             # TST-18, REG-02·10
    ("대구시 수성구 ○○로 3", "대구광역시 수성구"),                      # REG-10 변형 표기
    ("서울 중구 ○○로 1", "서울특별시 중구"),
    ("(06000) 서울특별시 강남구 ○○로 1", "서울특별시 강남구"),          # 우편번호 앞부분
    ("경기 수원시 영통구 ○○로 5", "경기도 수원시 영통구"),              # REG-03 (초안, 엑셀대로)
    ("충남 천안시 서북구 ○○로 5", "충청남도 천안시 서북구"),            # TST-34-N
    ("경기 고양시 ○○로 7", "경기도 고양시"),                            # 구 없이 시만
    ("강원도 춘천시 ○○로 1", "강원특별자치도 춘천시"),                  # 옛 명칭
    ("전라북도 전주시 완산구 ○○로 1", "전북특별자치도 전주시 완산구"),
    ("제주도 서귀포시 ○○로 1", "제주특별자치도 서귀포시"),
    ("세종시 ○○로 7", "세종특별자치시"),                                # TST-33-N, REG-04
    ("세종특별자치시 조치원읍 ○○로 7", "세종특별자치시"),
    ("경기 광주시 ○○로 9", "경기도 광주시"),                            # TST-32-N
    ("광주 북구 ○○로 9", "광주광역시 북구"),
    ("광주시 북구 ○○로 9", "광주광역시 북구"),                          # 03_지역표준화 비고
    ("수성구 ○○로 3", "대구광역시 수성구"),                             # TST-27-N, REG-11 (초안)
    ("수원시 영통구 ○○로 3", "경기도 수원시 영통구"),
])
def test_standard_region(address, expected):
    assert standard_region(address) == expected


@pytest.mark.parametrize("address,reason", [
    ("대구 수상구 ○○로", "invalid_sigungu"),        # REG-05: 시도만 남기지 않음
    ("대구 강남구 ○○로", "invalid_sigungu"),        # 다른 시도의 시군구
    ("수상구 ○○로 3", "invalid_sigungu"),
    ("중구 ○○로 3", "ambiguous_sigungu"),           # TST-28-N, REG-11
    ("고성군 ○○면 1", "ambiguous_sigungu"),
    ("광주시 ○○로 9", "ambiguous_gwangju"),         # 판단 보류
    ("대구광역시 중앙대로 1", "no_sigungu"),        # 판단 보류
    ("110동 905호", "no_region"),
])
def test_not_stored(address, reason):
    assert region_detail(address) == (None, reason)


def test_fullwidth_and_spacing():
    assert standard_region("대구　수성구　한빛로　１２") == "대구광역시 수성구"


def spans(text, *parts):
    out = []
    for label, part in parts:
        s = text.index(part)
        out.append({"label": label, "start": s, "end": s + len(part)})
    return out


def test_only_address_spans_are_used():                                    # TST-29-N, REG-01·07
    text = "부산에서 결제 시도. 담당: 홍길동"
    assert extract_regions(text, spans(text, ("person", "홍길동"))) == []


def test_dedupe_keeps_first_order_and_mixes_address_types():               # REG-06·21
    text = ("주소: 대구 수성구 한빛로 12\n지점: 서울 강남구 ○○로 1\n"
            "배송지: 대구광역시 수성구 한빛로 12, 302호\n주소: 중구 ○○로 3")
    s = spans(text, ("address", "대구 수성구 한빛로 12"), ("address", "서울 강남구 ○○로 1"),
              ("address", "대구광역시 수성구 한빛로 12, 302호"), ("address", "중구 ○○로 3"))
    result = extract_regions(text, s)
    assert result == ["대구광역시 수성구", "서울특별시 강남구"]
    assert all(isinstance(v, str) for v in result)                          # TEXT[] 로 바로 저장 가능


def test_regions_do_not_change_pseudonymized_text(monkeypatch):            # REG-22, 기존 가명처리 유지
    monkeypatch.setattr(pii.config, "HMAC_KEY", "region-test-key-not-for-production")
    monkeypatch.delenv("ADDRESS_MODE", raising=False)
    pii._key.cache_clear()
    text = "주소: 대구 수성구 한빛로 12, 302호"
    s = spans(text, ("address", "대구 수성구 한빛로 12, 302호"))
    masked, records = pii.pseudonymize(text, s)
    assert "수성구" not in masked and "대구" not in masked                   # full 모드: 본문에 지역 없음
    assert extract_regions(text, s) == ["대구광역시 수성구"]                 # 지역은 별도 값으로만
    assert records[0]["token"] == pii.make_token("address", records[0]["normalized"])
    pii._key.cache_clear()
