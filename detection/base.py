"""탐지기 공통 인터페이스.

모든 탐지기(규칙 기반 / 토큰 분류 / LLM)는 BaseDetector를 상속하고
detect(text) -> list[Span] 하나만 구현한다.
저장·시간 측정·경계 후처리는 detection/run_detect.py가 공통으로 처리한다.
"""
from dataclasses import dataclass, asdict

# run_compare.py 채점 대상 7종 (organization은 회사 안내로 제외됨)
LABELS = [
    "person",
    "phone_number",
    "email",
    "address",
    "resident_registration_number",
    "account_number",
    "card_number",
]


@dataclass
class Span:
    start: int          # 원문 기준 시작 위치 (포함)
    end: int            # 원문 기준 끝 위치 (미포함) → text[start:end]
    label: str          # LABELS 중 하나
    text: str = ""      # 확인용. 저장할 때는 원문에서 다시 잘라 씀
    confidence: float | str = ""   # 선택. 오류 CSV에 같이 찍힘

    def to_dict(self) -> dict:
        return asdict(self)


class BaseDetector:
    name: str = "base"

    def detect(self, text: str) -> list[Span]:
        raise NotImplementedError
