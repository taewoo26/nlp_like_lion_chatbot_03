"""모든 테스트 공통 설정.

.env 에 REGION_META=on / ROLE_META=on / PERSON_DICT=on 을 켜 둔 컴퓨터에서도 기존 테스트가
실전 2 기본 동작(off)으로 돌도록 끈 상태로 시작한다. 켜서 시험하는 테스트는 monkeypatch.setenv 로 직접 켠다.
인물 사전 연결 스위치(PERSON_LINK_UNIQUE / PERSON_LINK_GIVEN)는 지워서 코드 기본값으로 돌게 한다.
출력 재검사(RECHECK)도 끈다: 켜면 탐지 모델을 불러오므로, 재검사 테스트(tests/test_recheck.py)만 켜고 가짜 탐지기를 쓴다.
"""
import pytest


@pytest.fixture(autouse=True)
def metadata_off_by_default(monkeypatch):
    monkeypatch.setenv("REGION_META", "off")
    monkeypatch.setenv("ROLE_META", "off")
    monkeypatch.setenv("PERSON_DICT", "off")
    monkeypatch.setenv("RECHECK", "off")
    monkeypatch.delenv("PERSON_LINK_UNIQUE", raising=False)
    monkeypatch.delenv("PERSON_LINK_GIVEN", raising=False)
