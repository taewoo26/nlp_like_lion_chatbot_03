"""탐지기 공통 인터페이스.

모든 탐지기(규칙 기반 / 토큰 분류 / LLM)는 이 형식으로 결과를 반환한다.
채점기, 가명처리, 출력 재검사가 모두 이 형식을 입력으로 사용한다.
"""
from dataclasses import dataclass, asdict

LABELS = [
    "person",
    "phone_number",
    "organization",
    "email",
    "address",
    "resident_registration_number",
    "account_number",
    "card_number",
]


@dataclass
class Span:
    start: int   # 원문 기준 시작 위치 (포함)
    end: int     # 원문 기준 끝 위치 (미포함)
    label: str   # LABELS 중 하나
    text: str    # text == 원문[start:end]

    def to_dict(self) -> dict:
        return asdict(self)


class BaseDetector:
    name: str = "base"

    def detect(self, text: str) -> list[Span]:
        raise NotImplementedError
