"""정규식 탐지기: 주민번호 · 카드 · 계좌 · 전화 · 이메일 · 주소.

설계 원칙 (라벨 가이드 2.2~2.7)
- 긴 번호부터 잡는다: 주민번호(13) → 카드(16) → 계좌 → 전화. 먼저 잡힌 구간은 다음 패턴이 못 씀
  (예: 주민번호 010621-4097209를 전화번호가 먼저 잡는 것 방지, D0058)
- 숫자 앞뒤에 다른 숫자가 붙어 있으면 매칭하지 않음 (민원번호 12자리 일부를 잡는 것 방지)
- 체크섬(주민번호 검증식, 카드 Luhn)은 쓰지 않음: dev 정답이 전부 검증식에 안 맞는 가상 값
- 특정 문서 문구(예: "대출금 입금 계좌:")에 기대지 않음. 문맥 단어는 '애매한 형식'에만 보조로 사용
"""
import re

from detection.base import BaseDetector, Span

D = r"(?<!\d)"          # 앞에 숫자 없음
E = r"(?!\d)"           # 뒤에 숫자 없음
SEP = r"[-.\s]?"        # 구분자: 하이픈 · 점 · 공백 · 없음

SIDO = (
    "서울특별시|부산광역시|대구광역시|인천광역시|광주광역시|대전광역시|울산광역시|세종특별자치시|"
    "경기도|강원특별자치도|강원도|충청북도|충청남도|전북특별자치도|전라북도|전라남도|경상북도|경상남도|"
    "제주특별자치도|서울|부산|대구|인천|광주|대전|울산|세종|경기|강원|충북|충남|전북|전남|경북|경남|제주"
)

PATTERNS: list[tuple[str, re.Pattern]] = [
    # 주민번호: 앞 6자리(생년월일) + 뒷자리 첫 숫자 1~8
    ("resident_registration_number",
     re.compile(D + r"\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\s?-\s?[1-8]\d{6}" + E)),
    # 카드: 4-4-4-4, 구분자는 하나로 통일된 경우만
    ("card_number", re.compile(D + r"\d{4}([-.\s]?)\d{4}\1\d{4}\1\d{4}" + E)),
    # 계좌: 3-6-2 같은 하이픈 3덩어리 (전화번호와 겹치지 않게 앞자리가 0으로 시작하는 10~11자리는 전화가 가져감)
    ("account_number", re.compile(D + r"\d{2,6}-\d{2,6}-\d{2,7}" + E)),
    # 휴대전화: 01X + 3~4 + 4
    ("phone_number", re.compile(D + r"01[016789]([-.\s]?)\d{3,4}\1\d{4}" + E)),
    # 지역번호 유선: 02, 0XX
    ("phone_number", re.compile(D + r"0(?:2|[3-6][1-5])([-.\s]?)\d{3,4}\1\d{4}" + E)),
    # 이메일: 도메인은 영문·숫자만 (파이썬 \w는 한글을 포함해 '.net로'까지 잡으므로 사용 금지)
    ("email", re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")),
    # 도로명 주소: 시·도 + (시·군·구 1~2개) + 도로명(로/길) + 건물번호 + (, 동 호 / 층)
    ("address", re.compile(
        rf"(?:{SIDO})\s+(?:[가-힣]+(?:시|군|구)\s+){{1,2}}[가-힣0-9]+(?:로|길)\s?\d+(?:-\d+)?"
        r"(?:\s?,\s?(?:\d+동\s?)?\d+(?:호|층))?"
    )),
]

# 계좌 패턴이 전화번호 모양을 잡지 않도록: 0으로 시작하는 2~3덩어리 10~11자리는 전화
PHONE_LIKE = re.compile(r"^0\d{1,2}-\d{3,4}-\d{4}$")
# 날짜(2026-06-25)는 계좌 패턴(\d{2,6}-\d{2,6}-\d{2,7})에 걸리므로 제외
DATE_LIKE = re.compile(r"^(19|20)\d{2}-\d{1,2}-\d{1,2}$")


class RegexDetector(BaseDetector):
    name = "regex"

    def detect(self, text: str) -> list[Span]:
        taken = [False] * len(text)
        spans: list[Span] = []
        for label, pat in PATTERNS:
            for m in pat.finditer(text):
                s, e = m.start(), m.end()
                val = m.group()
                if label == "account_number" and (PHONE_LIKE.match(val) or DATE_LIKE.match(val)):
                    continue
                if any(taken[s:e]):
                    continue
                for i in range(s, e):
                    taken[i] = True
                spans.append(Span(s, e, label, val))
        return spans
