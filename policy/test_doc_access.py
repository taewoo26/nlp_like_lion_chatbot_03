"""문서 열람 판정(AccessPolicy.can_view_doc) 단위 테스트. DB 없이 실행된다.

실행: python -m pytest policy/test_doc_access.py -q
기준: 11-26 권한 표 (중간 정리 문서). 계정은 시연 계정(07_seed_demo_users.sql)과 같은 조합이며,
'Lv1 비고객 팀(IT운영팀)'은 시연 계정에 없어 가상으로 만든 계정이다.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from access_policy import load_policy  # noqa: E402

policy = load_policy()

USERS = {
    "admin":     {"access_level": 2, "team_cd": "DEPT_INFOSEC",    "is_admin": True},   # SB260002 개인정보 담당자
    "lv2_hr":    {"access_level": 2, "team_cd": "DEPT_HR",         "is_admin": False},  # SB260001 팀장
    "lv1_cust":  {"access_level": 1, "team_cd": "DEPT_COLLECTION", "is_admin": False},  # SB260004 고객 업무 팀
    "lv1_it":    {"access_level": 1, "team_cd": "DEPT_IT_OPS",     "is_admin": False},  # 가상: 비고객 팀 Lv1
    "lv0_it":    {"access_level": 0, "team_cd": "DEPT_IT_OPS",     "is_admin": False},  # SB260009
}

DOCS = {                                    # (doc_type, 담당 팀)
    "공개":            ("공지", []),
    "고객업무":        ("VOC요약", []),
    "내부업무_IT":     ("업무메일", ["DEPT_IT_OPS"]),
    "내부업무_미결정": ("회의록", []),
    "사내메신저":      ("사내메신저", ["DEPT_IT_OPS"]),
    "직원개인":        ("감사지적", ["DEPT_IT_OPS"]),
    "미등록":          ("정책에없는유형", []),
}

#            공개  고객업무 내부업무_IT 내부업무_미결정 사내메신저 직원개인 미등록
EXPECTED = {
    "admin":    (1, 1, 1, 1, 1, 1, 1),
    "lv2_hr":   (1, 1, 1, 1, 1, 0, 0),
    "lv1_cust": (1, 1, 0, 0, 0, 0, 0),
    "lv1_it":   (1, 0, 1, 0, 0, 0, 0),
    "lv0_it":   (1, 0, 0, 0, 0, 0, 0),
}

CASES = [(u, d, bool(EXPECTED[u][i])) for u in USERS for i, d in enumerate(DOCS)]


@pytest.mark.parametrize("user,doc,expected", CASES, ids=[f"{u}-{d}" for u, d, _ in CASES])
def test_can_view_doc(user, doc, expected):
    doc_type, teams = DOCS[doc]
    assert policy.can_view_doc(USERS[user], doc_type, teams) is expected


def test_customer_team_group_only():
    """고객업무는 담당 팀과 관계없이 고객 업무 팀 그룹의 Lv1 이면 열람 (팀 구분 없음)."""
    for team in policy.customer_teams:
        user = {"access_level": 1, "team_cd": team, "is_admin": False}
        assert policy.can_view_doc(user, "VOC요약", ["DEPT_HR"])


def test_internal_other_team_blocked():
    """내부업무는 Lv1 이라도 담당 팀이 다르면 막는다."""
    user = {"access_level": 1, "team_cd": "DEPT_CS_CENTER", "is_admin": False}
    assert not policy.can_view_doc(user, "업무메일", ["DEPT_IT_OPS"])
    assert policy.can_view_doc(user, "출장보고", ["DEPT_RISK", "DEPT_CS_CENTER"])   # 여러 팀 중 하나면 열람


def test_bad_level_raises():
    with pytest.raises(ValueError):
        policy.can_view_doc({"access_level": 3, "team_cd": "DEPT_HR"}, "공지", [])
