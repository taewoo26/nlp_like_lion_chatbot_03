"""질의의 역할·지역 조건과 검색 결과 판정 (메타데이터 규칙 정의서 v0.2 ROLE-40~46, REG-30~36).

    analyze(query, records, master) -> Conditions
    role_tier(doc_persons, condition) -> 1 일치 | 2 미상 | 3 명백히 다름(제외)

역할 (질의 단서는 role_master.query_cues 만 쓴다, ROLE-45)
  - 질의 속 PERSON 마다 바로 앞뒤의 질의 단서로 역할을 정한다 (person_roles.classify_mention 재사용)
  - 단서가 없으면 조건 없음 (ROLE-40). 단서가 서로 다르면 조건 없음 + 안내 (ROLE-16과 같은 기준)
  - 이름 없이 역할어만 있으면, 지역 조건이 함께 있을 때만 문서의 아무 언급에 적용 (REG-34 '대구 사는 고객')
  - '직원' 질의는 employee·agent·staff_contact 를 일치로 본다 (ROLE-46, 초안 그대로)
판정 (문서 단위, ROLE-44: 한 문서에서 하나라도 일치하면 일치)
  1 일치  : 그 토큰의 언급 중 하나라도 허용 역할 (ROLE-41)
  2 미상  : 일치 없음 + unknown 언급이 있거나 언급 정보가 아예 없음 (ROLE-42, 메타데이터 미적재 문서 포함)
  3 제외  : 언급이 모두 unknown 이 아닌 다른 역할 (ROLE-43, 문구 그대로: source 구분 없음)
지역 (REG-30~33): address.query_regions 결과. 모호하면 검색하지 않고 되묻는다 (REG-33, 초안)
"""
import re
from dataclasses import dataclass, field
from functools import lru_cache

from .address import SIDO, _SIGUNGU_OWNERS, query_regions
from .person_roles import _JOSA, classify_mention
from .role_master import RoleMaster, load_role_master

STAFF_GROUP = frozenset({"employee", "agent", "staff_contact"})


@dataclass
class Conditions:
    roles: list = field(default_factory=list)      # [{"token": str|None, "role": str, "accept": frozenset}]
    regions: list = field(default_factory=list)    # 표준 지역값 (시도 또는 '시도 시군구')
    clarify: str = None                            # 있으면 검색하지 않고 되묻는다
    notices: list = field(default_factory=list)

    @property
    def active(self):
        return bool(self.roles or self.regions or self.clarify)


def query_master(master):
    """문서 단서·항목명을 비우고 질의 단서만 남긴 마스터 (ROLE-45)."""
    return RoleMaster.from_rows([(e.code, e.name_ko, e.query_cues, (), (), e.priority, e.active)
                                 for e in master.entries.values()])


def accepted_roles(role):
    return STAFF_GROUP if role == "employee" else frozenset({role})     # ROLE-46 (초안)


@lru_cache(maxsize=1)
def _precheck():
    """역할·지역 단서가 있을 수도 있는 질의만 거르는 가벼운 검사 (없으면 기존 검색 그대로)."""
    words = set(load_role_master().query_cue_to_role) | set(SIDO) | set(_SIGUNGU_OWNERS) | {"광주시"}
    return re.compile("|".join(re.escape(w) for w in sorted(words, key=len, reverse=True)))


def may_have_conditions(query):
    return bool(_precheck().search(query))


def _role_words(query, qmaster):
    cues = "|".join(re.escape(w) for w in sorted(qmaster.cue_to_role, key=len, reverse=True))
    if not cues:
        return set()
    pat = re.compile(rf"(?<![가-힣])(?P<c>{cues})(?:님)?(?=$|[^가-힣]|{_JOSA}(?![가-힣]))")
    return {qmaster.cue_to_role[m.group("c")] for m in pat.finditer(query)}


def analyze(query, records, master):
    cond = Conditions()
    qmaster = query_master(master)
    names = {code: e.name_ko for code, e in master.entries.items()}

    persons = [r for r in records if r["label"] == "person"]
    for r in persons:
        res = classify_mention(query, r["start"], r["end"], qmaster)
        if res.rule_id == "ROLE-16":
            cond.notices.append("질문의 역할 단서가 서로 달라 역할 조건 없이 검색했습니다.")
        elif res.role != "unknown":
            cond.roles.append({"token": r["token"], "role": res.role, "accept": accepted_roles(res.role),
                               "name": names.get(res.role, res.role)})

    for reg in query_regions(query):
        if reg.kind == "ambiguous":
            cond.clarify = f"'{reg.word}'은(는) {reg.value} 중 어느 곳인지 알 수 없습니다. 시도를 함께 적어 주세요."
        else:
            cond.regions.append(reg.value)

    if not persons and cond.regions:                                   # REG-34: 이름 없는 역할어
        roles = _role_words(query, qmaster)
        if len(roles) == 1:
            role = roles.pop()
            cond.roles.append({"token": None, "role": role, "accept": accepted_roles(role),
                               "name": names.get(role, role)})
        elif len(roles) > 1:
            cond.notices.append("질문의 역할 단서가 서로 달라 역할 조건 없이 검색했습니다.")
    return cond


def role_tier(doc_persons, condition):
    mentions = [p for p in doc_persons if condition["token"] is None or p.get("token") == condition["token"]]
    if any(p.get("role") in condition["accept"] for p in mentions):
        return 1
    if not mentions or any(p.get("role") == "unknown" for p in mentions):
        return 2
    return 3


def doc_tier(doc_persons, conditions):
    return max((role_tier(doc_persons, c) for c in conditions), default=1)


def region_params(regions):
    """regions 배열 비교용 (정확히 같은 값, 또는 그 값으로 시작하는 하위 지역)."""
    return list(regions), [r + " %" for r in regions]


def load_master_for_search(conn, source):
    return load_role_master(conn if source == "db" else None)


class Docs(list):
    """search() 결과 목록. 기존처럼 list 로 쓰고, 안내 정보는 .info 로 읽는다."""
    def __init__(self, items=(), info=None):
        super().__init__(items)
        self.info = info or {}
