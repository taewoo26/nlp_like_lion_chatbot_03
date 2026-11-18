"""인물 후보 판정 (person_info 규칙 정의서 H1~H4, M1~M6, N1~N8) 과 문서 안 후보 만들기 (H2·N6·M4)."""
import itertools

import pytest

from starter.person_candidates import build_doc_candidates, is_shared_number
from starter.person_resolve import PersonResolver, build_look_up, check_rows, is_given_of, new_evidence
from starter.roles import default_rules

RULES = default_rules()
N = "<PERSON_N>"


def tok(label, value):
    return f"<{label.upper()}_{value}>"


TOKEN_INFO = {}


def reg(label, value, normalized=None):
    t = tok(label, value)
    TOKEN_INFO[t] = {"label": label, "normalized": normalized or value}
    return t


def name(value, normalized):
    t = f"<PERSON_{value}>"
    TOKEN_INFO[t] = {"label": "person", "normalized": normalized}
    return t


KIM = name("N", "김민준")
PHONE_A, PHONE_B = reg("phone_number", "A", "01011111111"), reg("phone_number", "B", "01022222222")
ACCT_A = reg("account_number", "A", "110222")
MAIL_A = reg("email", "A", "a@x.kr")
RRN_1 = reg("resident_registration_number", "1", "9001011234567")     # 1990-01-01 남
RRN_2 = reg("resident_registration_number", "2", "8703151234567")
RRN_F = reg("resident_registration_number", "3", "9001012234567")     # 1990-01-01 여


def row(pid, doc, token=KIM, role="customer", info=(), chunks=None, status="pending"):
    return {"person_id": pid, "doc_id": doc, "token": token, "role": role, "info_label": list(info),
            "evidence": new_evidence(chunks if chunks is not None else [f"{doc}#0"]), "reg_id": None,
            "resolution_status": status, "created_at": None, "updated_at": None}


COUNTER = itertools.count(1)


def run(rows, registry=None, rejudge_hold=False, **kw):
    resolver = PersonResolver(rows, registry if registry is not None else [], TOKEN_INFO,
                              clock=lambda: "2026-11-17T00:00:00+00:00", id_factory=lambda p: f"{p}{next(COUNTER)}", **kw)
    out = resolver.resolve(rejudge_hold=rejudge_hold)
    check_rows(rows, out["registry"])
    return out


def by(rows):
    return {r["person_id"]: r for r in rows}


def level(r):
    return r["evidence"]["level"]


def nsa(r):
    """evidence.not_same_as {상대: 규칙}"""
    return r["evidence"]["not_same_as"]


# ----------------------------------------------------------------- 설계안 7장 시나리오
def test_design_example_four_documents():
    rows = [row("A", "H1", info=[PHONE_A]), row("B", "H2", role="employee", info=[PHONE_B]),
            row("C", "H3", info=[PHONE_A]), row("D", "H4")]
    out = run(rows)
    r = by(rows)
    # A·C: 이름 + 전화 1개(M1) → 같은 reg 에 연결, 행은 그대로 3개 + 1개
    assert r["A"]["reg_id"] == r["C"]["reg_id"] and r["A"]["reg_id"]
    assert [level(r["A"]), level(r["C"])] == ["mid", "mid"]
    assert r["A"]["evidence"]["rule"] == "M1" and r["A"]["resolution_status"] == "resolved"
    # B: 역할 그룹이 달라(N4) A·C 와 다른 사람. 등록하지 않고 not_same_as 에 서로 기록
    assert r["B"]["resolution_status"] == "unresolved" and r["B"]["reg_id"] is None and level(r["B"]) == "none"
    assert nsa(r["B"]) == {"A": "N4", "C": "N4", "D": "N4"} and nsa(r["A"]) == {"B": "N4"} and nsa(r["C"]) == {"B": "N4"}
    assert "not_same_as" not in r["B"]                                         # 칼럼이 아니라 evidence 안에만
    # D: 번호는 없지만 고객이라 직원 B 와는 N4(규칙 정의서 그대로). A·C 와는 근거가 없어 잇지 않음.
    #    (설계안 7.2 는 D 를 hold 로 적었지만 정의서의 N4 는 번호 유무와 무관하게 B 와 none 이다)
    assert r["D"]["resolution_status"] == "unresolved" and r["D"]["reg_id"] is None
    assert level(r["D"]) == "none" and nsa(r["D"]) == {"B": "N4"}
    assert len(out["registry"]) == 1
    # look_up: 분석이 끝난 후보 전부, chunk 단위
    assert sorted((x["chunk_id"], x["reg_id"]) for x in out["look_up"]) == \
        [("H1#0", r["A"]["reg_id"]), ("H2#0", None), ("H3#0", r["A"]["reg_id"]), ("H4#0", None)]


def test_pending_rows_are_not_in_look_up():
    rows = [row("A", "H1", status="resolved"), row("B", "H2")]
    rows[0]["reg_id"], rows[0]["evidence"]["level"] = "reg_x", "mid"
    assert [x["person_id"] for x in build_look_up(rows)] == ["A"]


# ----------------------------------------------------------------- high
def test_h1_same_rrn_links_even_when_name_and_role_differ():
    other = name("O", "김 민준 오타")
    rows = [row("A", "H1", info=[RRN_1]), row("B", "H2", token=other, role="employee", info=[RRN_1])]
    run(rows)
    r = by(rows)
    assert r["A"]["reg_id"] == r["B"]["reg_id"] and level(r["A"]) == "high" and r["A"]["evidence"]["rule"] == "H1"
    assert r["A"]["token"] != r["B"]["token"]                       # 행은 따로, reg_id 만 같게


def test_h4_two_kinds_of_identifiers_is_high():
    rows = [row("A", "H1", info=[PHONE_A, ACCT_A]), row("B", "H2", info=[PHONE_A, ACCT_A])]
    run(rows)
    assert level(by(rows)["B"]) == "high" and by(rows)["B"]["evidence"]["rule"] == "H4"


def test_h4_review_when_another_person_has_same_identifiers():
    rows = [row("A", "H1", info=[PHONE_A, ACCT_A]), row("B", "H2", info=[PHONE_A, ACCT_A]),
            row("C", "H3", info=[PHONE_A, ACCT_A])]
    rows[0]["resolution_status"] = rows[1]["resolution_status"] = "unresolved"      # A·B 는 서로 다른 사람으로 이미 저장됨
    for r in rows[:2]:
        r["evidence"]["level"] = "hold"
    out = run(rows)
    assert by(rows)["C"]["reg_id"] is None and any(x["rule"] == "H4" for x in out["review"])


# ----------------------------------------------------------------- mid · 사람 수 세기
def test_m1_counts_people_not_rows():
    """A·B 가 같은 reg 로 이어져 있으면 후보는 1명 → C 가 이어진다."""
    a, b = row("A", "H1", info=[PHONE_A]), row("B", "H2", info=[PHONE_A])
    c = row("C", "H3", info=[PHONE_A])
    out = run([a, b])
    run([a, b, c], registry=out["registry"])
    assert c["reg_id"] == a["reg_id"] == b["reg_id"]


def test_m1_two_different_people_is_hold_and_review():
    a, b = row("A", "H1", info=[PHONE_A], status="unresolved"), row("B", "H2", info=[PHONE_A], status="unresolved")
    c = row("C", "H3", info=[PHONE_A])
    out = run([a, b, c])
    assert c["reg_id"] is None and level(c) == "hold" and out["review"][0]["rule"] == "M1"


def test_m1_different_name_drops_shared_identifier():
    other = name("P", "박영희")
    a, b = row("A", "H1", info=[PHONE_A, ACCT_A]), row("B", "H2", token=other, info=[PHONE_A])
    run([a, b])
    assert a["info_label"] == [ACCT_A] and b["info_label"] == [] and a["reg_id"] is None


def test_none_with_existing_not_same_never_merges_and_n8_blocks():
    """A–B none 상태에서 B·C 가 M1 로 이어지면 A–C 도 none. 그 뒤 A·C 를 잇는 근거가 와도 잇지 않는다."""
    a = row("A", "H1", role="employee", info=[PHONE_A])                     # 직원
    b = row("B", "H2", role="customer", info=[PHONE_B])                     # 고객 → A 와 N4
    c = row("C", "H3", role="customer", info=[PHONE_B])
    out = run([a, b, c])
    assert b["reg_id"] == c["reg_id"] and nsa(a) == {"B": "N4", "C": "N4"} and nsa(c)["A"] == "N4"
    d = row("D", "H4", role="customer", info=[PHONE_A, ACCT_A])             # 전화 A 는 직원 A 와 같지만 역할이 달라 N4
    run([a, b, c, d], registry=out["registry"])
    assert d["reg_id"] is None or d["reg_id"] != a["reg_id"]
    assert a["reg_id"] is None                                                # 직원 A 는 어떤 고객과도 잇지 않음


def test_n8_blocks_h1_link_across_not_same_group():
    a = row("A", "H1", info=[RRN_1], status="unresolved")
    b = row("B", "H2", info=[RRN_1], status="unresolved")
    nsa(a)["B"], nsa(b)["A"] = "N4", "N4"
    a["evidence"]["level"] = b["evidence"]["level"] = "none"
    c = row("C", "H3", info=[RRN_1])
    out = run([a, b, c])        # C 의 주민번호가 서로 다른 사람인 A·B 와 모두 같음 → 어느 쪽인지 알 수 없어 잇지 않음
    assert c["reg_id"] is None and [x["rule"] for x in out["review"]] == ["N8"]


def test_m3_birth_and_gender_match_but_n2_differs():
    birth = reg("birth_date", "b", "19900101")
    male, female = reg("gender", "m", "M"), reg("gender", "f", "F")
    rows = [row("A", "H1", info=[RRN_1]), row("B", "H2", info=[birth, male])]
    run(rows)
    assert by(rows)["B"]["evidence"]["rule"] == "M3" and by(rows)["A"]["reg_id"] == by(rows)["B"]["reg_id"]
    rows = [row("A", "H1", info=[RRN_1]), row("B", "H2", info=[RRN_F])]               # 주민번호가 달라 N1
    run(rows)
    assert nsa(by(rows)["B"]) == {"A": "N1"}
    rows = [row("A", "H1", info=[RRN_1]), row("B", "H2", info=[birth, female])]       # 성별이 달라 N2
    run(rows)
    assert nsa(by(rows)["B"]) == {"A": "N2"}


def test_n1_different_rrn_is_none_even_with_same_phone():
    rows = [row("A", "H1", info=[RRN_1, PHONE_A]), row("B", "H2", info=[RRN_2, PHONE_A])]
    run(rows)
    r = by(rows)
    assert r["A"]["reg_id"] is None and r["B"]["reg_id"] is None and level(r["A"]) == "none"
    assert nsa(r["A"]) == {"B": "N1"}


# ----------------------------------------------------------------- 이름이 다른 같은 사람
SEON = name("S", "선영")
YUN = name("Y", "윤선영")
KIM_SEON = name("K", "김선영")


def test_m2_given_name_row_links_to_full_name_row_with_shared_phone():
    a, b = row("A", "H135", token=YUN, info=[PHONE_A]), row("B", "H201", token=SEON, info=[PHONE_A])
    run([a, b])
    assert a["reg_id"] == b["reg_id"] and b["evidence"]["rule"] == "M2" and level(b) == "mid"
    assert a["token"] != b["token"]


def test_m2_two_full_name_candidates_is_hold():
    a = row("A", "H1", token=YUN, info=[PHONE_A], status="unresolved")
    c = row("C", "H2", token=KIM_SEON, info=[PHONE_A], status="unresolved")
    b = row("B", "H3", token=SEON, info=[PHONE_A])
    out = run([a, c, b])
    assert b["reg_id"] is None and level(b) == "hold" and out["review"][0]["rule"] == "M2"


def test_m2_when_full_name_row_arrives_later():
    b, a = row("B", "H201", token=SEON, info=[PHONE_A], status="unresolved"), row("A", "H135", token=YUN, info=[PHONE_A])
    run([b, a])
    assert a["reg_id"] == b["reg_id"] and a["reg_id"]


def test_n7_role_conflict_marks_none_only_for_that_candidate():
    agent = row("B", "H2", token=SEON, role="agent", info=[PHONE_A])
    cust = row("A", "H1", token=YUN, role="customer", info=[PHONE_A])
    out = run([cust, agent])
    assert nsa(agent) == {"A": "N7"}
    assert agent["reg_id"] is None and level(agent) == "none"


def test_m6_staff_given_name_with_single_candidate_needs_switch():
    full = row("A", "H1", token=name("M", "김민준"), role="employee", status="unresolved")
    short = row("B", "H2", token=name("m", "민준"), role="agent")
    run([full, short])
    assert short["reg_id"] is None                                           # 스위치 꺼짐
    short2 = row("B", "H2", token=name("m", "민준"), role="agent")
    full2 = row("A", "H1", token=name("M", "김민준"), role="employee", status="unresolved")
    run([full2, short2], link_given=True)
    assert short2["reg_id"] == full2["reg_id"] and short2["evidence"]["rule"] == "M6"
    cust = row("B", "H2", token=name("m", "민준"), role="customer")          # 고객은 R8 → hold
    run([row("A", "H1", token=name("M", "김민준"), role="employee", status="unresolved"), cust], link_given=True)
    assert cust["reg_id"] is None


def test_m5_unique_same_name_needs_switch_and_known_role():
    a = row("A", "H1", info=[PHONE_A], status="unresolved")
    b = row("B", "H2")
    run([a, b])
    assert b["reg_id"] is None
    a = row("A", "H1", info=[PHONE_A], status="unresolved")
    b = row("B", "H2")
    run([a, b], link_unique=True)
    assert b["reg_id"] == a["reg_id"] and b["evidence"]["rule"] == "M5"
    u1, u2 = row("A", "H1", role="unknown", status="unresolved"), row("B", "H2", role="unknown")   # unknown 끼리는 충돌 확인 불가
    run([u1, u2], link_unique=True)
    assert u2["reg_id"] is None
    x, y, z = row("A", "H1", status="unresolved"), row("B", "H2", status="unresolved"), row("C", "H3")
    run([x, y, z], link_unique=True)                                          # 같은 이름이 2명이면 안 함
    assert z["reg_id"] is None


# ----------------------------------------------------------------- 약한 연결, registry 합치기
def two_groups():
    """C·D 는 H4 로 reg1, F·G 는 이름이 달라도 주민번호가 같아 H1 로 reg2."""
    rows = [row("C", "H3", info=[PHONE_A, ACCT_A]), row("D", "H4", info=[PHONE_A, ACCT_A]),
            row("F", "H6", token=name("O", "김 민준"), info=[RRN_1]), row("G", "H7", token=name("O", "김 민준"), info=[RRN_1])]
    out = run(rows)
    assert by(rows)["C"]["reg_id"] == by(rows)["D"]["reg_id"] != by(rows)["F"]["reg_id"] == by(rows)["G"]["reg_id"]
    return rows, out


def test_level_is_weakest_link_and_registries_merge_with_merged_into():
    rows, out = two_groups()
    h = row("H", "H8", info=[RRN_1, PHONE_A, ACCT_A])                        # H1(F·G, reg2) + H4(C·D, reg1)
    rows.append(h)
    out2 = run(rows, registry=out["registry"])
    regs = {r["reg_id"] for r in rows}
    assert len(regs) == 1 and None not in regs                                 # 두 reg 가 하나로
    merged = [x for x in out2["registry"] if x["merged_into"]]
    assert len(merged) == 1 and merged[0]["merged_into"] in regs and merged[0]["reg_id"] not in regs
    assert merged[0]["merged_into"] == out["registry"][0]["reg_id"]            # 먼저 등록된 쪽이 남음
    assert all(x["reg_id"] == merged[0]["merged_into"] for x in out2["look_up"])
    assert level(h) == "high" and level(by(rows)["C"]) == "high"


def test_registry_merge_is_blocked_by_not_same_as():
    rows, out = two_groups()
    c, f = by(rows)["C"], by(rows)["F"]
    nsa(c)["F"] = nsa(f)["C"] = "N4"                                              # C(reg1)–F(reg2) 는 다른 사람으로 확인됨
    h = row("H", "H8", info=[RRN_1, PHONE_A, ACCT_A])
    rows.append(h)
    out2 = run(rows, registry=out["registry"])
    assert any(x["rule"] == "N8" for x in out2["review"])
    assert not any(x["merged_into"] for x in out2["registry"])
    assert len({r["reg_id"] for r in rows}) == 2


def test_rejudge_hold_after_new_evidence():
    a = row("A", "H1", info=[PHONE_A])
    b = row("B", "H2")
    out = run([a, b])
    assert level(a) == "hold" and level(b) == "hold"
    b["info_label"] = [PHONE_A]
    PersonResolver([a, b], out["registry"], TOKEN_INFO, clock=lambda: "2026-11-17T00:00:00+00:00").resolve(rejudge_hold=True)
    assert a["reg_id"] == b["reg_id"] and a["reg_id"]


# ----------------------------------------------------------------- 문서 안 후보 (H2·N6·M4)
def make_doc(parts):
    """parts: 문자열 또는 (label, normalized) → (original, masked, records)"""
    original, masked, records = "", "", []
    for part in parts:
        if isinstance(part, str):
            original += part
            masked += part
            continue
        label, value = part
        start = len(original)
        original += value
        key = label.upper()
        token = f"<{key}_{value.encode().hex()}>"
        TOKEN_INFO[token] = {"label": label, "normalized": value}
        masked += token
        records.append({"label": label, "start": start, "end": start + len(value), "original": value,
                        "normalized": value, "token": token})
    return original, masked, records


def doc_rows(parts, doc_id="D1"):
    original, masked, records = make_doc(parts)
    counter = itertools.count(1)
    return build_doc_candidates(doc_id, original, masked, records, [masked], RULES,
                                id_factory=lambda p: f"{p}{next(counter)}", clock=lambda: "2026-11-17T00:00:00+00:00")


def test_h2_same_name_repeated_is_one_row_and_collects_clues_on_name_line():
    rows, links = doc_rows(["고객 ", ("person", "김민준"), " 문의 ", ("phone_number", "01011111111"),
                            "\n답변: ", ("person", "김민준"), " 고객님 확인"])
    assert len(rows) == 1 and rows[0]["doc_id"] == "D1" and rows[0]["role"] == "customer"
    assert len(rows[0]["info_label"]) == 1 and links == []
    assert rows[0]["resolution_status"] == "pending" and rows[0]["evidence"]["level"] == "hold"
    assert rows[0]["evidence"]["chunk_ids"] == ["D1#0"]
    ev = rows[0]["evidence"]
    assert ev["name_spans"] == [[3, 6], [26, 29]]                         # 두 언급 모두, 원문 위치만
    assert ev["clues"] == [{"token": rows[0]["info_label"][0], "span": [10, 21]}]


def test_n6_role_groups_differ_splits_rows():
    rows, _ = doc_rows(["상담원 ", ("person", "김민준"), "이 고객 ", ("person", "김민준"), "에게 안내함"])
    assert len(rows) == 2 and sorted(r["role"] for r in rows) == ["agent", "customer"]
    assert nsa(rows[0]) == {rows[1]["person_id"]: "N6"} and nsa(rows[1]) == {rows[0]["person_id"]: "N6"}


def test_n6_other_markers():
    rows, _ = doc_rows([("person", "김민준"), " 과장 보고\n", ("person", "김민준"), " 대리 확인"])
    assert len(rows) == 2                                                    # 직급이 다름
    rows, _ = doc_rows([("person", "김민준"), " A 입금\n", ("person", "김민준"), " B 입금"])
    assert len(rows) == 2                                                    # A/B 표기
    rows, _ = doc_rows([("person", "김민준"), "(동명이인 주의) 확인\n", ("person", "김민준"), " 확인"])
    assert len(rows) == 2
    rows, _ = doc_rows([("person", "김민준"), "이 ", ("person", "김민준"), "에게 전달"])
    assert len(rows) == 2                                                    # 서로 상대하는 관계
    rows, _ = doc_rows(["고객 ", ("person", "김민준"), " ", ("phone_number", "01011111111"), "\n고객 ",
                        ("person", "김민준"), " ", ("phone_number", "01022222222")])
    assert len(rows) == 2                                                    # 같은 줄 번호가 다름


def test_n6_different_clue_kinds_are_not_a_conflict():
    # 전화번호 줄과 카드번호 줄: 종류가 달라 비교할 수 없으므로 한 사람으로 묶고 단서를 모은다 (holdout H0127 유형)
    rows, _ = doc_rows(["피해 고객: ", ("person", "박현준"), " (", ("phone_number", "01004955746"), ")\n",
                        ("person", "박현준"), "님은 카드번호 ", ("card_number", "4092571177774123"), "를 알려줌"])
    assert len(rows) == 1 and len(rows[0]["info_label"]) == 2
    assert rows[0]["evidence"]["not_same_as"] == {}
    # 같은 종류가 양쪽에 있으면 하나라도 다르면 여전히 N6
    rows, _ = doc_rows(["고객 ", ("person", "김민준"), " ", ("phone_number", "01011111111"), " ",
                        ("card_number", "4092571177774123"), "\n고객 ", ("person", "김민준"), " ",
                        ("phone_number", "01022222222"), " ", ("card_number", "4092571177774123")])
    assert len(rows) == 2


def spans_of(rows):
    return [[tuple(s) for s in r["evidence"]["name_spans"]] for r in rows]


def test_h2_clue_mentions_cluster_first_and_ambiguous_neutral_mention_stays_apart():
    # 단서 없는 첫 언급이 묶음의 기준이 되면 뒤의 외부 담당자가 거기 붙어 사내 직원과 섞였다 (dev D0272 유형)
    rows, _ = doc_rows(["점검자: 정서현, ", ("person", "이서연"), "\n수탁사 담당자: ", ("person", "이서연"), " 책임 (",
                        ("phone_number", "01004005262"), ")\n문의는 ", ("person", "이서연"), " 과장에게"])
    assert len(rows) == 3                                                    # 첫 언급은 누구인지 모름 → 따로
    neutral, vendor, staff = rows                                           # 행은 문서 순서
    assert neutral["evidence"]["not_same_as"] == {} and neutral["info_label"] == []
    assert vendor["evidence"]["not_same_as"] == {staff["person_id"]: "N6"}  # 직급(책임 vs 과장)이 다름
    assert len(vendor["info_label"]) == 1 and staff["info_label"] == []


def test_h2_neutral_mention_joins_when_only_one_cluster_fits():
    rows, _ = doc_rows(["안내: ", ("person", "김민준"), " 확인\n고객 ", ("person", "김민준"), " ",
                        ("phone_number", "01011111111")])
    assert len(rows) == 1 and len(rows[0]["evidence"]["name_spans"]) == 2 and len(rows[0]["info_label"]) == 1


def test_n6_title_with_honorific_and_new_titles():
    # '책임님께'(외부)와 '과장이'(사내): '님'을 떼고 직급으로 본다 (dev D0210 유형)
    rows, _ = doc_rows([("person", "이서연"), " 책임님께,\n내용 생략\n저희 ", ("person", "이서연"), " 과장이 전달"])
    assert len(rows) == 2 and set(rows[0]["evidence"]["not_same_as"].values()) == {"N6"}
    rows, _ = doc_rows([("person", "김민준"), " 과장 보고\n", ("person", "김민준"), " 과장님 확인"])
    assert len(rows) == 1                                                    # 같은 직급 + '님'


def test_n5_internal_department_vs_external_organization():
    # 사내 부서 '정보보호팀' vs '수탁사' 담당자: 역할이 미상이어도 다른 사람 (dev D0062 유형)
    rows, _ = doc_rows(["정보보호팀 ", ("person", "이서연"), "입니다.\n수탁사 담당자: ", ("person", "이서연"), " (",
                        ("phone_number", "01004005262"), ")"])
    assert len(rows) == 2
    assert set(rows[0]["evidence"]["not_same_as"].values()) == {"N5"} and rows[0]["info_label"] == []
    rows, _ = doc_rows(["정보보호팀 ", ("person", "이서연"), " 대리 보고\n카드운영팀 ", ("person", "이서연"), " 대리 확인"])
    assert len(rows) == 2                                                    # 기존 N5 (둘 다 직원, 부서 다름)도 유지
    rows, _ = doc_rows(["정보보호팀 ", ("person", "이서연"), " 보고\n카드운영팀 협조: ", ("person", "이서연"), " 확인"])
    assert len(rows) == 1                                                    # 사내 부서끼리는 역할 미상이면 기존대로 묶음
    rows, _ = doc_rows(["수탁사 담당 ", ("person", "이서연"), " 연락\n수탁사 ", ("person", "이서연"), " 회신"])
    assert len(rows) == 1                                                    # 둘 다 외부: 충돌 아님


def test_m4_addressee_and_signer_are_not_linked():
    # 메일 '민준 씨,'(받는 사람)와 '조민준 드림'(보내는 사람) (dev D0100 유형)
    rows, links = doc_rows([("person", "민준"), " 씨, 안녕하세요.\n검토 부탁드립니다.\n\n", ("person", "조민준"), " 드림"])
    assert links == []
    short, full = rows
    assert short["evidence"]["not_same_as"] == {full["person_id"]: "N7"}
    rows, links = doc_rows(["담당 ", ("person", "조민준"), " 대리 안내\n", ("person", "민준"), " 대리가 재확인"])
    assert len(links) == 1                                                   # 일반 M4 는 그대로


def test_n6_same_name_addressee_and_signer_are_split():
    rows, _ = doc_rows([("person", "김민준"), " 님, 안녕하세요.\n요청하신 자료입니다.\n", ("person", "김민준"), " 드림"])
    assert len(rows) == 2 and set(rows[0]["evidence"]["not_same_as"].values()) == {"N6"}


def test_clues_only_from_same_line_with_single_person_and_not_shared_numbers():
    rows, _ = doc_rows(["고객 ", ("person", "김민준"), " ", ("phone_number", "15881234"),
                        "\n메모 ", ("phone_number", "01099999999")])
    assert rows[0]["info_label"] == []                                       # 대표번호·다른 줄 번호는 제외
    rows, _ = doc_rows([("person", "김민준"), " ", ("person", "박영희"), " ", ("phone_number", "01011112222")])
    assert all(r["info_label"] == [] for r in rows)                          # 줄에 사람이 2명
    assert is_shared_number("phone_number", "15881234") and not is_shared_number("phone_number", "01011112222")


def test_m4_links_in_document_but_does_not_create_registry():
    # 같은 문서 안 연결만으로는 확정 인물을 만들지 않는다 (연결 정보는 document_links 에 남김)
    rows, links = doc_rows(["상담원 ", ("person", "김민준"), "이 안내함\n", ("person", "민준"), " 상담원이 재확인함"])
    assert len(rows) == 2 and len(links) == 1 and links[0][2] == "M4"
    out = PersonResolver(rows, [], TOKEN_INFO, links=links, clock=lambda: "2026-11-17T00:00:00+00:00").resolve()
    assert rows[0]["reg_id"] is None and rows[1]["reg_id"] is None and out["registry"] == []
    assert {r["evidence"]["level"] for r in rows} == {"hold"}
    assert rows[1]["evidence"]["document_links"] == [rows[0]["person_id"]]
    check_rows(rows, out["registry"])


def m4_doc_with_phone(doc_id, phone="01011111111"):
    """'김민준(전화) … 민준' 문서: 성명 행과 이름만 행이 M4 로 이어진다."""
    original, masked, records = make_doc(["상담원 ", ("person", "김민준"), " 연락처 ", ("phone_number", phone),
                                          "\n", ("person", "민준"), " 상담원이 재확인함"])
    return build_doc_candidates(doc_id, original, masked, records, [masked], RULES,
                                id_factory=lambda p, c=itertools.count(1): f"{p}{doc_id}_{next(c)}",
                                clock=lambda: "2026-11-17T00:00:00+00:00")


def test_m4_partner_gets_registry_when_full_name_links_across_documents():
    rows1, links1 = m4_doc_with_phone("D1")
    rows2, links2 = m4_doc_with_phone("D2")
    full1, given1 = rows1
    full2, given2 = rows2
    out = PersonResolver(rows1 + rows2, [], TOKEN_INFO, links=links1 + links2,
                         clock=lambda: "2026-11-17T00:00:00+00:00").resolve()
    # 문서 간 근거(M1: 같은 전화)로 확정 인물 1명 → 두 문서의 '민준'도 같은 reg_id
    assert full1["reg_id"] and len(out["registry"]) == 1
    assert {r["reg_id"] for r in rows1 + rows2} == {full1["reg_id"]}
    check_rows(rows1 + rows2, out["registry"])


def test_m4_partner_gets_registry_in_incremental_load():
    # 1차 적재: 문서 1개뿐이라 등록 안 됨 → 2차 적재에서 같은 전화가 나오면 저장된 연결로 '민준'까지 등록
    from starter.person_ingest import persisted_links
    rows1, links1 = m4_doc_with_phone("D1")
    out1 = PersonResolver(rows1, [], TOKEN_INFO, links=links1, clock=lambda: "2026-11-17T00:00:00+00:00").resolve()
    assert out1["registry"] == [] and all(r["reg_id"] is None for r in rows1)
    rows2, links2 = m4_doc_with_phone("D2")
    out2 = PersonResolver(rows1 + rows2, out1["registry"], TOKEN_INFO, links=persisted_links(rows1) + links2,
                          clock=lambda: "2026-11-17T00:00:01+00:00").resolve()
    assert len(out2["registry"]) == 1 and {r["reg_id"] for r in rows1 + rows2} == {rows2[0]["reg_id"]}
    check_rows(rows1 + rows2, out2["registry"])


def test_m4_two_full_names_is_r3_hold_and_role_group_conflict_is_n7():
    rows, links = doc_rows(["고객 ", ("person", "김민준"), " 고객 ", ("person", "이민준"), "\n", ("person", "민준"), " 확인"])
    assert links == [] and len(rows) == 3
    rows, links = doc_rows(["고객 ", ("person", "김민준"), " 문의\n상담원 ", ("person", "민준"), " 확인"])
    assert links == [] and nsa(rows[0]) == {rows[1]["person_id"]: "N7"}


def test_is_given_of():
    assert is_given_of("선영", "윤선영") and is_given_of("민수", "남궁민수")
    assert not is_given_of("영", "윤선영") and not is_given_of("윤선영", "윤선영") and not is_given_of("JOHN", "KIM")


# ----------------------------------------------------------------- 정의서 보완: H3·N3, 약한 고리, name_diff, N5, N7(N1~N5), 서술 칸
EMP_1, EMP_2 = reg("employee_id", "E1", "S001"), reg("employee_id", "E2", "S002")


def test_h3_same_internal_id_and_n3_different_internal_id():
    rows = [row("A", "H1", role="employee", info=[EMP_1]), row("B", "H2", role="employee", info=[EMP_1])]
    run(rows)
    assert by(rows)["B"]["evidence"]["rule"] == "H3" and level(by(rows)["B"]) == "high"
    rows = [row("A", "H1", role="employee", info=[EMP_1]), row("B", "H2", role="employee", info=[EMP_2])]
    run(rows)
    assert nsa(by(rows)["B"]) == {"A": "N3"} and by(rows)["B"]["reg_id"] is None


def test_chain_level_is_weakest_link_for_whole_group():
    """A–B 는 high(H4), B–C 는 mid(M1) 로 이어지면 묶음 전체(A·B·C)가 mid (정의서 0장)."""
    acct2 = reg("account_number", "Z", "999")
    a = row("A", "H1", info=[PHONE_A, ACCT_A])
    b = row("B", "H2", info=[PHONE_A, ACCT_A, acct2])
    out = run([a, b])
    assert level(a) == level(b) == "high"
    c = row("C", "H3", info=[acct2])
    run([a, b, c], registry=out["registry"])
    assert a["reg_id"] == b["reg_id"] == c["reg_id"]
    assert [level(a), level(b), level(c)] == ["mid", "mid", "mid"]


def test_h1_name_diff_is_recorded_in_registry_reason():
    other = name("O", "김 민준 오타")
    rows = [row("A", "H1", info=[RRN_1]), row("B", "H2", token=other, info=[RRN_1])]
    out = run(rows)
    assert out["registry"][0]["reason"] == {"method": "rule", "reason_code": "H1", "name_diff": True}
    rows = [row("A", "H1", info=[RRN_1]), row("B", "H2", info=[RRN_1])]
    assert "name_diff" not in run(rows)["registry"][0]["reason"]


def test_n5_different_department_in_same_document_splits_rows():
    rows, _ = doc_rows(["인사팀 ", ("person", "김민준"), " 대리 보고\n영업부 ", ("person", "김민준"), " 대리 확인"])
    assert len(rows) == 2 and set(nsa(rows[0]).values()) == {"N5"}
    rows, _ = doc_rows(["인사팀 ", ("person", "김민준"), " 대리 보고\n인사팀 ", ("person", "김민준"), " 대리 확인"])
    assert len(rows) == 1                                                      # 같은 소속이면 한 사람 (H2)
    rows, _ = doc_rows([("person", "김민준"), " 고객 보고\n영업부 ", ("person", "김민준"), " 고객 확인"])
    assert len(rows) == 1                                                      # staff 가 아니면 N5 아님


def test_n7_in_document_uses_n1_to_n5_not_only_role_group():
    rrn_a, rrn_b = ("resident_registration_number", "9001011234567"), ("resident_registration_number", "8703151234567")
    rows, links = doc_rows(["고객 ", ("person", "김민준"), " ", rrn_a, "\n고객 ", ("person", "민준"), " ", rrn_b])
    assert links == [] and set(nsa(rows[0]).values()) == {"N7"}               # 같은 고객이어도 주민번호가 다르면(N1) N7
    rows, links = doc_rows(["고객 ", ("person", "김민준"), " ", rrn_a, "\n고객 ", ("person", "민준"), " ", rrn_a])
    assert len(links) == 1


def test_clue_in_narrative_cell_is_not_used():
    rows, _ = doc_rows(["고객 ", ("person", "김민준"), " / 메모: ", ("phone_number", "01011111111")])
    assert rows[0]["info_label"] == []
    rows, _ = doc_rows(["고객 ", ("person", "김민준"), " / 연락처: ", ("phone_number", "01011111111")])
    assert len(rows[0]["info_label"]) == 1
    rows, _ = doc_rows(["고객 ", ("person", "김민준"), " 접수 결과: ", ("phone_number", "01011111111")])
    assert rows[0]["info_label"] == []


def _look_up_fixture():
    ev = new_evidence(["D1#0"])
    row = {"person_id": "tmp_a", "doc_id": "D1", "token": N, "role": "customer", "info_label": [],
           "evidence": ev, "reg_id": None, "resolution_status": "unresolved"}
    lu = [{"chunk_id": "D1#0", "person_id": "tmp_a", "token": N, "reg_id": None, "role": "customer"}]
    return [row], lu, [{"chunk_id": "D1#0", "doc_id": "D1"}]


def test_check_look_up_ok():
    from starter.person_resolve import check_look_up
    check_look_up(*_look_up_fixture())


@pytest.mark.parametrize("mutate", [
    lambda rows, lu, ch: lu[0].update(role="employee"),          # 복사값 불일치
    lambda rows, lu, ch: lu[0].update(chunk_id="D9#0"),          # 없는 청크
    lambda rows, lu, ch: ch[0].update(doc_id="D2"),              # 문서 불일치
    lambda rows, lu, ch: rows[0].update(resolution_status="pending"),  # pending 이 look_up 에 있음
    lambda rows, lu, ch: lu.append(dict(lu[0])),                 # 중복
])
def test_check_look_up_rejects(mutate):
    from starter.person_resolve import check_look_up
    rows, lu, ch = _look_up_fixture()
    mutate(rows, lu, ch)
    with pytest.raises(ValueError):
        check_look_up(rows, lu, ch)


def test_check_rows_validates_tokens_against_token_map():
    phone = reg("phone_number", "01011112222")
    nm = name("chk1", "김확인")
    row = {"person_id": "tmp_x", "doc_id": "D1", "token": nm, "role": "customer", "info_label": [phone],
           "evidence": new_evidence(["D1#0"]), "reg_id": None, "resolution_status": "pending"}
    check_rows([row], token_info=TOKEN_INFO)
    with pytest.raises(ValueError):
        check_rows([dict(row, token="<PERSON_missing>")], token_info=TOKEN_INFO)
    with pytest.raises(ValueError):
        check_rows([dict(row, info_label=[nm])], token_info=TOKEN_INFO)      # 이름 토큰은 단서가 아님


def test_evidence_has_method_and_positions_only():
    ev = new_evidence(["D1#0"], name_spans=[(3, 6)], clues=[{"token": "<PHONE_NUMBER_a>", "span": [10, 23]}])
    assert ev["method"] == "rule" and ev["name_spans"] == [[3, 6]]
    assert ev["clues"] == [{"token": "<PHONE_NUMBER_a>", "span": [10, 23]}]


def test_rejudge_none_can_link_third_person_but_preserves_exclusion():
    a = row('A', 'A', role='employee')
    b = row('B', 'B')
    c = row('C', 'C', info=[PHONE_A])
    out = run([a, b, c])
    assert b['reg_id'] is None and level(b) == 'none'
    b['info_label'] = [PHONE_A]
    resolver = PersonResolver([a, b, c], out['registry'], TOKEN_INFO)
    result = resolver.resolve(rejudge_unresolved=True)
    assert b['reg_id'] == c['reg_id'] and b['reg_id']
    assert a['reg_id'] is None and 'A' in nsa(b) and 'A' in nsa(c)
    check_rows(result['rows'], result['registry'])


def test_document_links_survive_serialization_for_rejudge():
    import json
    from starter.person_ingest import persisted_links
    rows, links = m4_doc_with_phone('D1')
    other, other_links = m4_doc_with_phone('D2')
    restored = json.loads(json.dumps(rows + other))
    assert persisted_links(restored) == links + other_links
    result = PersonResolver(restored, token_info=TOKEN_INFO, links=persisted_links(restored)).resolve()
    assert len({r['reg_id'] for r in restored}) == 1 and restored[0]['reg_id']   # 저장된 연결로 '민준'까지 같은 reg_id
    check_rows(restored, result['registry'])


def test_n8_review_is_persisted_in_evidence():
    a, b = row('A', 'A', info=[RRN_1]), row('B', 'B', info=[RRN_1])
    nsa(a)['B'] = nsa(b)['A'] = 'N4'
    result = run([a, b])
    assert result['registry'] == []
    assert any(r['evidence'].get('review') for r in [a, b])


def test_created_time_allocation_survives_same_or_backwards_clock():
    from datetime import timedelta
    from starter.person_resolve import created_time, next_created_at
    previous = created_time('2099-01-01T00:00:00+00:00')
    assert next_created_at(previous, '2026-11-17T00:00:00+00:00') == previous + timedelta(microseconds=1)
    assert next_created_at(previous, previous) == previous + timedelta(microseconds=1)


def test_candidate_processing_uses_created_at_then_id_for_legacy_ties(monkeypatch):
    a, b, c = row('Z', 'Z'), row('A', 'A'), row('B', 'B')
    a['created_at'] = '2026-11-17T00:00:00+00:00'
    b['created_at'] = c['created_at'] = '2026-11-17T00:00:01+00:00'
    resolver = PersonResolver([c, b, a])
    seen = []
    monkeypatch.setattr(resolver, '_evaluate', lambda r: seen.append(r['person_id']))
    resolver.resolve()
    assert seen == ['Z', 'A', 'B']
