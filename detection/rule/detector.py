"""규칙 기반 탐지기 (정규식). 이름(person)은 이후 Kiwi로 추가한다."""
import re

from detection.base import BaseDetector, Span
from detection.rule.names import detect_names


# 원문 → detector.py
#         ├─ 정규식 → email / 주민번호 / 카드 / 계좌 / 전화 / 주소
#         └─ names.py → 이름 후보(넓게)
#                         └─ (use_kiwi=True일 때만) kiwi_filter.py → 후보 걸러냄


# 앞에서부터 우선순위: 겹치면 먼저 나온 라벨이 이긴다
# (예: 주민번호 안의 숫자가 전화번호로 잡히는 것을 막는다)
PATTERNS = [
    # 이메일: \w 는 한글도 포함하므로 영문·숫자만 쓴다 (뒤에 붙은 조사 '로'가 딸려 오는 것 방지)
    ("email", r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+"),
    ("resident_registration_number", r"(?<!\d)\d{6}-\d{7}(?!\d)"),
    ("card_number", r"(?<!\d)\d{4}-\d{4}-\d{4}-\d{4}(?!\d)"),
    ("account_number", r"(?<!\d)\d{3}-\d{6}-\d{2}(?!\d)"),
    ("phone_number",
        r"(?<!\d)01[016789][-.\s]?\d{3,4}[-.\s]?\d{4}(?!\d)"
        r"|(?<!\d)02[-. ]?\d{3,4}[-. ]?\d{4}(?!\d)"
    ),
    # 주소는 이름 탐지보다 먼저 돌려야 함 (이름 후보가 주소 안에 들어있을 수 있음)
    ("address", 
        r"(?:서울특별시|부산광역시|대구광역시|인천광역시|광주광역시|대전광역시|울산광역시|세종특별자치시|"
        r"경기도|강원도|충청북도|충청남도|전라북도|전라남도|경상북도|경상남도|제주특별자치도)"
        r"(?:\s[가-힣]+시)?"          # 경기도 성남시 같은 중간 '시'
        r"\s[가-힣]+(?:구|군)"         # 연수구, 분당구, 일산동구
        r"\s[가-힣0-9]+(?:로|길)"      # 온누리로, 별빛길
        r"\s\d+(?:-\d+)?"              # 건물번호
        r"(?:,\s?(?:\d+동\s)?\d+(?:호|층))?"   # , 101동 1202호 / , 302호 / , 13층
        ),
]



class RuleDetector(BaseDetector):
    def __init__(self, use_kiwi: bool = False):
        self.use_kiwi = use_kiwi
        self.name = "rule_kiwi" if use_kiwi else "rule"

    def detect(self, text: str) -> list[Span]:
        spans: list[Span] = []
        for label, pat in PATTERNS:
            for m in re.finditer(pat, text):
                if any(m.start() < s.end and s.start < m.end() for s in spans):
                    continue    # 이미 잡힌 구간과 겹치면 버림
                spans.append(Span(m.start(), m.end(), label, m.group()))
        names = detect_names(text)            # 이름 1단계: 성씨 + 문맥 규칙
        if self.use_kiwi:                     # 이름 2단계: Kiwi 품사로 거르기
            from detection.rule.kiwi_filter import filter_names
            names = filter_names(text, names)
        for s in names:
            if not any(s.start < o.end and o.start < s.end for o in spans):
                spans.append(s)
        return sorted(spans, key=lambda s: s.start)
    