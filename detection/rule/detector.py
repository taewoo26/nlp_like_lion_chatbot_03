"""규칙 기반 탐지기 (정규식). 이름(person)은 이후 Kiwi로 추가한다."""
import re

from detection.base import BaseDetector, Span
from detection.rule.names import detect_names, has_strong_cue, trim_particle


# 02 및 광역 지역번호. 070은 인터넷전화 식별번호여서 이 규칙에 포함하지 않는다.
AREA_PHONE = (r"(?<!\d)(?:02|0(?:3[1-3]|4[1-4]|5[1-5]|6[1-4]))"
              r"[-. ]?\d{3,4}[-. ]?\d{4}(?!\d)")
# 국제 표기: +82 10-1234-5678, +82 2-123-4567
INTL_PHONE = r"(?<![\d+])\+82[-. ]?(?:1[016789]|2|[3-6]\d)[-. ]?\d{3,4}[-. ]?\d{4}(?!\d)"
PUBLIC_PHONE_CUE = re.compile(r"(?:대표\s*(?:번호|전화|연락처)?|고객센터|콜센터)\s*[:：]?\s*$")


def _is_public_phone(text: str, start: int) -> bool:
    """번호 바로 앞에 공용 대표번호 표시가 있으면 제외한다."""
    prefix = text[max(0, start - 20):start].split("\n")[-1]
    return PUBLIC_PHONE_CUE.search(prefix) is not None


# 앞에 '계좌'나 '은행'이 있는 숫자만 계좌로 본다 (민원번호 같은 일반 숫자와 구분)
ACCOUNT_CUE = re.compile(r"(?:계좌|은행)[^\n\d]{0,12}$")

RRN = r"(?<!\d)\d{6}[- ]?[1-8]\d{6}(?!\d)"
CARD = r"(?<!\d)\d{4}[- ]\d{4}[- ]\d{4}[- ]\d{4}(?!\d)|(?<!\d)\d{16}(?!\d)"
ACCOUNT_OLD = r"(?<!\d)\d{3}-\d{6}-\d{2}(?!\d)"
ACCOUNT_HYPHEN = r"(?<![\d-])\d{2,6}(?:-\d{1,7}){2,3}(?![\d-])"
ACCOUNT_PLAIN = r"(?<!\d)\d{11,14}(?!\d)"

SIDO = (r"(?:서울특별시|부산광역시|대구광역시|인천광역시|광주광역시|대전광역시|울산광역시|세종특별자치시|"
        r"경기도|강원도|충청북도|충청남도|전라북도|전라남도|경상북도|경상남도|제주특별자치도|"
        r"서울|부산|대구|인천|광주|대전|울산|세종|경기|강원|충북|충남|전북|전남|경북|경남|제주)")
ADDRESS = (
    r"(?:\(\d{5}\)\s?)?(?<![가-힣])" + SIDO +
    r"(?:\s[가-힣]+(?:시|군|구)){1,2}"
    r"(?:\s[가-힣0-9]+(?:로|길)(?:\s\d+번길)?\s\d+(?:-\d+)?"          # 도로명: 한빛로 174
    r"|\s[가-힣0-9]+(?:동|읍|면|리)\s\d+(?:-\d+)?)"                    # 지번: 잠실동 678-23
    r"(?:(?:,\s?|\s)(?:[가-힣0-9]+(?:아파트|빌라|맨션|타워|오피스텔)\s)?(?:\d+동\s?)?\d+(?:호|층))?"
    r"(?:\s\([가-힣0-9]+(?:동|가|리)\))?"                              # (범어동)
)


def _valid_rrn(text, m):
    """생년월일이 날짜가 아니면 주민번호가 아니다. 앞에 계좌 단서가 있으면 계좌로 넘긴다."""
    digits = re.sub(r"\D", "", m.group())
    if not (1 <= int(digits[2:4]) <= 12 and 1 <= int(digits[4:6]) <= 31):
        return False
    return ACCOUNT_CUE.search(text[max(0, m.start() - 30):m.start()]) is None


def _hyphen_account_ok(text, m):
    s = m.group()
    if re.fullmatch(r"0\d{1,2}-\d{3,4}-\d{4}", s):     # 070 등 전화번호 모양은 계좌가 아니다
        return False
    return 10 <= len(re.sub(r"\D", "", s)) <= 14


def _plain_account_ok(text, m):
    return ACCOUNT_CUE.search(text[max(0, m.start() - 30):m.start()]) is not None


# (라벨, 패턴, 추가 검사). 앞에서부터 우선순위: 겹치면 먼저 나온 라벨이 이긴다
PATTERNS = [
    # 이메일: \w 는 한글도 포함하므로 영문·숫자만 쓴다
    ("email", r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+", None),
    ("resident_registration_number", RRN, _valid_rrn),
    ("card_number", CARD, None),
    ("phone_number",
        r"(?<!\d)01[016789][-.\s]?\d{3,4}[-.\s]?\d{4}(?!\d)"
        r"|" + AREA_PHONE + r"|" + INTL_PHONE, None),
    ("account_number", ACCOUNT_OLD, None),
    ("account_number", ACCOUNT_HYPHEN, _hyphen_account_ok),
    ("account_number", ACCOUNT_PLAIN, _plain_account_ok),
    # 주소는 이름 탐지보다 먼저 돌려야 함 (이름 후보가 주소 안에 들어있을 수 있음)
    ("address", ADDRESS, None),
]



class RuleDetector(BaseDetector):
    def __init__(self, use_kiwi: bool = False):
        self.use_kiwi = use_kiwi
        self.name = "rule_kiwi" if use_kiwi else "rule"

    def detect(self, text: str) -> list[Span]:
        spans: list[Span] = []
        for label, pat, check in PATTERNS:
            for m in re.finditer(pat, text):
                if label == "phone_number" and _is_public_phone(text, m.start()):
                    continue
                if check is not None and not check(text, m):
                    continue
                if any(m.start() < s.end and s.start < m.end() for s in spans):
                    continue    # 이미 잡힌 구간과 겹치면 버림
                spans.append(Span(m.start(), m.end(), label, m.group()))

        cands = detect_names(text)            # 이름 1단계: 후보를 넓게 (반드시 if 문보다 먼저!)
        if self.use_kiwi:                     # 이름 2단계: Kiwi로 거르되, 강한 문맥 단서가 있으면 살린다
            from detection.rule.kiwi_filter import filter_names
            kept = filter_names(text, cands) + [s for s in cands if has_strong_cue(text, s)]
            names = list({(s.start, s.end): s for s in kept}.values())
        else:
            names = cands
        names = [trim_particle(text, s, cands) for s in names]   # 끝에 딸려 온 조사 떼기

        for s in names:
            if not any(s.start < o.end and o.start < s.end for o in spans):
                spans.append(s)
        return sorted(spans, key=lambda s: s.start)
