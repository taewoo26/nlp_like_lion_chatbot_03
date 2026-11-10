"""모든 테스트 공통 설정.

.env 에 REGION_META=on / ROLE_META=on / PERSON_DICT=on 을 켜 둔 컴퓨터에서도 기존 테스트가
실전 2 기본 동작(off)으로 돌도록 끈 상태로 시작한다. 켜서 시험하는 테스트는 monkeypatch.setenv 로 직접 켠다.
인물 사전 연결 스위치(PERSON_LINK_UNIQUE / PERSON_LINK_GIVEN)는 지워서 코드 기본값으로 돌게 한다.
"""
import pytest


@pytest.fixture(autouse=True)
def metadata_off_by_default(monkeypatch):
    monkeypatch.setenv("REGION_META", "off")
    monkeypatch.setenv("ROLE_META", "off")
    monkeypatch.setenv("PERSON_DICT", "off")
    monkeypatch.delenv("PERSON_LINK_UNIQUE", raising=False)
    monkeypatch.delenv("PERSON_LINK_GIVEN", raising=False)
