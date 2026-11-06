"""지역 메타데이터 규칙 (규칙 정의서 REG, 엑셀 07 시트)."""
import pytest

from starter.address import query_regions, regions_of
from starter.ingest import doc_regions


@pytest.mark.parametrize("address,expected", [
    ("대구 수성구 한빛로 12, 302호", {"대구광역시", "대구광역시 수성구"}),           # TST-18 + 시도 단독 값
    ("수성구 ○○로 3", {"대구광역시", "대구광역시 수성구"}),                          # TST-27-N 전국 유일
    ("중구 ○○로 3", set()),                                                          # TST-28-N 여러 곳
    ("경기 광주시 ○○로 9", {"경기도", "경기도 광주시"}),                             # TST-32-N
    ("광주 북구 ○○로 9", {"광주광역시", "광주광역시 북구"}),
    ("세종시 ○○로 7", {"세종특별자치시"}),                                           # TST-33-N
    ("충남 천안시 서북구 ○○로 5", {"충청남도", "충청남도 천안시 서북구"}),          # TST-34-N
    ("(05299) 서울특별시 강남구 한빛로 9, 2층", {"서울특별시", "서울특별시 강남구"}),
    ("대구 수상구 ○○로", {"대구광역시"}),                                            # 없는 시군구는 저장 안 함
])
def test_regions_of(address, expected):
    assert regions_of(address) == expected


def test_only_address_spans_are_used():                                                 # TST-29-N, REG-01
    text = "부산에서 결제 시도가 있었습니다. 주소: 대구 수성구 한빛로 12"
    start = text.index("대구")
    spans = [{"label": "address", "start": start, "end": len(text)}]
    assert doc_regions(text, spans) == ["대구광역시", "대구광역시 수성구"]
    assert doc_regions(text, []) == []


@pytest.mark.parametrize("query,expected", [
    ("대구 사는 고객", (["대구광역시"], None)),
    ("대구 수성구 고객", (["대구광역시 수성구"], None)),
    ("수성구 고객", (["대구광역시 수성구"], None)),
    ("중구 고객 문서 찾아줘", ([], "중구")),                                           # TST-19 되묻기
    ("충남 아산시에 거주하는 고객 관련 문서 찾아주세요", (["충청남도 아산시"], None)),  # 조사 떼기
    ("충남 천안시 서북구 쪽 사는 고객들 문서 다 보여줘", (["충청남도 천안시 서북구"], None)),
    ("직원 김민준 관련 문서만 찾아주세요", ([], None)),
])
def test_query_regions(query, expected):
    assert query_regions(query) == expected
