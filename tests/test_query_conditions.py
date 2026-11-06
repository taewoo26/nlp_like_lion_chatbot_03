"""질의 역할·지역 조건 분석과 문서 판정 (ROLE-40~46, REG-30~35). 가상 이름만 사용."""
import pytest

from starter import pii_pipeline as pii
from starter.address import query_regions
from starter.query_conditions import analyze, doc_tier, may_have_conditions, role_tier
from starter.role_master import load_role_master


@pytest.fixture(autouse=True)
def synthetic_key(monkeypatch):
    monkeypatch.setattr(pii.config, "HMAC_KEY", "query-cond-test-key")
    pii._key.cache_clear()
    yield
    pii._key.cache_clear()


def cond_of(query, *names):
    spans, cur = [], 0
    for n in names:
        s = query.index(n, cur); spans.append({"label": "person", "start": s, "end": s + len(n)}); cur = s + len(n)
    _, records = pii.pseudonymize(query, spans, strict=False)
    return analyze(query, records, load_role_master())


T = lambda n: pii.make_token("person", n)


# ---------------------------------------------------------------- 질의 역할 (ROLE-40·45·46)
@pytest.mark.parametrize("query,name,role", [
    ("김민준 고객의 상담 기록", "김민준", "customer"),
    ("직원 김민준의 업무 보고서", "김민준", "employee"),
    ("상담사 홍길동 응대 내역", "홍길동", "agent"),
    ("담당자 김하늘 연락처", "김하늘", "staff_contact"),
    ("보호자 이바다 위임장", "이바다", "guardian"),
])
def test_query_role_from_query_cues(query, name, role):
    c = cond_of(query, name)
    assert [(r["token"], r["role"]) for r in c.roles] == [(T(name), role)]


def test_no_cue_no_condition():                                         # ROLE-40
    c = cond_of("홍길동 결제 문의", "홍길동")
    assert c.roles == [] and not c.active


def test_conflicting_query_cues_give_no_condition_and_notice():
    c = cond_of("고객 홍길동 직원", "홍길동")
    assert c.roles == [] and c.notices


def test_doc_cues_are_not_used_for_queries():                           # ROLE-45: 질의 단서만
    assert cond_of("홍길동 대리 보고서", "홍길동").roles == []


def test_staff_query_accepts_staff_group():                             # ROLE-46 (초안)
    c = cond_of("직원 홍길동", "홍길동")
    assert c.roles[0]["accept"] == {"employee", "agent", "staff_contact"}


# ---------------------------------------------------------------- 문서 판정 (ROLE-41~44)
COND = {"token": "<P>", "role": "customer", "accept": frozenset({"customer"}), "name": "고객"}


@pytest.mark.parametrize("persons,tier", [
    ([{"token": "<P>", "role": "customer"}], 1),                                       # ROLE-41
    ([{"token": "<P>", "role": "unknown"}], 2),                                        # ROLE-42
    ([{"token": "<P>", "role": "employee"}], 3),                                       # ROLE-43
    ([{"token": "<P>", "role": "employee"}, {"token": "<P>", "role": "customer"}], 1),  # ROLE-44
    ([{"token": "<P>", "role": "employee"}, {"token": "<P>", "role": "unknown"}], 2),
    ([{"token": "<Q>", "role": "employee"}], 2),            # 그 토큰 언급 정보 없음 → 미상
    ([], 2),                                                 # 메타데이터 없는 문서 → 미상 (제외 안 함)
])
def test_role_tier(persons, tier):
    assert role_tier(persons, COND) == tier


def test_doc_tier_takes_worst_of_conditions():
    other = COND | {"token": "<Q>"}
    assert doc_tier([{"token": "<P>", "role": "customer"}, {"token": "<Q>", "role": "agent"}], [COND, other]) == 3


# ---------------------------------------------------------------- 질의 지역 (REG-30~35)
@pytest.mark.parametrize("query,expected", [
    ("대구 수성구 관련 문서", [("대구광역시 수성구", "sigungu")]),          # REG-31
    ("대구 사는 고객", [("대구광역시", "sido")]),                           # REG-30
    ("대구에서 접수된 민원", [("대구광역시", "sido")]),
    ("수성구에 사는 고객", [("대구광역시 수성구", "sigungu")]),             # REG-32 (초안)
    ("경기 수원시 영통구 지점", [("경기도 수원시 영통구", "sigungu")]),
    ("세종시 고객", [("세종특별자치시", "sido")]),
    ("홍길동 결제 문의", []),                                                # REG-35
    ("고객센터 문의", []),
    ("강남구청 민원", []),                                                   # 다른 단어의 일부는 지역 아님
])
def test_query_regions(query, expected):
    assert [(r.value, r.kind) for r in query_regions(query)] == expected


@pytest.mark.parametrize("query", ["중구 고객", "광주시 고객 문의"])
def test_ambiguous_region_asks_back(query):                             # REG-33 (초안)
    c = cond_of(query)
    assert c.clarify and c.regions == []


def test_region_plus_role_without_name():                               # REG-34
    c = cond_of("대구 사는 고객")
    assert c.regions == ["대구광역시"]
    assert [(r["token"], r["role"]) for r in c.roles] == [(None, "customer")]


def test_role_word_without_name_and_region_is_ignored():
    assert cond_of("고객 상담 기록 보여줘").roles == []


def test_precheck_skips_plain_queries():
    assert not may_have_conditions("결제 알림 미수신 처리 기록")
    assert may_have_conditions("대구 결제") and may_have_conditions("직원 홍길동")
