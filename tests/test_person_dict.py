"""인물 사전 (3-1 이름 키 · 3-2 식별자 · 3-3 문서 안 묶기 · 3-4 문서 간 연결 · 3-5 토큰과 저장할 행)."""
import json

import pytest

from starter import person_dict as pd
from starter import pii_pipeline as pii


@pytest.fixture(autouse=True)
def synthetic_key(monkeypatch):
    monkeypatch.setattr(pii.config, "HMAC_KEY", "person-dict-test-key-not-for-production")
    pii._key.cache_clear()
    yield
    pii._key.cache_clear()


@pytest.mark.parametrize("raw,expected", [
    ("김민준", "김민준"), ("김 민준", "김민준"), (" 남궁 나연 ", "남궁나연"), ("황보 현우", "황보현우"),
])
def test_normalize_name(raw, expected):
    assert pd.normalize_name(raw) == expected


@pytest.mark.parametrize("name,expected", [
    ("김민준", ("김", "민준")), ("남궁나연", ("남궁", "나연")), ("제갈찬", ("제갈", "찬")),
    ("안윤", ("안", "윤")), ("남궁", ("남", "궁")), ("린", ("", "린")),
])
def test_split_name(name, expected):
    assert pd.split_name(name) == expected


def test_same_name_same_key_regardless_of_spacing():
    assert pd.name_hmac(pd.normalize_name("김 민준")) == pd.name_hmac(pd.normalize_name("김민준"))


def test_different_names_different_keys():
    assert pd.name_hmac("김민준") != pd.name_hmac("김민주")


def test_key_has_no_raw_name_and_differs_from_person_token():
    key = pd.name_hmac("김민준")
    assert "김민준" not in key and len(key) == 64
    token_hex = pii.make_token("person", "김민준")[len("<PERSON_"):-1]
    assert not key.startswith(token_hex)          # 가명 토큰과 용도 분리


def test_empty_name_rejected():
    with pytest.raises(ValueError):
        pd.name_hmac("")


# ---------------------------------------------------------------- 3-2 식별자 붙이기
def spans_of(text, items):
    """[(label, 문자열), ...] → span 목록 (앞에서부터 차례로 찾음)."""
    out, cursor = [], 0
    for label, value in items:
        start = text.index(value, cursor)
        out.append({"label": label, "start": start, "end": start + len(value)})
        cursor = start + len(value)
    return out


def attached(text, items):
    spans = spans_of(text, items)
    result = pd.attach_identifiers(text, spans)
    return {text[s:e]: toks for (s, e), toks in result.items()}


def phone(value):
    return pii.make_token("phone_number", pii.normalize_value("phone_number", value))


def test_field_line_attaches():
    text = "고객명: 김도윤 / 고객번호: C-F00182 / 휴대전화: 010-0001-1001"
    assert attached(text, [("person", "김도윤"), ("phone_number", "010-0001-1001")]) == {"김도윤": [phone("010-0001-1001")]}


def test_parentheses_attach_even_with_two_people():
    text = "상담원 박수현이 김민준(010-0933-5186) 고객에게 안내"
    got = attached(text, [("person", "박수현"), ("person", "김민준"), ("phone_number", "010-0933-5186")])
    assert got == {"김민준": [phone("010-0933-5186")]}


def test_bare_value_cell_attaches():
    text = "신청인: 김민준 / 010-0933-5186"
    assert attached(text, [("person", "김민준"), ("phone_number", "010-0933-5186")]) == {"김민준": [phone("010-0933-5186")]}


def test_table_row_attaches():
    text = "| 성명 | 소속 | 연락처 |\n| 김민준 | 고객센터 | 02-1234-5678 |"
    assert attached(text, [("person", "김민준"), ("phone_number", "02-1234-5678")]) == {"김민준": [phone("02-1234-5678")]}


def test_number_inside_sentence_not_attached():                     # 설계서 8.3 / 규칙 정의서 ATT-11
    text = "직원 김민준이 010-1234-5678로 전화한 고객을 응대함"
    assert attached(text, [("person", "김민준"), ("phone_number", "010-1234-5678")]) == {}


def test_narrative_field_not_attached():                            # ATT-10
    text = "상담 내용: 고객 김도윤은 010-1111-2222 번호로 재문의 요청"
    assert attached(text, [("person", "김도윤"), ("phone_number", "010-1111-2222")]) == {}


def test_other_line_not_attached():                                 # 김민준 / 0100433244 사례
    text = "직원: 김민준\n내용: 0100433244 라는 번호로 연락이 왔습니다"
    assert attached(text, [("person", "김민준"), ("phone_number", "0100433244")]) == {}


def test_two_people_on_line_not_attached():                         # ATT-12
    text = "상담원: 박수현 / 고객: 김도윤 / 연락처: 010-2222-3333"
    got = attached(text, [("person", "박수현"), ("person", "김도윤"), ("phone_number", "010-2222-3333")])
    assert got == {}


def test_same_person_twice_on_line_counts_once():
    text = "예금주: 박하준 / 신청인: 박 하준 / 출금 계좌: 가상은행 123-456-789012"
    got = attached(text, [("person", "박하준"), ("person", "박 하준"), ("account_number", "123-456-789012")])
    token = pii.make_token("account_number", pii.normalize_value("account_number", "123-456-789012"))
    assert got == {"박하준": [token]}


def test_card_and_address_not_used():
    text = "고객명: 정민지 / 카드번호: 1234-5678-9012-3456 / 주소: 서울특별시 강서구 가상로 55"
    got = attached(text, [("person", "정민지"), ("card_number", "1234-5678-9012-3456"),
                          ("address", "서울특별시 강서구 가상로 55")])
    assert got == {}


# ---------------------------------------------------------------- 3-3 문서 안 묶기
from starter.roles import default_rules

RULES = default_rules()


def entities(text, items):
    found = pd.group_mentions(text, spans_of(text, items), RULES)
    return sorted((e["name"], e["kind"], tuple(text[s:t] for s, t in e["mentions"])) for e in found)


def test_r1_spacing_variants_are_one_entity():
    text = "고객 김민준 님 문의\n김 민준 고객님께 안내함"
    assert entities(text, [("person", "김민준"), ("person", "김 민준")]) == [("김민준", "full", ("김민준", "김 민준"))]


def test_r2_given_name_links_to_full_name():
    text = "신청인: 신도윤\n도윤 님께 처리 결과를 안내함"
    assert entities(text, [("person", "신도윤"), ("person", "도윤")]) == [("신도윤", "full", ("신도윤", "도윤"))]


def test_r2_role_conflict_keeps_apart():                               # H0113 사례
    text = "상담사 민준: 네, 확인해 드릴게요\n김민준 고객님 본인 확인 부탁드립니다"
    assert entities(text, [("person", "민준"), ("person", "김민준")]) == [
        ("김민준", "full", ("김민준",)), ("민준", "given", ("민준",))]


def test_r3_two_full_name_candidates_no_link():
    text = "고객 김민준, 고객 이민준 두 분 건\n민준 님 연락 예정"
    got = entities(text, [("person", "김민준"), ("person", "이민준"), ("person", "민준")])
    assert ("민준", "given", ("민준",)) in got and len(got) == 3


def test_same_full_name_different_roles_split():
    text = "고객 김하늘 님 민원 접수\n담당: 김하늘 대리가 처리함"
    got = entities(text, [("person", "김하늘"), ("person", "김하늘")])
    assert len(got) == 2


def test_identifiers_collected_on_entity():
    text = "고객명: 김도윤 / 휴대전화: 010-0001-1001\n김 도윤 고객님께 문자 안내"
    found = pd.group_mentions(text, spans_of(text, [("person", "김도윤"), ("phone_number", "010-0001-1001"),
                                                     ("person", "김 도윤")]), RULES)
    assert len(found) == 1 and found[0]["ids"] == [phone("010-0001-1001")]


# ---------------------------------------------------------------- 3-4 문서 간 연결 (R4~R8)
RRN_A = "<RESIDENT_REGISTRATION_NUMBER_aaaaaaaaaaaa>"
RRN_B = "<RESIDENT_REGISTRATION_NUMBER_bbbbbbbbbbbb>"
PHONE_1 = "<PHONE_NUMBER_111111111111>"
PHONE_2 = "<PHONE_NUMBER_222222222222>"


def ent(name, groups=(), ids=(), kind="full"):
    """문서 안 개체 (group_mentions 결과와 같은 모양)."""
    return {"name": name, "kind": kind, "mentions": [(0, len(name))], "groups": set(groups),
            "ids": list(ids), "rules": ["R1"]}


def link(*entities):
    """[(doc_id, 개체), ...] 를 연결하고, 같은 사람으로 묶인 문서 번호 묶음과 상태를 돌려준다."""
    res = pd.link_documents([(f"D{i}", e) for i, e in enumerate(entities)])
    return sorted((tuple(p["members"]), p["status"]) for p in res["persons"]), res


@pytest.fixture
def switches(monkeypatch):
    monkeypatch.delenv("PERSON_LINK_UNIQUE", raising=False)
    monkeypatch.delenv("PERSON_LINK_GIVEN", raising=False)
    return monkeypatch


def test_r4_same_name_same_phone_is_one_person(switches):
    got, _ = link(ent("김도윤", ids=[PHONE_1]), ent("김도윤", ids=[PHONE_1]))
    assert got == [((0, 1), "confirmed")]


def test_r4_different_names_sharing_phone_not_linked(switches):    # 가족·대리인이 같은 번호
    got, res = link(ent("김도윤", ids=[PHONE_1]), ent("김하늘", ids=[PHONE_1]))
    assert len(got) == 2 and res["unlinkable"] == {PHONE_1}
    assert all(status == "provisional" for _, status in got)       # 공유 번호는 근거로 안 씀


def test_r5_different_rrn_never_merged(switches):
    got, _ = link(ent("김도윤", ids=[PHONE_1, RRN_A]), ent("김도윤", ids=[PHONE_1, RRN_B]))
    assert got == [((0,), "confirmed"), ((1,), "confirmed")]


def test_r6_is_on_by_default(switches):
    got, _ = link(ent("김도윤", ["customer"], [PHONE_1]), ent("김도윤", ["customer"]))
    assert got == [((0, 1), "confirmed")]


def test_r6_off_keeps_loose_entity_provisional(switches):
    switches.setenv("PERSON_LINK_UNIQUE", "off")
    got, _ = link(ent("김도윤", ["customer"], [PHONE_1]), ent("김도윤", ["customer"]))
    assert got == [((0,), "confirmed"), ((1,), "provisional")]


def test_r6_role_conflict_stays_provisional(switches):             # 확정 인물은 직원, 근거 없는 쪽은 고객
    got, _ = link(ent("김민준", ["staff"], [PHONE_1]), ent("김민준", ["customer"]))
    assert got == [((0,), "confirmed"), ((1,), "provisional")]


def test_r6_one_person_with_two_numbers_merged(switches):          # 사무실·휴대폰 번호
    got, _ = link(ent("김도윤", ["staff"], [PHONE_1]), ent("김도윤", ["staff"], [PHONE_2]))
    assert got == [((0, 1), "confirmed")]


def test_r6_ambiguous_unknown_role_goes_to_provisional(switches):  # 붙일 곳이 2곳 → 이름 토큰 유지
    got, _ = link(ent("김민준", ["staff"], [PHONE_1]), ent("김민준", ["customer"], [PHONE_2]),
                  ent("김민준"), ent("김민준"))
    assert got == [((0,), "confirmed"), ((1,), "confirmed"), ((2, 3), "provisional")]


def test_r7_off_by_default_given_name_not_linked(switches):
    got, _ = link(ent("김재원", ["staff"], [PHONE_1]), ent("재원", ["staff"], kind="given"))
    assert len(got) == 2


def test_r7_on_links_staff_given_name(switches):
    switches.setenv("PERSON_LINK_GIVEN", "on")
    got, _ = link(ent("김재원", ["staff"], [PHONE_1]), ent("재원", ["staff"], kind="given"))
    assert got == [((0, 1), "confirmed")]


def test_r8_customer_given_name_not_linked_even_with_r7(switches):
    switches.setenv("PERSON_LINK_GIVEN", "on")
    got, _ = link(ent("김재원", ["customer"], [PHONE_1]), ent("재원", ["customer"], kind="given"))
    assert len(got) == 2


def test_bad_switch_value_rejected(switches):
    switches.setenv("PERSON_LINK_UNIQUE", "yes")
    with pytest.raises(ValueError):
        link(ent("김도윤"))


# ---------------------------------------------------------------- 3-5 토큰 만들기 · 저장할 행
def test_confirmed_token_from_person_id_not_name():
    a = {"status": "confirmed", "person_id": "11111111-1111-4111-8111-111111111111", "name": "김민준"}
    b = dict(a, person_id="22222222-2222-4222-8222-222222222222")
    assert pd.person_token(a) != pd.person_token(b)                     # 동명이인이라도 사람이 다르면 다른 토큰
    assert pd.person_token(a) == pd.person_token(dict(a))               # 같은 사람은 항상 같은 토큰
    assert pd.person_token(a) != pii.make_token("person", "김민준")
    assert pii.TOKEN_PATTERN.fullmatch(pd.person_token(a)) and len(pd.person_token(a)) == len("<PERSON_>") + 12


def test_provisional_token_is_todays_name_token():
    assert pd.person_token({"status": "provisional", "person_id": "x", "name": "김민준"}) == \
        pii.make_token("person", "김민준")


@pytest.mark.parametrize("rrn,expected", [
    ("9001011234567", "M"), ("9001012234567", "F"), ("0101013234567", "M"), ("0101014234567", "F"),
    ("900101123456", None), (None, None),
])
def test_gender_from_rrn(rrn, expected):
    assert pd.gender_from_rrn(rrn) == expected


def doc(doc_id, text, items):
    return {"id": doc_id, "text": text, "spans": spans_of(text, items)}


def ids_counter():
    n = iter(range(1, 100))
    return lambda: f"00000000-0000-4000-8000-{next(n):012d}"


@pytest.fixture
def two_docs(monkeypatch):
    monkeypatch.delenv("PERSON_LINK_UNIQUE", raising=False)
    monkeypatch.delenv("PERSON_LINK_GIVEN", raising=False)
    d1 = doc("D1", "고객명: 김도윤 / 휴대전화: 010-0001-1001\n도윤 님께 안내함",
             [("person", "김도윤"), ("phone_number", "010-0001-1001"), ("person", "도윤")])
    d2 = doc("D2", "고객 김도윤 / 연락처: 010-0001-1001",
             [("person", "김도윤"), ("phone_number", "010-0001-1001")])
    d3 = doc("D3", "담당: 김도윤 대리 / 내선: 02-3779-0001",
             [("person", "김도윤"), ("phone_number", "02-3779-0001")])
    return [d1, d2, d3], pd.build_dictionary([d1, d2, d3], RULES, new_id=ids_counter())


def test_dictionary_same_phone_one_confirmed_token(two_docs):
    docs, dic = two_docs
    def t(d, n):                                                        # 문서 d 의 n번째 이름 언급 토큰
        s = [x for x in d["spans"] if x["label"] == "person"][n]
        return dic["mentions"][(d["id"], s["start"], s["end"])]["token"]
    d1, d2, d3 = docs
    assert t(d1, 0) == t(d2, 0) == t(d1, 1)                             # R4 + 문서 안 R2 ('도윤')
    assert t(d3, 0) != t(d1, 0)                                         # 고객 ↔ 직원: 역할 충돌 → 다른 사람
    assert all(p["status"] == "confirmed" for p in dic["persons"])


def test_vault_rows_have_no_raw_names(two_docs):
    _, dic = two_docs
    rows = pd.vault_rows(dic)
    assert len(rows["registry"]) == 2 and len(rows["logs"]) == 2
    kinds = {(a["name_hmac"], a["kind"]) for a in rows["aliases"]}
    assert (pd.name_hmac("김도윤"), "full") in kinds and (pd.name_hmac("도윤"), "given") in kinds
    assert all(i["linkable"] for i in rows["identifiers"]) and len(rows["identifiers"]) == 2
    assert "김도윤" not in json.dumps(rows, ensure_ascii=False, default=str)


def test_lookup_rows_list_both_homonyms(two_docs):
    _, dic = two_docs
    rows = pd.lookup_rows(dic)
    same_name = [r for r in rows if r["name_hmac"] == pd.name_hmac("김도윤")]
    assert len(same_name) == 2
    assert sorted(tuple(r["roles"]) for r in same_name) == [("customer",), ("employee",)]
    assert {r["doc_count"] for r in same_name} == {2, 1}
    assert "김도윤" not in json.dumps(rows, ensure_ascii=False)


def test_retokenize_replaces_tokens_and_keeps_short_name_alias(two_docs):
    docs, dic = two_docs
    d1 = docs[0]
    masked, records = pii.pseudonymize(d1["text"], d1["spans"])
    new_masked, new_records = pd.retokenize(masked, records, dic["mentions"], "D1")
    person = [r for r in new_records if r["label"] == "person"]
    assert person[0]["token"] == person[1]["token"] != records[0]["token"]
    assert person[1]["normalized"] == "김도윤" and person[1]["alias_token"] == pii.make_token("person", "도윤")
    assert [m.group() for m in pii.TOKEN_PATTERN.finditer(new_masked)] == [r["token"] for r in new_records]
    assert "김도윤" not in new_masked


def test_retokenize_rejects_misaligned_records(two_docs):
    docs, dic = two_docs
    masked, records = pii.pseudonymize(docs[0]["text"], docs[0]["spans"])
    with pytest.raises(ValueError):
        pd.retokenize(masked, records[1:], dic["mentions"], "D1")


def test_shared_identifier_marked_not_linkable(monkeypatch):
    monkeypatch.delenv("PERSON_LINK_UNIQUE", raising=False)
    d1 = doc("D1", "신청인: 김도윤 / 연락처: 010-0001-1001", [("person", "김도윤"), ("phone_number", "010-0001-1001")])
    d2 = doc("D2", "대리인: 김하늘 / 연락처: 010-0001-1001", [("person", "김하늘"), ("phone_number", "010-0001-1001")])
    dic = pd.build_dictionary([d1, d2], RULES, new_id=ids_counter())
    rows = pd.vault_rows(dic)
    assert len(dic["unlinkable"]) == 1
    assert [i["linkable"] for i in rows["identifiers"]] == [False, False]
    assert all(p["status"] == "provisional" for p in dic["persons"])
