"""역할 메타데이터: 문서 속 이름 언급마다 역할을 정한다 (규칙 정의서 ROLE-10~18).

역할 6종과 단서는 rag.role_master 가 원본이다 (ROLE-07). 적재·검색 시작 때 load_rules(cur)로
한 번 읽는다. DB 없이 쓰는 시험·비상용으로 같은 내용을 DEFAULT_ROWS 에 둔다
(infra/init/02_metadata.sql 의 INSERT 와 같이 고칠 것).

판정 순서 (숫자가 작을수록 강함, 같은 순위끼리 다르면 unknown)
  1  화자 표기 '상담사 지우:' (ROLE-10), 항목명 '신청인: 홍길동' (ROLE-11)
  2  직급 '홍길동 대리'·'심사역 홍길동' (ROLE-12·13), 서명 '인사팀 하람 드림' (ROLE-14)
  3  호칭 '홍길동 고객님'·'고객 홍길동'·'상담사 홍길동' (ROLE-12·13)
단서는 같은 줄 안, 이름 앞뒤 3어절 안에서만 찾고 다른 이름을 넘어가지 않는다 (ROLE-17).
"""
import re
from dataclasses import dataclass, field

ROLES = ("customer", "employee", "agent", "staff_contact", "guardian", "unknown")

# (role_code, role_group, query_cues, doc_cues, field_labels) — 02_metadata.sql 과 같은 내용
DEFAULT_ROWS = [
    ("customer", "customer", ["고객", "회원", "민원인"],
     ["고객", "고객님", "회원", "회원님", "민원인", "가입자"],
     ["신청인", "고객명", "가입자", "민원인", "접수자", "예금주", "명의자", "피해 고객", "대상 고객"]),
    ("employee", "staff", ["직원", "행원", "임직원"],
     ["사원", "주임", "대리", "과장", "차장", "부장", "팀장", "실장", "지점장", "심사역"], []),
    ("agent", "staff", ["상담사", "상담원"], ["상담사", "상담원"], []),
    ("staff_contact", "staff", ["담당자"], [], ["담당자", "문의처", "처리 담당", "연락 담당자", "작성자"]),
    ("guardian", "related", ["대리인", "보호자"], ["대리인", "보호자"], ["대리인", "보호자", "수임인", "법정대리인"]),
    ("unknown", "unknown", [], [], []),
]

TITLE_PRIORITY = 2       # 직급·서명
HONORIFIC_PRIORITY = 3   # 고객님·고객 ○○·상담사 ○○
WINDOW = 3               # 이름 앞뒤 어절 수

_JOSA = ("께서", "에게", "한테", "으로", "께", "은", "는", "이", "가", "을", "를", "의", "도", "만",
         "와", "과", "로", "에", "들")
_PUNCT = " \t,.;!?·()[]{}<>\"'|/~-"
_TEAM_END = re.compile(r"[가-힣A-Za-z]+(?:팀|부|실|센터|본부|지점)\s*$")
_SIGN_AFTER = re.compile(r"\s*(?:드림|올림)\s*$")
_FIELD_BEFORE = re.compile(r"([가-힣]+(?: [가-힣]+)?)\s*[:：]\s*$")
_NAME_JOSA_AFTER = re.compile(r"(?:께서|에게|한테|으로|께|은|는|이|가|을|를|의|도|만|와|과|로|에)(?![가-힣])")


@dataclass
class RoleRules:
    field_to_role: dict            # '신청인' → 'customer'
    cue_to_role: dict              # '대리' → 'employee'
    query_to_roles: dict           # '직원' → {'employee','agent','staff_contact'}
    role_group: dict = field(default_factory=dict)   # 'agent' → 'staff'


def rules_from_rows(rows):
    """(role_code, role_group, query_cues, doc_cues, field_labels) 목록 → RoleRules."""
    groups, members = {}, {}
    for code, group, *_ in rows:
        if code not in ROLES:
            raise ValueError(f"알 수 없는 역할 코드: {code}")
        groups[code] = group
        members.setdefault(group, set()).add(code)
    field_map, cue_map, query_map = {}, {}, {}
    for code, group, q_cues, d_cues, labels in rows:
        for target, words in ((field_map, labels), (cue_map, d_cues)):
            for w in words:
                if w in target and target[w] != code:
                    raise ValueError(f"단서 '{w}'가 두 역할({target[w]}, {code})에 등록됨")
                target[w] = code
        for w in q_cues:
            # '직원'은 사내 인력 전체(staff 그룹)로 넓힌다 (ROLE-46). 나머지는 그 역할만.
            query_map[w] = set(members[group]) if code == "employee" else {code}
    return RoleRules(field_map, cue_map, query_map, groups)


def load_rules(cur):
    cur.execute("""SELECT role_code, role_group, query_cues, doc_cues, field_labels
                   FROM rag.role_master WHERE active ORDER BY role_code""")
    return rules_from_rows(cur.fetchall())


def default_rules():
    return rules_from_rows(DEFAULT_ROWS)


def _candidates(word):
    """'대리가,' → ['대리가', '대리'] : 앞뒤 문장부호를 떼고, 조사를 뗀 형태도 후보로."""
    w = word.strip(_PUNCT)
    out = [w] if w else []
    for josa in _JOSA:
        if w.endswith(josa) and len(w) > len(josa):
            out.append(w[: -len(josa)])
    return out


def _cue(word, rules, allow_josa=True):
    if word.rstrip().endswith((":", "：")):      # '상담사:' 는 화자·항목명이지 호칭이 아님
        return None
    for c in (_candidates(word) if allow_josa else [word.strip(_PUNCT)]):
        if c in rules.cue_to_role:
            return rules.cue_to_role[c]
    return None


def _priority(role):
    return TITLE_PRIORITY if role == "employee" else HONORIFIC_PRIORITY


def role_for_mention(text, start, end, rules, other_spans=()):
    """text[start:end] 이름 언급의 역할 → (role, rule_id). 단서가 없으면 ('unknown', None)."""
    line_start = text.rfind("\n", 0, start) + 1
    line_end = text.find("\n", end)
    line_end = len(text) if line_end < 0 else line_end
    # 같은 줄의 다른 이름을 넘어가지 않도록 창을 자른다
    left = max([s["end"] for s in other_spans if line_start <= s["end"] <= start], default=line_start)
    right = min([s["start"] for s in other_spans if end <= s["start"] <= line_end], default=line_end)
    before, after = text[left:start], text[end:right]
    full_before = text[line_start:start]

    hits = []   # (priority, role, rule_id)
    # ROLE-10 화자 표기: 줄 맨 앞 '상담사 지우:'
    if left == line_start and re.match(r"\s*[:：]", after):
        role = rules.cue_to_role.get(full_before.strip())
        if role:
            hits.append((1, role, "ROLE-10"))
    # ROLE-11 항목명: '신청인: 홍길동', '1. 처리 담당: 홍길동'
    m = _FIELD_BEFORE.search(before)
    if m:
        label = m.group(1)
        role = rules.field_to_role.get(label) or rules.field_to_role.get(label.split()[-1])
        if role:
            hits.append((1, role, "ROLE-11"))
    # ROLE-12 이름 앞 / ROLE-13 이름 뒤: 3어절 안에서 가장 가까운 단서 하나씩
    if not any(rid == "ROLE-10" for _, _, rid in hits):
        # 이름 앞 단서는 조사 없이 맨 단어일 때만 ('직원이 홍길동 고객 …'의 '직원이'는 다른 사람)
        for word in reversed(before.split()[-WINDOW:]):
            role = _cue(word, rules, allow_josa=False)
            if role:
                hits.append((_priority(role), role, "ROLE-12"))
                break
    # 이름 바로 뒤에 조사가 붙으면('박도윤이 고객 김동현') 그 이름의 말은 끝난 것이라 뒤 단서를 보지 않는다
    if not _NAME_JOSA_AFTER.match(after):
        for word in after.split()[:WINDOW]:
            role = _cue(word, rules)
            if role:
                hits.append((_priority(role), role, "ROLE-13"))
                break
    # ROLE-14 서명: '인사팀 하람 드림' (팀명이 있어야 함, 없으면 unknown)
    if _TEAM_END.search(before) and _SIGN_AFTER.match(after):
        hits.append((TITLE_PRIORITY, "employee", "ROLE-14"))

    if not hits:
        return "unknown", None
    best = min(p for p, _, _ in hits)
    top = {r for p, r, _ in hits if p == best}
    if len(top) > 1:
        return "unknown", "ROLE-16"          # 같은 순위끼리 충돌
    role = top.pop()
    return role, next(rid for p, r, rid in hits if p == best and r == role)


def query_roles(query, rules):
    """질문 속 역할 단서 → 일치로 볼 역할 집합 (ROLE-45·46). 없으면 빈 집합."""
    wanted = set()
    for word in query.split():
        for c in _candidates(word):
            if c in rules.query_to_roles:
                wanted |= rules.query_to_roles[c]
                break
    return wanted


_TOKEN = re.compile(r"<[A-Z_]+_[0-9A-Za-z]+>")


def _query_rules(rules):
    """질문용 규칙: 문서 단서(대리·심사역 …)에 질문 단서(직원·행원 …)를 더한다."""
    cue = dict(rules.cue_to_role)
    for word, roles in rules.query_to_roles.items():
        cue.setdefault(word, "employee" if len(roles) > 1 else next(iter(roles)))
    return RoleRules(rules.field_to_role, cue, rules.query_to_roles, rules.role_group)


def wanted_roles(role, rules):
    """판정된 역할 → 일치로 볼 역할 집합. 직원은 사내 인력 전체(staff 그룹)로 넓힌다 (ROLE-46)."""
    if role == "unknown":
        return set()
    if role == "employee":
        group = rules.role_group.get("employee")
        return {r for r, g in rules.role_group.items() if g == group}
    return {role}


def query_name_roles(masked_query, token, rules):
    """가명 질문에서 그 사람 토큰에 '직접 붙은' 역할어만 본다 (문서와 같은 3어절 규칙).

    '직원 김민준 문서' → 직원 / '김소율 심사역 통해서 한도 올라간 고객 안내문' → 직원
    ('고객'은 김소율이 아니라 안내문 받는 사람). 붙은 역할어가 없으면 빈 집합 = 역할 조건 없음.
    """
    q_rules = _query_rules(rules)
    spans = [{"start": m.start(), "end": m.end()} for m in _TOKEN.finditer(masked_query)]
    found = set()
    for s in spans:
        if masked_query[s["start"]:s["end"]] != token:
            continue
        others = [o for o in spans if o is not s]
        role, _ = role_for_mention(masked_query, s["start"], s["end"], q_rules, others)
        if role != "unknown":
            found.add(role)
    return wanted_roles(found.pop(), rules) if len(found) == 1 else set()
