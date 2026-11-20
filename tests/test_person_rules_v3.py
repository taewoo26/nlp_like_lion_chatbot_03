"""인물사전 v3 판정 규칙 (PERSON_RULES=v3): 단서·역할 귀속 보정, M7·M8·M9, N9, 구성원 충돌, 추정 reg_id."""
import itertools

import pytest

from starter.person_resolve import PersonResolver, check_rows, mid_reg_default, rules_version
from tests.test_person_resolve import PHONE_A, RRN_1, RRN_2, TOKEN_INFO, by, doc_rows, level, mids, name, nsa, row

LEE = name("LEE3", "이지영")


def dept_row(pid, doc, dept=None, **kw):
    r = row(pid, doc, **kw)
    if dept:
        r["evidence"]["dept"] = dept
    return r


def run(rows, version="v3", name_only=None, links=()):
    counter = itertools.count(1)
    out = PersonResolver(rows, [], TOKEN_INFO, links=links, clock=lambda: "2026-11-20T00:00:00+00:00",
                         id_factory=lambda p: f"{p}{next(counter)}", version=version, name_only=name_only).resolve()
    check_rows(rows, out["registry"], mid_reg=version == "v3")
    return out


# ----------------------------------------------------------------- 설정
def test_rules_version(monkeypatch):
    monkeypatch.delenv("PERSON_RULES", raising=False)
    assert rules_version() == "v1" and not mid_reg_default()
    monkeypatch.setenv("PERSON_RULES", "v3")
    assert rules_version() == "v3" and mid_reg_default()
    monkeypatch.setenv("PERSON_RULES", "v2")
    with pytest.raises(ValueError):
        rules_version()


def test_v1_keeps_old_behavior_name_only_stays_hold():
    rows = [row("tmp_a", "D1", role="unknown"), row("tmp_b", "D2", role="unknown")]
    out = run(rows, version="v1")
    assert all(level(r) == "hold" and r["reg_id"] is None for r in rows) and out["registry"] == []


# ----------------------------------------------------------------- v3 판정
def test_m8_name_only_links_with_mid_reg_id():
    rows = [row("tmp_a", "D1", role="unknown"), row("tmp_b", "D2", role="unknown")]
    out = run(rows)
    a, b = rows
    assert a["reg_id"] and a["reg_id"] == b["reg_id"] and a["resolution_status"] == "resolved"
    assert level(a) == "mid" and a["evidence"]["rule"] == "M8"
    reason = out["registry"][0]["reason"]
    assert reason["confidence"] == "mid" and reason["path"] == "doc"
    with pytest.raises(ValueError):
        check_rows(rows, out["registry"], mid_reg=False)            # v1 기준에서는 resolved=high 만 허용


def test_name_only_off_argument_does_not_link():
    rows = [row("tmp_a", "D1", role="unknown"), row("tmp_b", "D2", role="unknown")]
    run(rows, name_only=False)                                         # 실험용 인자 (M7·M8 끔)
    assert all(r["reg_id"] is None for r in rows)


def test_m7_same_role_group():
    rows = [row("tmp_a", "D1", role="employee"), row("tmp_b", "D2", role="agent")]   # 둘 다 staff
    run(rows)
    assert rows[0]["reg_id"] == rows[1]["reg_id"] and rows[0]["evidence"]["rule"] == "M7"


def test_m9_same_dept_is_mid():
    rows = [dept_row("tmp_a", "D1", "정보보호팀", token=LEE, role="employee"),
            dept_row("tmp_b", "D2", "정보보호팀", token=LEE, role="employee")]
    run(rows)
    a, b = rows
    assert a["reg_id"] == b["reg_id"] and level(a) == "mid" and a["evidence"]["rule"] == "M9"


def test_m9_same_dept_two_people_goes_to_review():
    rows = [dept_row("tmp_a", "D1", "정보보호팀", token=LEE, role="employee", info=[RRN_1]),
            dept_row("tmp_b", "D2", "정보보호팀", token=LEE, role="employee", info=[RRN_2]),
            dept_row("tmp_c", "D3", "정보보호팀", token=LEE, role="employee")]
    out = run(rows)
    assert by(rows)["tmp_c"]["reg_id"] is None
    assert any(item["rule"] == "M9" and item["person_id"] == "tmp_c" for item in out["review"])


def test_m1_phone_is_mid_with_reg_id():
    rows = [row("tmp_a", "D1", info=[PHONE_A]), row("tmp_b", "D2", info=[PHONE_A])]
    run(rows)
    assert rows[0]["reg_id"] == rows[1]["reg_id"] and level(rows[0]) == "mid" and rows[0]["evidence"]["rule"] == "M1"


def test_confidence_rises_with_high_evidence():
    rows = [row("tmp_a", "D1", role="unknown"), row("tmp_b", "D2", role="unknown"),
            row("tmp_c", "D3", info=[RRN_1]), row("tmp_d", "D4", info=[RRN_1])]
    out = run(rows)
    live = [g for g in out["registry"] if not g.get("merged_into")]
    assert len({r["reg_id"] for r in rows}) == 1 and len(live) == 1
    assert live[0]["reason"]["confidence"] == "high" and level(by(rows)["tmp_d"]) == "high"


def test_dept_differs_blocks_weak_link_and_marks_namesake():
    rows = [dept_row("tmp_a", "D1", "정보보호팀", token=LEE, role="employee"),
            dept_row("tmp_b", "D2", "카드운영팀", token=LEE, role="employee"),
            row("tmp_c", "D3", token=LEE, role="unknown")]
    run(rows)
    assert all(r["reg_id"] is None and not mids(r) for r in rows)    # N9: 부서 충돌 이름은 M7·M8 안 씀


def test_n9_namesake_by_rrn_skips_name_only_links():
    rows = [row("tmp_a", "D1", info=[RRN_1]), row("tmp_b", "D2", info=[RRN_2]), row("tmp_c", "D3", role="unknown")]
    run(rows)
    assert by(rows)["tmp_c"]["reg_id"] is None and set(nsa(by(rows)["tmp_a"]).values()) == {"N1"}


def test_n9_customer_and_staff_namesake_is_order_independent_in_batch():
    # 고객(A)·이름만(B)·직원(C): 전체 행으로 N9 를 먼저 계산하므로 순서와 상관없이 B 는 어느 쪽에도 붙지 않는다
    for order in ("ABC", "CBA"):
        spec = {"A": ("D1", "customer"), "B": ("D2", "unknown"), "C": ("D3", "employee")}
        rows = [row(f"tmp_{k}", spec[k][0], role=spec[k][1]) for k in order]
        run(rows)
        assert all(r["reg_id"] is None for r in rows), order


def test_n4_still_applies():
    rows = [row("tmp_a", "D1", role="customer"), row("tmp_b", "D2", role="employee")]
    run(rows)
    assert set(nsa(rows[0]).values()) == {"N4"} and rows[0]["reg_id"] is None


def test_member_conflict_blocks_chain():
    # a(D1, 주민번호1)~b(D2, 같은 전화)가 이어진 뒤, c(D3, 같은 전화 + 주민번호2)는 묶음 구성원 a 와 N1 이라 잇지 않는다
    rows = [row("tmp_a", "D1", info=[RRN_1, PHONE_A]), row("tmp_b", "D2", info=[PHONE_A]),
            row("tmp_c", "D3", info=[RRN_2, PHONE_A])]
    run(rows)
    a, b, c = (by(rows)[p] for p in ("tmp_a", "tmp_b", "tmp_c"))
    assert a["reg_id"] and a["reg_id"] == b["reg_id"] and c["reg_id"] != a["reg_id"]


def test_registry_is_cross_document_only():
    rows = [row("tmp_a", "D1", role="unknown"), row("tmp_b", "D1", token=LEE, role="unknown")]
    out = run(rows, links=[("tmp_a", "tmp_b", "M4")])                # 같은 문서 안 연결만 있음
    assert out["registry"] == [] and all(r["reg_id"] is None and level(r) == "mid" for r in rows)


def test_in_doc_link_joins_cross_document_person():
    rows = [row("tmp_a", "D1", role="unknown"), row("tmp_b", "D1", token=LEE, role="unknown"),
            row("tmp_c", "D2", role="unknown")]
    run(rows, links=[("tmp_a", "tmp_b", "M4")])
    assert len({r["reg_id"] for r in rows}) == 1 and rows[0]["reg_id"]


# ----------------------------------------------------------------- 단서·역할 귀속 보정
@pytest.fixture
def v3(monkeypatch):
    monkeypatch.setenv("PERSON_RULES", "v3")


def test_candidates_store_dept_next_to_name():
    rows, _ = doc_rows(["정보보호팀 ", ("person", "이지영"), " 과장 보고"])
    assert rows[0]["evidence"]["dept"] == "정보보호팀"


def test_speaker_line_customer_word_is_not_speaker_role(v3):
    rows, _ = doc_rows([("person", "이소율"), ": 연체 고객 안내 문자 검토 요청"])
    assert rows[0]["role"] == "unknown"


def test_speaker_line_phone_is_not_attached_to_speaker(v3):
    rows, _ = doc_rows([("person", "김은영"), ": 그 고객 연락처 ", ("phone_number", "01055525661"), " 맞지?"])
    assert rows[0]["info_label"] == []


def test_speaker_line_old_behavior_in_v1(monkeypatch):
    monkeypatch.delenv("PERSON_RULES", raising=False)
    rows, _ = doc_rows([("person", "김은영"), ": 그 고객 연락처 ", ("phone_number", "01055525661"), " 맞지?"])
    assert rows[0]["info_label"] and rows[0]["role"] == "customer"


def test_ambiguous_field_label_gives_no_role(v3):
    rows, _ = doc_rows(["접수자: ", ("person", "김주원"), " / 접수일: 2026-06-04"])
    assert rows[0]["role"] == "unknown"


def test_subject_field_block_attaches_next_line_identifiers(v3):
    rows, _ = doc_rows(["신청인: ", ("person", "오현"), "\n주민등록번호: ", ("resident_registration_number", "8602172834901"),
                        "\n연락처: ", ("phone_number", "0275659163")])
    assert len(rows[0]["info_label"]) == 2


def test_block_does_not_attach_other_party_or_across_blank_line(v3):
    rows, _ = doc_rows(["신청인: ", ("person", "오현"), "\n받는 사람: ", ("email", "a@x.kr"),
                        "\n\n연락처: ", ("phone_number", "0275659163")])
    assert rows[0]["info_label"] == []


def test_identity_check_line_attaches_to_single_name_above(v3):
    rows, _ = doc_rows([("person", "정태오"), "님 신용카드 신규 발급 건 검토함.\n본인 확인: 휴대전화 ",
                        ("phone_number", "01092473932")])
    assert len(rows[0]["info_label"]) == 1
