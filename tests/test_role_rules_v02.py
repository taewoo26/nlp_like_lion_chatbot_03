"""메타데이터 규칙 정의서 v0.2 ROLE 규칙 회귀 시험 (가상 이름만 사용)."""
import pytest

from starter import pii_pipeline as pii
from starter.ingest import chunk
from starter.person_role_mapping import map_person_roles
from starter.person_roles import classify_mention
from starter.role_master import ROLE_CODES, RoleMaster, load_role_master


@pytest.fixture(autouse=True)
def synthetic_key(monkeypatch):
    monkeypatch.setattr(pii.config, "HMAC_KEY", "role-test-key-not-for-production")
    pii._key.cache_clear()
    yield
    pii._key.cache_clear()


def at(text, name, nth=0):
    start = -1
    for _ in range(nth + 1):
        start = text.index(name, start + 1)
    return classify_mention(text, start, start + len(name))


# ---------------------------------------------------------------- 마스터 (ROLE-01~07)
def test_master_has_six_roles_from_seed():
    master = load_role_master()
    assert set(master.entries) == set(ROLE_CODES)


def test_master_rejects_cue_registered_to_two_roles():
    rows = [("customer", "고객", [], ["담당"], [], 4, True),
            ("staff_contact", "담당자", [], ["담당"], [], 1, True),
            ("unknown", "미상", [], [], [], None, True)]
    with pytest.raises(ValueError, match="두 역할"):
        RoleMaster.from_rows(rows)


def test_master_rejects_unknown_role_code():
    with pytest.raises(ValueError, match="정의되지 않은"):
        RoleMaster.from_rows([("vip", "VIP", [], [], [], 1, True), ("unknown", "미상", [], [], [], None, True)])


def test_db_rows_are_used_instead_of_file():
    """ROLE-07: DB 마스터에 단서를 추가하면 코드 수정 없이 반영된다."""
    base = [(c, e.name_ko, list(e.query_cues), list(e.doc_cues), list(e.field_labels), e.priority, True)
            for c, e in load_role_master().entries.items()]
    rows = [(c, n, q, d + (["상담 매니저"] if c == "agent" else []), f, p, a) for c, n, q, d, f, p, a in base]

    class Cur:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def execute(self, sql): assert "rag.role_master" in sql
        def fetchall(self): return rows

    class Conn:
        def cursor(self): return Cur()

    master = load_role_master(Conn())
    text = "상담 매니저 홍길동 연결"
    s = text.index("홍길동")
    assert classify_mention(text, s, s + 3, master).role == "agent"
    assert classify_mention(text, s, s + 3).role == "unknown"      # 파일 사본에는 없는 단서


def test_inactive_role_is_ignored():
    rows = [("customer", "고객", [], ["고객"], [], 4, False), ("unknown", "미상", [], [], [], None, True)]
    master = RoleMaster.from_rows(rows)
    text = "고객 홍길동"
    assert classify_mention(text, 3, 6, master).role == "unknown"


# ---------------------------------------------------------------- 규칙별 (02_역할추출규칙 예시)
@pytest.mark.parametrize("text,name,role,rule", [
    ("상담사 지우: 네, 확인해 드릴게요", "지우", "agent", "ROLE-10"),                 # ROLE-10
    ("고객 김하늘: 카드가 안 돼요", "김하늘", "customer", "ROLE-10"),
    ("신청인: 홍길동", "홍길동", "customer", "ROLE-11"),                              # ROLE-11
    ("담당자: 김하늘", "김하늘", "staff_contact", "ROLE-11"),
    ("대리인: 이바다", "이바다", "guardian", "ROLE-11"),
    ("문의처: 김하늘 (내선 0000)", "김하늘", "staff_contact", "ROLE-11"),
    ("신청인: 홍길동 / 연락처: <PHONE>", "홍길동", "customer", "ROLE-11"),
    ("신청인: 홍길동, 연락처: <PHONE>", "홍길동", "customer", "ROLE-11"),
    ("신청인: 홍길동 고객님", "홍길동", "customer", "ROLE-11"),                       # 같은 역할 단서 2개
    ("고객 홍길동", "홍길동", "customer", "ROLE-12"),                                 # ROLE-12
    ("심사역 김하늘 검토 의견", "김하늘", "employee", "ROLE-12"),
    ("담당 직원 김하늘 배정", "김하늘", "employee", "ROLE-12"),
    ("직원 홍길동 처리", "홍길동", "employee", "ROLE-12"),
    ("홍길동 고객님께서 문의", "홍길동", "customer", "ROLE-13"),                      # ROLE-13
    ("김하늘 대리가 처리했습니다", "김하늘", "employee", "ROLE-13"),
    ("김하늘 대리님 확인 부탁드립니다", "김하늘", "employee", "ROLE-13"),
    ("이바다 대리인이 서류 제출", "이바다", "guardian", "ROLE-13"),
    ("고객 홍길동 대리", "홍길동", "unknown", "ROLE-16"),                             # ROLE-16 (TST-17)
    ("신청인: 홍길동 대리", "홍길동", "unknown", "ROLE-16"),
])
def test_rule_examples(text, name, role, rule):
    assert at(text, name) == (role, "rule", rule)


def test_role14_signature_at_document_end():                                         # TST-25-N
    text = "안내드립니다.\n변경 사항은 아래와 같습니다.\n\n인사팀 하람 드림"
    assert at(text, "하람") == ("staff_contact", "rule", "ROLE-14")


def test_role14_not_applied_in_middle_of_document():
    text = "인사팀 하람 드림\n" + "\n".join(f"본문 {i}" for i in range(5))
    assert at(text, "하람").role == "unknown"


def test_role14_is_fallback_when_title_exists():
    text = "본문\n\n리스크관리팀 김하늘 과장 드림"
    assert at(text, "김하늘") == ("employee", "rule", "ROLE-13")


def test_role17_other_line_cue_is_not_used():                                        # TST-24-N
    text = "상담 직원 안내\n홍길동 님 문의"
    assert at(text, "홍길동") == ("unknown", "default", None)


def test_speaker_label_without_name_is_not_a_field():
    """'상담사:' 뒤의 호명은 상담사가 아니다. 이어지는 문장은 항목 값이 아님 (ROLE-11 값 판정)."""
    text = "상담사: 홍길동 고객님, 확인하겠습니다."
    assert at(text, "홍길동") == ("customer", "rule", "ROLE-13")
    assert at("상담사: 홍길동 님 안녕하세요", "홍길동").role == "unknown"


def test_cue_inside_longer_word_is_not_used():
    assert at("홍길동이 고객센터 시스템을 점검했다.", "홍길동").role == "unknown"
    assert at("비고객 홍길동", "홍길동").role == "unknown"


def test_role12_allows_at_most_one_space():
    assert at("고객  홍길동", "홍길동").role == "unknown"
    assert at("고객홍길동", "홍길동").role == "customer"


# ---------------------------------------------------------------- 한 문서에 고객·직원 (요구사항)
def test_customer_and_employee_tokens_get_their_own_roles():
    text = ("[VOC] 접수\n고객명: 홍길동\n상담사 김하늘: 불편을 드려 죄송합니다\n"
            "고객 홍길동: 결제 알림이 안 와요\n처리 담당: 이바다\n심사역 박도윤 검토\n\n인사팀 서준 드림")
    names = ["홍길동", "김하늘", "홍길동", "이바다", "박도윤", "서준"]
    pos, cur = [], 0
    for n in names:
        s = text.index(n, cur); pos.append({"label": "person", "start": s, "end": s + len(n)}); cur = s + len(n)
    masked, records = pii.pseudonymize(text, pos)
    rows = map_person_roles("D9", text, masked, records, chunk(masked))
    by_token = {}
    for r in rows:
        by_token.setdefault(r["person_token"], set()).add(r["role"])
    tok = {n: pii.make_token("person", n) for n in set(names)}
    assert by_token[tok["홍길동"]] == {"customer"}
    assert by_token[tok["김하늘"]] == {"agent"}
    assert by_token[tok["박도윤"]] == {"employee"}
    assert by_token[tok["서준"]] == {"staff_contact"}
    assert by_token[tok["이바다"]] == {"staff_contact"}      # '처리 담당:' 안의 항목명 '담당:'
    assert [r["rule_id"] for r in rows] == ["ROLE-11", "ROLE-10", "ROLE-10", "ROLE-11", "ROLE-12", "ROLE-14"]
    assert all(r["source"] in ("rule", "default") for r in rows)
