"""권한 정책 조회 모듈.

정책 파일(access_policy.yaml)을 읽어 검색 전 권한 필터와 복원 규칙을 돌려줍니다.
(정책 원본을 DB policy.* 테이블로 옮기는 중입니다. 지금은 YAML 초기값을 읽습니다.)
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

from dataclasses import dataclass
from pathlib import Path

import yaml

POLICY_PATH = Path(__file__).with_name("access_policy.yaml")
VALID_LEVELS = (0, 1, 2)
# doc_type의 분류(정책서 3절 판정 질문의 결과)와 그 분류의 기본 레벨
CATEGORY_BASE_LEVEL = {"직원개인": 2, "고객업무": 1, "내부업무": 1, "공개": 0}


@dataclass(frozen=True)
class AccessPolicy:
    version: str
    min_levels: dict          # doc_type -> min_level
    unregistered_min_level: int
    restore_rules: dict       # level -> {label: mode}
    restore_default: str
    audit_read_min_level: int
    team_default_levels: dict = None   # team -> 기본 레벨 (계정 생성용)
    categories: dict = None            # doc_type -> 분류 (직원개인/고객업무/내부업무/공개)

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

    def default_level_for_team(self, team: str | None) -> int:
        """계정을 만들 때 채울 기본 레벨. 표에 없거나 비어 있는 팀은 Lv0(기본 거부)."""
        return (self.team_default_levels or {}).get((team or "").strip(), 0)

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
        # DB CHECK doc_type_access_level_floor 와 같은 규칙: 분류 기본 레벨이 하한(올리기만 가능)
        if min_levels[t] < CATEGORY_BASE_LEVEL[c]:
            raise ValueError(f"{t}: 분류 {c}의 기본 레벨 Lv{CATEGORY_BASE_LEVEL[c]}보다 낮출 수 없습니다"
                             f" (현재 Lv{min_levels[t]})")
    restore = raw["restore"]
    return AccessPolicy(
        version=raw["version"],
        min_levels=min_levels,
        unregistered_min_level=int(raw["defaults"]["unregistered_min_level"]),
        restore_rules={int(k): v for k, v in restore.items() if isinstance(k, int)},
        restore_default=restore["default_mode"],
        audit_read_min_level=int(raw["audit_log_read_min_level"]),
        team_default_levels={str(t): int(v) for t, v in (raw.get("team_default_levels") or {}).items()},
        categories=categories,
    )
