"""v5: 문서 내 이름 연결(NAME_LINK=doc), 질의 strict=False."""
import pytest

from starter import pii_pipeline as pii


def _spans(text, *names):
    out, pos = [], 0
    for n in names:
        i = text.index(n, pos)
        out.append({"label": "person", "start": i, "end": i + len(n)})
        pos = i + len(n)
    return out


def test_link_off_by_default(monkeypatch):
    monkeypatch.delenv("NAME_LINK", raising=False)
    text = "신도윤 고객 송금. 도윤 님이 유학 중"
    _, recs = pii.pseudonymize(text, _spans(text, "신도윤", "도윤"))
    assert recs[0]["token"] != recs[1]["token"] and "alias_token" not in recs[1]


def test_given_name_links_to_unique_full_name(monkeypatch):
    monkeypatch.setenv("NAME_LINK", "doc")
    text = "신도윤 고객 송금. 도윤 님이 유학 중"
    masked, recs = pii.pseudonymize(text, _spans(text, "신도윤", "도윤"))
    assert recs[0]["token"] == recs[1]["token"]
    assert recs[1]["alias_token"] == pii.make_token("person", "도윤")      # 검색용 별칭
    assert recs[1]["original"] == "도윤" and recs[1]["normalized"] == "신도윤"


def test_compound_surname(monkeypatch):
    monkeypatch.setenv("NAME_LINK", "doc")
    text = "남궁하율 고객, 하율 님"
    _, recs = pii.pseudonymize(text, _spans(text, "남궁하율", "하율"))
    assert recs[0]["token"] == recs[1]["token"]


def test_ambiguous_candidates_are_not_linked(monkeypatch):
    monkeypatch.setenv("NAME_LINK", "doc")
    text = "김민준 고객, 이민준 과장, 민준 님"
    _, recs = pii.pseudonymize(text, _spans(text, "김민준", "이민준", "민준"))
    assert len({r["token"] for r in recs}) == 3 and "alias_token" not in recs[2]


def test_query_mode_masks_unsupported_value_instead_of_failing():
    text = "번호 010-12AB-5678 확인"
    span = [{"label": "phone_number", "start": 3, "end": 16}]
    with pytest.raises(ValueError):
        pii.pseudonymize(text, span)
    masked, recs = pii.pseudonymize(text, span, strict=False)
    assert masked == "번호 [개인정보] 확인" and recs == []
