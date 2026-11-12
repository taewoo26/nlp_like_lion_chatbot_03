"""권한 정책 조회 모듈.

정책 파일(access_policy.yaml)을 읽어 검색 전 권한 필터와 복원 규칙을 돌려줍니다.
(운영 중 원본은 DB policy.* 테이블입니다. 지금은 YAML 초기값을 읽습니다. DB에서 읽는 get_policy()는 구현 예정)
권한 판단은 LLM이 아니라 이 모듈(코드)에서 합니다.

사용 예 (검색 SQL의 권한 필터):
    from policy.access_policy import load_policy
    policy = load_policy()
    allowed = policy.search_filter(level)    # None이면 필터 없음(전체), 리스트면 허용 목록
    if allowed is not None:
        sql += " AND doc_type = ANY(%(allowed)s)"

주의: 검색 필터에는 allowed_doc_types()가 아니라 search_filter()를 쓰세요.
- 미등록 doc_type(정책에 없는 유형, doc_type이 NULL인 청크 포함)은 Lv2 전용입니다.
- Lv0/Lv1은 허용 목록에 미등록 유형이 없으므로 자동으로 제외됩니다.
- Lv2에 허용 목록을 쓰면 미등록 문서까지 빠지므로, search_filter()는 Lv2에 None을 돌려줍니다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

POLICY_PATH = Path(__file__).with_name("access_policy.yaml")
VALID_LEVELS = (0, 1, 2)
# doc_type의 분류(정책서 3절 판정 질문의 결과)와 그 분류의 기본 레벨
CATEGORY_BASE_LEVEL = {"직원개인": 2, "고객업무": 1, "내부업무": 1, "공개": 0}
# 팀 코드 체계 (DB CHECK teams_cd_format 과 같은 규칙)
TEAM_CD_PATTERN = re.compile(r"DEPT_[A-Z0-9]+(_[A-Z0-9]+)*")


@dataclass(frozen=True)
class TeamLevelRule:
    """사용자 레벨 규칙 한 행 (DB policy.team_level). team_cd/position이 None이면 '모두'."""
    team_cd: str | None
    position: str | None
    level: int
    rationale: str

    @property
    def specificity(self) -> int:
        return (self.team_cd is not None) + (self.position is not None)

    def matches(self, team_cd: str | None, position: str | None) -> bool:
        return ((self.team_cd is None or self.team_cd == team_cd)
                and (self.position is None or self.position == position))


@dataclass(frozen=True)
class AccessPolicy:
    version: str
    min_levels: dict          # doc_type -> min_level
    unregistered_min_level: int
    restore_rules: dict       # level -> {label: mode}
    restore_default: str
    audit_read_min_level: int
    team_levels: tuple = ()            # TeamLevelRule 목록 (팀·직책 → 계정 기본 레벨)
    categories: dict = None            # doc_type -> 분류 (직원개인/고객업무/내부업무/공개)
    teams: dict = None                 # team_cd -> team_name (DB policy.teams)

    # --- 검색 전 권한 필터 -------------------------------------------------
    def min_level(self, doc_type: str | None) -> int:
        """doc_type의 최소 레벨. 미등록·None이면 기본값(Lv2)."""
        return self.min_levels.get(doc_type, self.unregistered_min_level)

    def can_view(self, level: int, doc_type: str | None) -> bool:
        self._check_level(level)
        return level >= self.min_level(doc_type)

    def allowed_doc_types(self, level: int) -> list[str]:
        """level이 열람 가능한 '등록된' doc_type 목록(정렬). 미등록 유형은 포함하지 않음.
        검색 필터에는 search_filter()를 쓰세요(Lv2의 미등록 문서 누락 방지)."""
        self._check_level(level)
        return sorted(t for t, m in self.min_levels.items() if level >= m)

    def search_filter(self, level: int) -> list[str] | None:
        """검색 SQL용 권한 필터. 미등록 doc_type까지 볼 수 있는 레벨이면 None(필터 없음)."""
        self._check_level(level)
        if level >= self.unregistered_min_level:
            return None
        return self.allowed_doc_types(level)

    def is_registered(self, doc_type: str | None) -> bool:
        return doc_type in self.min_levels

    # --- 복원 규칙 ---------------------------------------------------------
    def restore_mode(self, level: int, label: str) -> str:
        """level × label의 복원 모드(none/full). 규칙에 없으면 기본 none."""
        self._check_level(level)
        return self.restore_rules.get(level, {}).get(label, self.restore_default)

    def default_level_for(self, team_cd: str | None, position: str | None = None) -> int:
        """계정을 만들 때 채울 기본 레벨(이후 자동 재계산 없음). DB policy.resolve_team_level()과 같은 규칙.
        맞는 규칙 중 더 구체적인 것 → 같으면 낮은 레벨 → 맞는 규칙이 없으면 Lv0(기본 거부)."""
        team_cd, position = _clean(team_cd), _clean(position)
        hits = [r for r in self.team_levels if r.matches(team_cd, position)]
        if not hits:
            return 0
        return min(hits, key=lambda r: (-r.specificity, r.level)).level

    def can_read_audit_log(self, level: int) -> bool:
        self._check_level(level)
        return level >= self.audit_read_min_level

    @staticmethod
    def _check_level(level: int) -> None:
        if level not in VALID_LEVELS:
            raise ValueError(f"알 수 없는 권한 레벨: {level!r} (허용: {VALID_LEVELS})")


def load_policy(path: Path | str = POLICY_PATH) -> AccessPolicy:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    min_levels = {t: int(v["min_level"]) for t, v in raw["doc_types"].items()}
    categories = {t: v["category"] for t, v in raw["doc_types"].items()}
    for t, c in categories.items():
        if c not in CATEGORY_BASE_LEVEL:
            raise ValueError(f"{t}: 알 수 없는 분류 {c!r} (허용: {tuple(CATEGORY_BASE_LEVEL)})")
        # DB CHECK doc_type_level_floor 와 같은 규칙: 분류 기본 레벨이 하한(올리기만 가능)
        if min_levels[t] < CATEGORY_BASE_LEVEL[c]:
            raise ValueError(f"{t}: 분류 {c}의 기본 레벨 Lv{CATEGORY_BASE_LEVEL[c]}보다 낮출 수 없습니다"
                             f" (현재 Lv{min_levels[t]})")
    restore = raw["restore"]
    teams = _parse_teams(raw.get("teams") or {})
    return AccessPolicy(
        version=raw["version"],
        min_levels=min_levels,
        unregistered_min_level=int(raw["defaults"]["unregistered_min_level"]),
        restore_rules={int(k): v for k, v in restore.items() if isinstance(k, int)},
        restore_default=restore["default_mode"],
        audit_read_min_level=int(raw["audit_log_read_min_level"]),
        team_levels=_parse_team_levels(raw.get("team_levels") or [], teams),
        categories=categories,
        teams=teams,
    )


def _clean(v: str | None) -> str | None:
    """앞뒤 공백 제거, 빈 값은 None."""
    v = (v or "").strip()
    return v or None


def _parse_teams(items: dict) -> dict:
    """teams 검증. DB 제약(teams_*)과 같은 규칙: 코드 형식 DEPT_*, 이름 필수·중복 금지."""
    teams, names = {}, set()
    for cd, name in items.items():
        cd, name = _clean(cd), _clean(name)
        if cd is None or not TEAM_CD_PATTERN.fullmatch(cd):
            raise ValueError(f"teams: 팀 코드 형식이 DEPT_<영문 대문자·숫자>가 아닙니다: {cd!r}")
        if name is None:
            raise ValueError(f"teams[{cd}]: 팀 이름이 비어 있습니다")
        if name in names:
            raise ValueError(f"teams[{cd}]: 팀 이름이 중복됩니다: {name}")
        names.add(name)
        teams[cd] = name
    return teams


def _parse_team_levels(items: list, teams: dict | None = None) -> tuple:
    """team_levels 검증. DB 제약(team_level_*)과 같은 규칙을 YAML 단계에서도 확인한다.
    teams를 주면 team_cd가 팀 목록에 있는지도 확인한다(DB 외래키 team_level_team_fk)."""
    rules, seen = [], set()
    for i, x in enumerate(items):
        team_cd, position = _clean(x.get("team_cd")), _clean(x.get("position"))
        level, rationale = int(x["level"]), (x.get("rationale") or "").strip()
        where = f"team_levels[{i}] ({team_cd or '*'}/{position or '*'})"
        if level not in VALID_LEVELS:
            raise ValueError(f"{where}: 알 수 없는 레벨 {level}")
        if teams is not None and team_cd is not None and team_cd not in teams:
            raise ValueError(f"{where}: teams에 없는 팀 코드입니다")
        if team_cd is None and position is None:
            raise ValueError(f"{where}: 팀이나 직책 중 하나는 있어야 합니다")
        if level == 2 and team_cd is None:
            raise ValueError(f"{where}: Lv2 규칙에는 팀이 있어야 합니다")
        if not rationale:
            raise ValueError(f"{where}: rationale(근거)가 비어 있습니다")
        if (team_cd, position) in seen:
            raise ValueError(f"{where}: 같은 팀·직책 규칙이 중복됩니다")
        seen.add((team_cd, position))
        rules.append(TeamLevelRule(team_cd, position, level, rationale))
    return tuple(rules)
