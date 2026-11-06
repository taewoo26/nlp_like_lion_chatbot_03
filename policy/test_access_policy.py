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
from access_policy import VALID_LEVELS, load_policy  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
DATA = {
    "dev": ROOT / "data/dev/pii_synth_finance_dev.jsonl",
    "test": ROOT / "data/test/pii_synth_finance_test.jsonl",
    "holdout": ROOT / "data/holdout/holdout_labeled.jsonl",
}
LABELS = ["person", "phone_number", "email", "address",
          "resident_registration_number", "account_number", "card_number"]

# 정책서(docs/permission_policy_draft.md) 5절 표의 기대값 (holdout 187 / 전체 487)
EXPECTED_VIEWABLE = {"holdout": {0: 23, 1: 120, 2: 187}, "all": {0: 73, 1: 390, 2: 487}}

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


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("PASS", t.__name__)
    print(f"정책 {policy.version}: doc_type {len(policy.min_levels)}종, 전체 통과")
