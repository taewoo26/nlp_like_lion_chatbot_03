"""역할 LLM 보조 (ROLE-20~23): 기본 꺼짐, 켜면 unknown만 묻고 가명 텍스트만 보낸다."""
import pytest

from starter import person_role_llm as llm
from starter import pii_pipeline as pii
from starter.ingest import chunk
from starter.person_role_mapping import map_person_roles


@pytest.fixture(autouse=True)
def synthetic_key(monkeypatch):
    monkeypatch.setattr(pii.config, "HMAC_KEY", "role-test-key-not-for-production")
    pii._key.cache_clear()
    yield
    pii._key.cache_clear()


def mapped(text, names, classifier):
    pos, cur = [], 0
    for n in names:
        s = text.index(n, cur); pos.append({"label": "person", "start": s, "end": s + len(n)}); cur = s + len(n)
    masked, records = pii.pseudonymize(text, pos)
    return masked, map_person_roles("L1", text, masked, records, chunk(masked), llm=classifier)


def test_disabled_by_default(monkeypatch):
    monkeypatch.delenv("ROLE_LLM", raising=False)
    assert llm.make_classifier(post=lambda p: "customer") is None


def test_invalid_mode(monkeypatch):
    monkeypatch.setenv("ROLE_LLM", "maybe")
    with pytest.raises(ValueError):
        llm.enabled()


def test_runs_without_llm():
    _, rows = mapped("홍길동 건 문의\n고객 김하늘", ["홍길동", "김하늘"], None)
    assert [(r["role"], r["source"]) for r in rows] == [("unknown", "default"), ("customer", "rule")]


@pytest.mark.parametrize("answer,role", [("customer", "customer"), ("Agent.", "agent"),
                                         ("고객인 듯", "unknown"), ("customer 또는 employee", "unknown"),
                                         ("vip", "unknown"), ("", "unknown")])
def test_parse_role(answer, role):
    assert llm.parse_role(answer) == role


def test_only_unknown_mentions_are_sent_and_only_masked_text(monkeypatch):
    monkeypatch.setenv("ROLE_LLM", "on")
    prompts = []
    clf = llm.make_classifier(post=lambda p: prompts.append(p) or "customer")
    text = "첫 줄\n홍길동 건 문의\n셋째 줄\n넷째 줄\n고객 김하늘"
    masked, rows = mapped(text, ["홍길동", "김하늘"], clf)
    assert len(prompts) == 1                                         # ROLE-20: unknown 1건만
    assert "홍길동" not in prompts[0] and "김하늘" not in prompts[0]  # ROLE-21: 원문 이름 없음
    assert "첫 줄" in prompts[0] and "셋째 줄" in prompts[0] and "넷째 줄" not in prompts[0]
    assert (rows[0]["role"], rows[0]["source"], rows[0]["rule_id"]) == ("customer", "llm", "ROLE-20")
    assert (rows[1]["role"], rows[1]["source"]) == ("customer", "rule")


def test_failure_becomes_unknown(monkeypatch):
    monkeypatch.setenv("ROLE_LLM", "on")
    def boom(prompt):
        raise TimeoutError
    _, rows = mapped("홍길동 건 문의", ["홍길동"], llm.make_classifier(post=boom))
    assert (rows[0]["role"], rows[0]["source"]) == ("unknown", "default")
