"""정책 파일 정합성 검증.

실행: 레포 루트에서
    python -m pytest policy/test_access_policy.py -q
    (pytest가 없으면) python policy/test_access_policy.py
"""
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from access_policy import CATEGORY_BASE_LEVEL, POLICY_PATH, TEAM_CD_PATTERN, VALID_LEVELS, load_policy  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
DATA = {
    "dev": ROOT / "data/dev/pii_synth_finance_dev.jsonl",
    "test": ROOT / "data/test/pii_synth_finance_test.jsonl",
    "holdout": ROOT / "data/holdout/holdout_labeled.jsonl",
}
LABELS = ["person", "phone_number", "email", "address",
          "resident_registration_number", "account_number", "card_number"]

# 정책서(docs/permission_policy_draft.md) 5절 표의 기대값 (holdout 187 / 전체 487)
EXPECTED_VIEWABLE = {"holdout": {0: 23, 1: 158, 2: 187}, "all": {0: 73, 1: 443, 2: 487}}

policy = load_policy()


def _doc_types():
    out = {}
    for split, path in DATA.items():
        if not path.exists():           # data/는 Git 제외 폴더라 없을 수 있음
            continue
        out[split] = [json.loads(l)["doc_type"] for l in path.open(encoding="utf-8")]
    return out


def test_min_levels_in_range():
    assert all(m in VALID_LEVELS for m in policy.min_levels.values())
    assert policy.unregistered_min_level == 2          # 기본 거부


def test_hierarchy():
    prev = set()
    for lv in VALID_LEVELS:
        cur = set(policy.allowed_doc_types(lv))
        assert prev <= cur                              # Lv2 ⊇ Lv1 ⊇ Lv0
        prev = cur
    assert set(policy.allowed_doc_types(2)) == set(policy.min_levels)


def test_unregistered_denied_below_lv2():
    assert not policy.can_view(0, "새로운유형")
    assert not policy.can_view(1, "새로운유형")
    assert policy.can_view(2, "새로운유형")
    assert not policy.can_view(1, None)


def test_search_filter():
    # Lv0/Lv1은 등록된 허용 목록, Lv2는 필터 없음(미등록·NULL 포함 전체)
    assert policy.search_filter(0) == policy.allowed_doc_types(0)
    assert policy.search_filter(1) == policy.allowed_doc_types(1)
    assert policy.search_filter(2) is None
    docs = ["공지", "대출상담", "감사지적", "새로운유형", None]
    def visible(lv):
        f = policy.search_filter(lv)
        return [d for d in docs if f is None or d in f]
    assert visible(0) == ["공지"]
    assert visible(1) == ["공지", "대출상담"]
    assert visible(2) == docs
    # can_view와 search_filter의 판단이 항상 일치
    for lv in VALID_LEVELS:
        assert visible(lv) == [d for d in docs if policy.can_view(lv, d)]


def test_invalid_level_rejected():
    for bad in (-1, 3, None, "1"):
        try:
            policy.allowed_doc_types(bad)
        except ValueError:
            continue
        raise AssertionError(f"레벨 {bad!r}이 거부되지 않음")


def test_restore_rules():
    for lab in LABELS:
        assert policy.restore_mode(0, lab) == "none"
        assert policy.restore_mode(1, lab) == "none"
        assert policy.restore_mode(2, lab) == "full"
    assert policy.restore_mode(2, "알수없는라벨") == "none"   # 기본 none
    assert policy.can_read_audit_log(2) and not policy.can_read_audit_log(1)


def test_all_data_doc_types_registered():
    dts = _doc_types()
    if not dts:
        return
    seen = {t for v in dts.values() for t in v}
    assert not (seen - set(policy.min_levels)), f"정책 누락: {seen - set(policy.min_levels)}"
    assert not (set(policy.min_levels) - seen), f"데이터에 없는 정책 항목: {set(policy.min_levels) - seen}"
    assert len(policy.min_levels) == 33


def test_viewable_counts_match_policy_doc():
    dts = _doc_types()
    if set(dts) != set(DATA):
        return
    allv = [t for v in dts.values() for t in v]
    for lv in VALID_LEVELS:
        assert sum(policy.can_view(lv, t) for t in dts["holdout"]) == EXPECTED_VIEWABLE["holdout"][lv]
        assert sum(policy.can_view(lv, t) for t in allv) == EXPECTED_VIEWABLE["all"][lv]



def test_team_levels_initial():
    rules = policy.team_levels
    assert len(rules) == 10, "초기 규칙은 팀 규칙 10개"
    assert all(r.position is None for r in rules), "초기값에는 직책 규칙이 없음"
    assert policy.default_level_for("DEPT_HR") == 2
    assert policy.default_level_for(" DEPT_CUST_PROT ") == 1              # 앞뒤 공백 무시
    assert policy.default_level_for("DEPT_CUST_PROT", "팀장") == 1        # 직책 규칙이 없으면 팀 규칙
    assert policy.default_level_for("DEPT_NONE") == 0                    # 맞는 규칙이 없으면 Lv0
    assert policy.default_level_for(None, None) == 0


def test_team_level_resolution():
    # 직책 규칙을 더한 가상 정책으로 판정 순서를 확인
    from dataclasses import replace

    from access_policy import TeamLevelRule
    p = replace(policy, team_levels=policy.team_levels + (
        TeamLevelRule("DEPT_IT_OPS", "팀장", 1, "t"),      # 팀+직책
        TeamLevelRule(None, "팀장", 1, "t"),            # 직책만 (전 팀 팀장 Lv1)
        TeamLevelRule(None, "인턴", 0, "t"),            # 직책만
    ))
    assert p.default_level_for("DEPT_IT_OPS", "팀원") == 0      # 팀 규칙
    assert p.default_level_for("DEPT_IT_OPS", "팀장") == 1      # 팀+직책이 가장 구체적
    assert p.default_level_for("DEPT_DIGITAL", "팀장") == 0  # 팀만(0) = 직책만(1) 동률 → 낮은 레벨
    assert p.default_level_for("DEPT_NONE", "팀장") == 1        # 직책 규칙만 맞음
    assert p.default_level_for("DEPT_HR", "인턴") == 0        # 팀만(2) = 직책만(0) 동률 → 낮은 레벨


def _load_rules(rules: list):
    import tempfile

    import yaml
    raw = yaml.safe_load(POLICY_PATH.read_text(encoding="utf-8"))
    raw["team_levels"] = rules
    with tempfile.TemporaryDirectory() as d:
        f = Path(d) / "p.yaml"
        f.write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")
        return load_policy(f)


def test_teams_initial():
    assert len(policy.teams) == 10
    assert policy.teams["DEPT_CREDIT"] == "여신심사팀"
    assert all(TEAM_CD_PATTERN.fullmatch(cd) for cd in policy.teams)
    assert {r.team_cd for r in policy.team_levels} <= set(policy.teams), "규칙의 팀 코드는 팀 목록에 있어야 함"


def _load_teams(teams: dict):
    import tempfile

    import yaml
    raw = yaml.safe_load(POLICY_PATH.read_text(encoding="utf-8"))
    raw["teams"] = teams
    raw["team_levels"] = []
    with tempfile.TemporaryDirectory() as d:
        f = Path(d) / "p.yaml"
        f.write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")
        return load_policy(f)


def test_teams_validation():
    bad = [
        {"인사팀": "인사팀"},                          # 코드 형식
        {"dept_hr": "인사팀"},                         # 소문자
        {"DEPT_": "인사팀"},                           # 빈 코드
        {"DEPT_HR": ""},                               # 이름 없음
        {"DEPT_HR": "인사팀", "DEPT_HR2": "인사팀"},   # 이름 중복
    ]
    for teams in bad:
        try:
            _load_teams(teams)
        except ValueError:
            continue
        raise AssertionError(f"잘못된 팀 목록이 통과함: {teams}")
    assert _load_teams({"DEPT_IT_OPS2": "IT운영2팀"}).teams == {"DEPT_IT_OPS2": "IT운영2팀"}


def test_team_level_validation():
    bad = [
        [{"team_cd": "DEPT_UNKNOWN", "level": 1, "rationale": "t"}],              # 팀 목록에 없는 코드
        [{"position": "팀장", "level": 2, "rationale": "t"}],                 # Lv2인데 팀 없음
        [{"level": 1, "rationale": "t"}],                                       # 조건 없음
        [{"team_cd": "DEPT_HR", "level": 1, "rationale": "t"},
         {"team_cd": "DEPT_HR", "level": 0, "rationale": "t"}],                          # 중복
        [{"team_cd": "DEPT_HR", "level": 1, "rationale": ""}],                           # 근거 없음
        [{"team_cd": "DEPT_HR", "level": 3, "rationale": "t"}],                          # 레벨 범위
    ]
    for rules in bad:
        try:
            _load_rules(rules)
        except ValueError:
            continue
        raise AssertionError(f"잘못된 규칙이 통과함: {rules}")
    ok = _load_rules([{"team_cd": "DEPT_HR", "position": "팀장", "level": 2, "rationale": "t"}])
    assert ok.default_level_for("DEPT_HR", "팀장") == 2



def test_categories():
    cats = policy.categories
    assert set(cats) == set(policy.min_levels), "모든 doc_type에 분류가 있어야 함"
    assert set(cats.values()) <= set(CATEGORY_BASE_LEVEL)
    # 직원 개인 문서는 정확히 Lv2 전용 4종
    assert sorted(t for t, c in cats.items() if c == "직원개인") == ["감사지적", "법인카드소명", "인사발령", "채용메일"]
    # 현재 정책은 모든 doc_type이 자기 분류의 기본 레벨과 같다(관리자가 올린 예외 없음)
    for t, c in cats.items():
        assert policy.min_levels[t] == CATEGORY_BASE_LEVEL[c], f"{t}: 분류 {c} 기본 레벨과 다름"


def _load_modified(doc_type: str, min_level: int):
    """YAML을 복사해 doc_type 하나의 min_level만 바꿔 load_policy()에 넣는다."""
    import tempfile

    import yaml
    raw = yaml.safe_load(POLICY_PATH.read_text(encoding="utf-8"))
    raw["doc_types"][doc_type]["min_level"] = min_level
    with tempfile.TemporaryDirectory() as d:
        f = Path(d) / "p.yaml"
        f.write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")
        return load_policy(f)


def _first_of(category: str) -> str:
    return sorted(t for t, c in policy.categories.items() if c == category)[0]


def test_level_floor_blocks_lowering():
    # 분류 기본 레벨보다 낮추면 거부: 직원개인 2→1, 고객업무 1→0, 내부업무 1→0
    for cat, lowered in (("직원개인", 1), ("고객업무", 0), ("내부업무", 0)):
        t = _first_of(cat)
        try:
            _load_modified(t, lowered)
        except ValueError:
            continue
        raise AssertionError(f"{cat} 문서({t})를 Lv{lowered}로 내린 정책이 통과함")


def test_level_floor_allows_raising():
    # 올리는 것은 허용: 공개 0→1, 고객업무 1→2
    for cat, raised in (("공개", 1), ("고객업무", 2)):
        t = _first_of(cat)
        assert _load_modified(t, raised).min_levels[t] == raised


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("PASS", t.__name__)
    print(f"정책 {policy.version}: doc_type {len(policy.min_levels)}종, 전체 통과")
