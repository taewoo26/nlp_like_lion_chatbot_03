"""PERSON 언급마다 같은 줄의 단서로 역할을 정한다 (메타데이터 규칙 정의서 v0.2 ROLE-10~18).

classify_mention(text, start, end) -> RoleResult(role, source, rule_id)
role_for_mention(text, start, end)  -> role   (기존 호출 호환)

단서는 rag.role_master(또는 그 사본)에서 읽는다 (ROLE-07, role_master.py).
모든 판단은 이름과 같은 줄에서만 한다 (ROLE-17).

  ROLE-10 화자 표기   줄 맨 앞 '역할어 이름:'                      예) 상담사 지우: 네
  ROLE-11 항목명      '항목명: 이름' 에서 이름이 그 항목의 값일 때   예) 신청인: 홍길동
          값 판정: 이름 뒤에 (호칭·직급 하나를 빼고) 아무것도 없거나, 구분자(/ | ( [ 탭)나
          다음 항목('…, 연락처:')만 오면 값이다. '상담사: 홍길동 고객님, 확인하겠습니다'처럼
          문장이 이어지면 화자 표기 뒤의 호명이므로 항목명 단서로 쓰지 않는다.
  ROLE-12 이름 바로 앞 역할어 (공백 0~1칸)                         예) 고객 홍길동
  ROLE-13 이름 바로 뒤 호칭·직급 (공백 0~1칸, '님'·조사 허용)        예) 김하늘 대리가
  ROLE-16 위 단서들이 서로 다른 역할을 가리키면 unknown            예) 고객 홍길동 대리
  ROLE-14 서명 (초안) 위 단서가 하나도 없을 때만, 문서 끝 3줄 안의 '[부서] 이름 드림/올림' → staff_contact
  ROLE-15 문서 유형 기본값 (초안) 적용 범위가 미정이라 구현하지 않음
  ROLE-18 '김 대리' 같은 성+직급은 탐지 대상이 아니므로 여기 들어오지 않는다

같은 역할을 가리키는 단서가 여러 개면 위 순서(10 → 11 → 12 → 13)에서 먼저 나온 규칙 ID를 남긴다.
"""
import re
from collections import namedtuple

from .role_master import load_role_master

RoleResult = namedtuple("RoleResult", "role source rule_id")
UNKNOWN = RoleResult("unknown", "default", None)

_JOSA = r"(?:은|는|이|가|을|를|의|에게|께서|께|도|만|과|와|으로|로)"
_PATTERNS = {}


def _alt(words):
    if not words:
        return "(?!)"          # 단서가 하나도 없으면 아무것도 맞지 않게
    return "|".join(re.escape(w).replace(r"\ ", r"[ \t]+")
                    for w in sorted(words, key=lambda x: (-len(x), x)))


def _patterns(master):
    key = id(master)
    if key not in _PATTERNS:
        cues, fields = _alt(master.cue_to_role), _alt(master.field_to_role)
        after = rf"[ \t]?(?P<cue>{cues})(?:님)?(?=$|[^가-힣]|{_JOSA}(?![가-힣]))"
        _PATTERNS[key] = {
            "speaker": re.compile(rf"^[ \t]*(?P<cue>{cues})[ \t]?$"),
            "before": re.compile(rf"(?<![가-힣])(?P<cue>{cues})[ \t]?$"),
            "field": re.compile(rf"(?<![가-힣])(?P<field>{fields})[ \t]*[:：][ \t]*$"),
            "after": re.compile(after),
            "after_strip": re.compile(rf"^(?:{after}{_JOSA}?)?(?:님)?"),
            "_master": master,
        }
    return _PATTERNS[key]


def _canon(word):
    return re.sub(r"[ \t]+", " ", word)


_NEXT_FIELD = re.compile(r"^[ \t]*[,，][ \t]*[^\s:：,，]{1,12}[ \t]*[:：]")
_VALUE_END = re.compile(r"^(?:[ \t]*$|[ \t]*[/|(\[\t]|[ \t]{2,}|[ \t]*[.)\]]+[ \t]*$)")


def _is_field_value(rest, pats):
    """'항목명: 이름' 에서 이름 뒤가 값의 끝인지 (ROLE-11)."""
    rest = pats["after_strip"].sub("", rest, count=1)
    return bool(_VALUE_END.match(rest) or _NEXT_FIELD.match(rest))


_SIGN = re.compile(r"^[ \t]*(?:드림|올림)[ \t]*[.。]?[ \t]*$")


def _is_signature(text, line_no, before, after):
    lines = text.split("\n")
    tail = [i for i, ln in enumerate(lines) if ln.strip()][-3:]
    return line_no in tail and _SIGN.match(after) and len(before.split()) <= 2


def classify_mention(text, start, end, master=None):
    master = master or load_role_master()
    pats = _patterns(master)
    line_start = text.rfind("\n", 0, start) + 1
    before = text[line_start:start]
    after = text[end:].split("\n", 1)[0]

    found = []  # (rule_id, role)
    speaker = pats["speaker"].match(before) if re.match(r"^[ \t]*[:：]", after) else None
    if speaker:
        found.append(("ROLE-10", master.cue_to_role[_canon(speaker.group("cue"))]))
    field = pats["field"].search(before)
    if field and _is_field_value(after, pats):
        found.append(("ROLE-11", master.field_to_role[_canon(field.group("field"))]))
    if not speaker:
        m = pats["before"].search(before)
        if m:
            found.append(("ROLE-12", master.cue_to_role[_canon(m.group("cue"))]))
    m = pats["after"].match(after)
    if m:
        found.append(("ROLE-13", master.cue_to_role[_canon(m.group("cue"))]))

    roles = {role for _, role in found}
    if len(roles) == 1:
        return RoleResult(roles.pop(), "rule", found[0][0])
    if len(roles) > 1:
        return RoleResult("unknown", "rule", "ROLE-16")
    if "staff_contact" in master.entries and _is_signature(text, text.count("\n", 0, start), before, after):
        return RoleResult("staff_contact", "rule", "ROLE-14")
    return UNKNOWN


def role_for_mention(text, start, end, master=None):
    """기존 호출 호환: 역할 값만 돌려준다."""
    return classify_mention(text, start, end, master).role
