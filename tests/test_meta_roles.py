"""역할 메타데이터 규칙 (규칙 정의서 ROLE-10~18, 엑셀 07 시트)."""
import json

import pytest

from starter import pii_pipeline as pii
from starter.ingest import chunk
from starter.person_role_mapping import persons_by_chunk
from starter.roles import DEFAULT_ROWS, default_rules, query_roles, role_for_mention, rules_from_rows

RULES = default_rules()


def spans_for(text, names):
    out, cursor = [], 0
    for name in names:
        start = text.index(name, cursor)
        out.append({"label": "person", "start": start, "end": start + len(name)})
        cursor = start + len(name)
    return out


def roles_of(text, names):
    spans = spans_for(text, names)
    result = []
    for s in spans:
        others = [o for o in spans if o is not s]
        result.append(role_for_mention(text, s["start"], s["end"], RULES, others))
    return result


@pytest.mark.parametrize("text,names,expected", [
    ("상담사 지우: 네, 확인해 드릴게요", ["지우"], [("agent", "ROLE-10")]),                    # TST-26
    ("신청인: 홍길동", ["홍길동"], [("customer", "ROLE-11")]),
    ("1. 처리 담당: 김하늘", ["김하늘"], [("staff_contact", "ROLE-11")]),
    ("대리인: 이바다", ["이바다"], [("guardian", "ROLE-11")]),
    ("고객 홍길동 님이 문의", ["홍길동"], [("customer", "ROLE-12")]),
    ("심사역 김하늘 검토", ["김하늘"], [("employee", "ROLE-12")]),
    ("홍길동 고객님께서 신고", ["홍길동"], [("customer", "ROLE-13")]),
    ("김하늘 대리가 처리", ["김하늘"], [("employee", "ROLE-13")]),
    ("인사팀 하람 드림", ["하람"], [("employee", "ROLE-14")]),                                 # 오늘 결정
    ("하람 드림", ["하람"], [("unknown", None)]),                                               # 팀명 없음
    ("고객 담당 김하늘 대리", ["김하늘"], [("employee", "ROLE-13")]),                         # 직급(2) > 호칭(3)
    ("고객 홍길동 상담사", ["홍길동"], [("unknown", "ROLE-16")]),                              # 같은 순위 충돌
    ("홍길동 건으로 문의", ["홍길동"], [("unknown", None)]),
    ("[부고] 고객센터 김민준 대리 장인상", ["김민준"], [("employee", "ROLE-13")]),          # '고객센터'는 단서 아님
    ("상담사: 김민준 고객님, 본인 확인 부탁드립니다", ["김민준"], [("customer", "ROLE-13")]),  # '상담사:'는 화자
    ("오지훈 대리가 고객 김하늘에게 안내", ["오지훈", "김하늘"],
     [("employee", "ROLE-13"), ("customer", "ROLE-12")]),                                     # 다른 이름 넘지 않음
    ("IT운영팀 소속 박도윤이 고객 김동현의 주민등록번호", ["박도윤", "김동현"],
     [("unknown", None), ("customer", "ROLE-12")]),                                           # 조사로 끝난 이름
])
def test_role_rules(text, names, expected):
    assert roles_of(text, names) == expected


def test_other_line_cue_not_used():                                                           # TST-24-N
    assert roles_of("상담 직원 안내\n홍길동 님 문의", ["홍길동"]) == [("unknown", None)]


def test_window_is_three_words():
    assert roles_of("고객 정말 아주 매우 홍길동", ["홍길동"]) == [("unknown", None)]
    assert roles_of("고객 아주 매우 홍길동", ["홍길동"]) == [("customer", "ROLE-12")]


def test_query_roles_expand_staff_group():                                                    # ROLE-45·46
    assert query_roles("직원 김민준 관련 문서", RULES) == {"employee", "agent", "staff_contact"}
    assert query_roles("상담사 홍길동", RULES) == {"agent"}
    assert query_roles("고객들 문서 다 보여줘", RULES) == {"customer"}
    assert query_roles("보이스피싱 피해 접수", RULES) == set()


def test_duplicate_cue_rejected():
    rows = [list(r) for r in DEFAULT_ROWS]
    rows[0][3] = rows[0][3] + ["대리"]
    with pytest.raises(ValueError):
        rules_from_rows(rows)


@pytest.fixture
def synthetic_key(monkeypatch):
    monkeypatch.setattr(pii.config, "HMAC_KEY", "role-test-key-not-for-production")
    pii._key.cache_clear()
    yield
    pii._key.cache_clear()


def test_persons_by_chunk_uses_document_mention_order(synthetic_key):
    text = "상담사 지우: 안녕하세요\n" + "가" * 30 + "\n고객 김하늘 문의\n김하늘 고객님 확인"
    spans = spans_for(text, ["지우", "김하늘", "김하늘"])
    masked, records = pii.pseudonymize(text, spans)
    parts = chunk(masked, max_chars=40)
    assert len(parts) > 1
    persons = persons_by_chunk("D1", text, masked, records, parts, RULES, spans)
    flat = [p for cid in sorted(persons) for p in persons[cid]]
    assert [p["mention"] for p in flat] == [0, 1, 2]
    assert [p["role"] for p in flat] == ["agent", "customer", "customer"]
    assert flat[1]["token"] == flat[2]["token"]
    assert all(set(p) == {"token", "role", "mention", "source", "rule_id"} for p in flat)
    json.dumps(persons, ensure_ascii=False)                                                   # JSONB 로 저장 가능
