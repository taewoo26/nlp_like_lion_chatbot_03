"""규칙 기반 탐지기 = 정규식(번호·이메일·주소) + 이름 규칙(Kiwi)."""
from detection.base import BaseDetector, Span
from detection.rule.name_detector import NameDetector
from detection.rule.regex_detector import RegexDetector


class RuleDetector(BaseDetector):
    name = "rule"

    def __init__(self):
        self.regex = RegexDetector()
        self.names = NameDetector()

    def detect(self, text: str) -> list[Span]:
        spans = self.regex.detect(text)
        taken = [(s.start, s.end) for s in spans]
        for sp in self.names.detect(text):
            # 주소 안의 도로명 등 이미 잡힌 구간과 겹치는 이름은 버림
            if all(sp.end <= a or b <= sp.start for a, b in taken):
                spans.append(sp)
        return spans
