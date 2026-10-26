"""정규식 v3: holdout 개선 전 평가(2026-10-26, 커밋 9d44bd4)의 오류 분석 결과 반영.

1. 국제 표기 휴대전화 '+82 10-0971-7912' (25건 → 계좌로 라벨 오류)
   v2에는 +82 패턴이 없어, 뒤쪽 '10-0971-7912'를 계좌(숫자-숫자-숫자)가 먼저 잡음
   → 계좌보다 먼저 국제번호 패턴 적용
2. 주소 앞 우편번호 '(05299) 서울특별시 …', 뒤 법정동 '… 502호 (범어동)' (13건 경계 오류)
   → 주소 패턴에 앞 '(5자리) ', 뒤 ' (○○동)' 선택적으로 포함
"""
import re

from detection.rule import regex_detector as v1
from detection.rule.regex_detector_v2 import ACCOUNT_V2, RegexDetectorV2

PHONE_INTL = re.compile(
    r"\+82[-.\s]?(?:\(0\)\s?)?(?:1[016789]|2|[3-6][1-5])[-.\s]?\d{3,4}[-.\s]?\d{4}" + v1.E)

ADDRESS_V3 = re.compile(
    rf"(?:\(\d{{5}}\)\s?)?(?:{v1.SIDO})\s+(?:[가-힣]+(?:시|군|구)\s+){{1,2}}[가-힣0-9]+(?:로|길)\s?\d+(?:-\d+)?"
    r"(?:\s?,\s?(?:[0-9A-Za-z가-힣]+동\s?)?\d+(?:호|층))?"
    r"(?:\s?\([가-힣0-9]+(?:동|가|리|읍|면)\))?"
)

PATTERNS_V3 = []
for lab, pat in v1.PATTERNS:
    if lab == "account_number":
        PATTERNS_V3.append(("phone_number", PHONE_INTL))   # 계좌보다 먼저
        PATTERNS_V3.append((lab, ACCOUNT_V2))
    elif lab == "address":
        PATTERNS_V3.append((lab, ADDRESS_V3))
    else:
        PATTERNS_V3.append((lab, pat))


class RegexDetectorV3(RegexDetectorV2):
    """v2의 제외 규칙(날짜·문서번호·대표번호 등)은 그대로, 패턴 목록만 v3로 교체."""
    name = "regex_v3"
    patterns = PATTERNS_V3
