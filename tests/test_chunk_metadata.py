"""청크별 메타데이터 (persons, regions) 생성. 가상 이름·주소만 사용."""
import pytest

from starter import pii_pipeline as pii
from starter.chunk_metadata import build_chunk_metadata
from starter.ingest import chunk


@pytest.fixture(autouse=True)
def synthetic_key(monkeypatch):
    monkeypatch.setattr(pii.config, "HMAC_KEY", "meta-test-key-not-for-production")
    monkeypatch.delenv("ADDRESS_MODE", raising=False)
    monkeypatch.delenv("NAME_LINK", raising=False)
    pii._key.cache_clear()
    yield
    pii._key.cache_clear()


def spans_of(text, *parts):
    out, cur = [], 0
    for label, part in parts:
        s = text.index(part, cur)
        out.append({"label": label, "start": s, "end": s + len(part)})
        cur = s + len(part)
    return out


def build(text, spans, max_chars):
    masked, records = pii.pseudonymize(text, spans)
    parts = chunk(masked, max_chars)
    return masked, parts, build_chunk_metadata("D1", text, spans, masked, records, parts)


TEXT = ("[민원 접수]\n신청인: 홍길동\n주소: 대구 수성구 한빛로 12, 302호\n배송지: 대구광역시 수성구 한빛로 12\n"
        "\n[처리]\n상담사 김하늘: 확인했습니다\n지점: 서울 강남구 ○○로 1\n처리 담당: 이바다 대리")
SPANS = spans_of(TEXT, ("person", "홍길동"), ("address", "대구 수성구 한빛로 12, 302호"),
                 ("address", "대구광역시 수성구 한빛로 12"), ("person", "김하늘"),
                 ("address", "서울 강남구 ○○로 1"), ("person", "이바다"))


def test_each_chunk_gets_its_own_persons_and_regions():
    masked, parts, meta = build(TEXT, SPANS, max_chars=80)
    assert list(meta) == ["D1#0", "D1#1", "D1#2"] and len(parts) == 3
    tok = lambda n: pii.make_token("person", n)
    view = {cid: ([(p["token"], p["role"], p["mention"]) for p in m["persons"]], m["regions"])
            for cid, m in meta.items()}
    assert view == {
        "D1#0": ([(tok("홍길동"), "customer", 0)], ["대구광역시 수성구"]),   # 신청인 + 주소
        "D1#1": ([(tok("김하늘"), "agent", 0)], ["대구광역시 수성구"]),      # 배송지가 이 청크로 넘어옴
        "D1#2": ([(tok("이바다"), "unknown", 0)], ["서울특별시 강남구"]),    # 담당·대리 충돌 → unknown
    }
    assert meta["D1#2"]["persons"][0]["rule_id"] == "ROLE-16"
    assert set(meta["D1#0"]["persons"][0]) == {"token", "role", "mention", "source", "rule_id"}


def test_single_chunk_collects_everything():
    _, parts, meta = build(TEXT, SPANS, max_chars=600)
    assert list(meta) == ["D1#0"] and len(parts) == 1
    assert meta["D1#0"]["regions"] == ["대구광역시 수성구", "서울특별시 강남구"]
    assert [p["mention"] for p in meta["D1#0"]["persons"]] == [0, 1, 2]


def test_same_region_in_two_chunks_is_stored_in_each():
    text = "주소: 대구 수성구 한빛로 12\n" + "가" * 30 + "\n주소: 대구 수성구 가온로 5"
    spans = spans_of(text, ("address", "대구 수성구 한빛로 12"), ("address", "대구 수성구 가온로 5"))
    _, parts, meta = build(text, spans, max_chars=40)
    assert len(parts) == 3
    assert [m["regions"] for m in meta.values()] == [["대구광역시 수성구"], [], ["대구광역시 수성구"]]


def test_region_only_address_stays_in_text_and_is_placed_in_right_chunk():
    text = "상담 메모\n" + "나" * 30 + "\n대구 수성구 쪽 거주 고객 문의 (홍길동)"
    spans = spans_of(text, ("address", "대구 수성구"), ("person", "홍길동"))
    masked, parts, meta = build(text, spans, max_chars=40)
    assert "대구 수성구" in masked                                       # 기존 가명처리 동작 그대로
    assert meta["D1#0"]["regions"] == [] and meta["D1#1"]["regions"] == ["대구광역시 수성구"]


def test_invalid_or_ambiguous_address_is_not_stored():
    text = "주소: 중구 ○○로 3\n주소: 대구 수상구 ○○로 1"
    spans = spans_of(text, ("address", "중구 ○○로 3"), ("address", "대구 수상구 ○○로 1"))
    _, _, meta = build(text, spans, max_chars=600)
    assert meta["D1#0"]["regions"] == []


def test_no_original_values_in_metadata():
    _, _, meta = build(TEXT, SPANS, max_chars=80)
    dumped = repr(meta)
    for raw in ("홍길동", "김하늘", "이바다", "한빛로", "302호"):
        assert raw not in dumped


def test_chunks_without_people_or_addresses_have_empty_lists():
    text = "공지\n" + "다" * 50 + "\n내용 없음"
    _, parts, meta = build(text, [], max_chars=30)
    assert len(meta) == len(parts) > 1
    assert all(m == {"persons": [], "regions": []} for m in meta.values())
