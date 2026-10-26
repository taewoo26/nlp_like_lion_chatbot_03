"""정규식 v2: 자체 검증셋 A에서 드러난 번호 오류 3가지를 일반 원칙으로 보완.

1. 계좌 4덩어리 (302-1928-4710-11): v1은 3덩어리만 허용해 뒤가 잘림(경계 오류)
   → 2~4덩어리 허용. 카드(4-4-4-4)는 먼저 잡히므로 겹치지 않음
2. 문서번호 안의 숫자 (결재-2026-1023-07): 앞에 '글자-'가 붙어 있으면 더 큰 ID의 일부 → 계좌 아님
3. 대표번호 (당사 대표번호 02-1234-5678): 바로 앞 단어가 대표번호·대표전화면 개인 번호 아님
   (라벨 가이드 2.2: 고객센터 대표번호는 정답 제외)
"""
import re

from detection.base import Span
from detection.rule import regex_detector as v1
from detection.rule.regex_detector import RegexDetector

ACCOUNT_V2 = re.compile(v1.D + r"\d{2,6}(?:-\d{2,6}){2,3}" + v1.E)
ID_PREFIX = re.compile(r"[A-Za-z가-힣]-$")
REP_NUMBER = re.compile(r"(대표\s?번호|대표\s?전화)\s*[:：]?\s*\(?$")

PATTERNS_V2 = [(lab, ACCOUNT_V2 if lab == "account_number" else pat) for lab, pat in v1.PATTERNS]


class RegexDetectorV2(RegexDetector):
    name = "regex_v2"
    patterns = PATTERNS_V2

    def detect(self, text: str) -> list[Span]:
        taken = [False] * len(text)
        spans: list[Span] = []
        for label, pat in self.patterns:
            for m in pat.finditer(text):
                s, e = m.start(), m.end()
                val = m.group()
                before = text[max(0, s - 12):s]
                if label == "account_number" and (
                        v1.PHONE_LIKE.match(val) or v1.DATE_LIKE.match(val) or ID_PREFIX.search(before)):
                    continue
                if label == "phone_number" and REP_NUMBER.search(before):
                    continue
                if any(taken[s:e]):
                    continue
                for i in range(s, e):
                    taken[i] = True
                spans.append(Span(s, e, label, val))
        return spans
