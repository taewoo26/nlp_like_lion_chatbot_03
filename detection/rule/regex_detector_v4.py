"""정규식 v4: 자체 검증셋 E(형식 변형 집중 셋) 오류 분석 반영.

1. 해외·국제 전화 '+1-212-555-0123', '+81-90-1234-5678', '+86 138 0013 8000', '82-10-0123-4567'
   v3는 +82만 알아서, 나머지는 계좌 패턴이 뒷부분('212-555-0123')을 계좌로 잡음 → 계좌보다 먼저 적용
2. 괄호 지역번호 '(010)0123-4567', '010)0123-4567'
3. 15자리 카드(4-6-5) '3782-822463-10005' → 계좌로 라벨 오류 → 계좌보다 먼저 카드로
4. 지번 주소 '서울특별시 마포구 상암동 1234-5' (도로명만 알던 v3가 놓침)
5. 사업자등록번호 형식(3-2-5, '123-81-99999')은 계좌가 아님
"""
import re

from detection.rule import regex_detector as v1
from detection.rule.regex_detector_v2 import ACCOUNT_V2, RegexDetectorV2
from detection.rule.regex_detector_v3 import ADDRESS_V3, PHONE_INTL

D, E = v1.D, v1.E
PHONE_INTL_ANY = re.compile(r"\+\d{1,3}[-.\s]?(?:\(0\)\s?)?\d{1,4}(?:[-.\s]\d{2,4}){1,3}" + E)
PHONE_82 = re.compile(r"(?<![\d+])82[-.\s]1[016789][-.\s]?\d{3,4}[-.\s]?\d{4}" + E)
PHONE_PAREN = re.compile(r"\(?0\d{1,2}\)\s?\d{3,4}[-.\s]?\d{4}" + E)
CARD_15 = re.compile(D + r"\d{4}[-\s]?\d{6}[-\s]?\d{5}" + E)
ADDRESS_JIBUN = re.compile(
    rf"(?:\(\d{{5}}\)\s?)?(?:{v1.SIDO})\s+(?:[가-힣]+(?:시|군|구)\s+){{1,2}}[가-힣0-9]+(?:동|읍|면|리|가)\s\d+(?:-\d+)?"
    r"(?:\s?,\s?(?:[0-9A-Za-z가-힣]+동\s?)?\d+(?:호|층))?"
)
BIZ_NO = re.compile(r"^\d{3}-\d{2}-\d{5}$")

PATTERNS_V4 = []
for lab, pat in v1.PATTERNS:
    if lab == "account_number":
        PATTERNS_V4 += [("card_number", CARD_15),
                        ("phone_number", PHONE_INTL), ("phone_number", PHONE_INTL_ANY),
                        ("phone_number", PHONE_82), ("phone_number", PHONE_PAREN),
                        (lab, ACCOUNT_V2)]
    elif lab == "address":
        PATTERNS_V4 += [(lab, ADDRESS_V3), (lab, ADDRESS_JIBUN)]
    else:
        PATTERNS_V4.append((lab, pat))


class RegexDetectorV4(RegexDetectorV2):
    name = "regex_v4"
    patterns = PATTERNS_V4

    def detect(self, text):
        return [s for s in super().detect(text)
                if not (s.label == "account_number" and BIZ_NO.match(s.text))]
