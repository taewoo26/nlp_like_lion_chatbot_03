"""검색 단계 역할 soft 3단계 (ROLE-41~44)."""
from starter.roles import default_rules
from starter.search import role_grade

GROUPS = default_rules().role_group
STAFF = {"employee", "agent", "staff_contact"}


def p(role, source="rule"):
    return {"token": "<PERSON_aaaaaaaaaaaa>", "role": role, "source": source}


def test_no_role_condition_keeps_everything():
    assert role_grade([p("customer")], set(), GROUPS) == 1


def test_match_is_first():                                                     # ROLE-41, ROLE-44
    assert role_grade([p("customer"), p("unknown", "default")], {"customer"}, GROUPS) == 1


def test_unknown_is_second():                                                  # ROLE-42, TST-21
    assert role_grade([p("unknown", "default")], {"customer"}, GROUPS) == 2
    assert role_grade([], {"customer"}, GROUPS) == 2


def test_rule_contradiction_is_excluded():                                     # ROLE-43, TST-20
    assert role_grade([p("customer")], STAFF, GROUPS) == 3


def test_llm_role_alone_never_excludes():
    assert role_grade([p("customer", "llm")], STAFF, GROUPS) == 2


def test_same_group_is_not_excluded():                                         # '상담사' 질문 ↔ 직원 문서
    assert role_grade([p("employee")], {"agent"}, GROUPS) == 2
    assert role_grade([p("agent")], STAFF, GROUPS) == 1                        # TST-36-N


from starter.roles import query_name_roles

RULES = default_rules()
T = "<PERSON_aaaaaaaaaaaa>"


def test_query_role_must_be_attached_to_the_name():
    assert query_name_roles(f"직원 {T} 관련 문서만 찾아주세요", T, RULES) == STAFF          # Q053
    assert query_name_roles(f"고객 {T} 건만 보여줘", T, RULES) == {"customer"}             # Q052
    assert query_name_roles(f"{T} 직원이 처리한 보이스피싱 피해 접수 건", T, RULES) == STAFF  # Q016
    assert query_name_roles(f"{T} 고객 한도 상향 심사 어떻게 됐어?", T, RULES) == {"customer"}
    # '고객'은 김소율이 아니라 안내문 받는 사람 → 붙은 단서 '심사역'만 본다 (Q062)
    assert query_name_roles(f"{T} 심사역 통해서 한도 올라간 고객 안내문 있어?", T, RULES) == STAFF
    assert query_name_roles(f"{T} 관련 문서 보여줘", T, RULES) == set()
    assert query_name_roles(f"{T}이 고객에게 보낸 안내문", T, RULES) == set()               # 조사로 끝난 이름
    # '직원이'는 조사가 붙은 주어라 뒤 이름을 꾸미지 않는다 → 이름 뒤 '고객'만 본다 (Q077)
    assert query_name_roles(f"여신심사팀 직원이 {T} 고객 정보를 메신저로 보내서", T, RULES) == {"customer"}
