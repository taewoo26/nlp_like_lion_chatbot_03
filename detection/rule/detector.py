"""규칙 기반 탐지기."""
import re
from detection.base import BaseDetector, Span

# Kiwi 형태소 분석기 (선택 의존성)
# 없어도 동작하지만 "한결같이" 같은 형용사·부사 오탐이 발생할 수 있음
try:
    from kiwipiepy import Kiwi as _Kiwi
    _kiwi: "_Kiwi | None" = _Kiwi()
except ImportError:
    _kiwi = None

# ── 정규식 상수 ──────────────────────────────────────────────

# [인명] 보조 상수
# 복합성씨(남궁·황보·선우)를 단일 성씨보다 앞에 써야 우선 매칭됨
_SURNAMES = (
    r"(?:남궁|황보|선우|"
    r"김|이|박|최|정|강|조|윤|장|임|한|오|서|신|권|황|안|송|류|전|홍|고|문|양|손|배|백|"
    r"허|유|남|심|노|하|곽|성|차|주|우|구|민|나|진|지|엄|채|원|천|방|공|현|변|염|석|선|"
    r"설|마|길|함|연|육|표|명|기|반|라|왕|금|옥|인|맹|제|모|탁|편|봉|복)"
)
_SINGLE_SURNAMES = (   # spaced 전용: 복합성씨는 full로만 처리
    r"(?:김|이|박|최|정|강|조|윤|장|임|한|오|서|신|권|황|안|송|류|전|홍|고|문|양|손|배|백|"
    r"허|유|남|심|노|하|곽|성|차|주|우|구|민|나|진|지|엄|채|원|천|방|공|현|변|염|석|선|"
    r"설|마|길|함|연|육|표|명|기|반|라|왕|금|옥|인|맹|제|모|탁|편|봉|복)"
)
_TITLE   = r"(?:팀장|부장|대리|과장|차장|사원|책임|매니저|주임|이사|원장)"
_ORG_SFX = r"(?:시스템즈|솔루션|보안|은행|카드|센터|지점|팀|부)"
# 이름 자체에 org suffix가 포함되면 오탐 → _find_full에서 span text를 확인
_ORG_SFX_TAIL_RE = re.compile(r"(?:센터|지점|은행|카드|시스템즈|솔루션|보안|팀|부)$")
_TITLE_NIM_RE = re.compile(r"님|씨|(?:팀장|부장|대리|과장|차장|사원|책임|매니저|주임|이사|원장)")
# 이름 경계 lookahead — 이름 뒤에 조사·호칭이 오거나 비한글·끝이면 이름으로 인정
_JOSA = (
    r"(?:이랑|이다|이에요|이고|이면|이라|입니다|께서|씩|들|님|씨|아|야|랑"
    r"|은|는|이|가|을|를|의|도|로|에서|에게|와|과|한테)"
)

# full: 성(1~2글자) + 이름(2~3글자) 붙여쓰기
# {2,3}? lazy:
#   - 최소 2글자 → "오"(성씨)+늘(1글자) 오탐 방지
#   - greedy 방지 → "이서연이 복귀"에서 "이서연이" 전체 캡처 방지
# (?<![면고]) → "복귀하면" 등 동사 어미로 끝나는 오탐 방지
PERSON_FULL_RE = re.compile(
    r"(?<![가-힣])"
    + _SURNAMES
    + r"(?!\s*" + _TITLE + r")"        # 성+공백+직함 제외 (허 부장님 등)
    + r"[가-힣]{2,3}?"                 # 이름 2~3글자 (lazy)
    + r"(?<![면고])"                   # 동사 어미(하면·하고) 끝 제외
    + r"(?!" + _ORG_SFX + r")"         # 기업·조직 suffix 제외
    + r"(?=" + _JOSA + r"|[^가-힣]|$)" # 경계: 조사·비한글·끝
)

# spaced: 성(1글자) + 공백 1개 + 이름(2글자)
PERSON_SPACED_RE = re.compile(
    r"(?<![가-힣])"
    + _SINGLE_SURNAMES
    + r" "
    + r"(?!" + _TITLE + r")"
    + r"[가-힣]{2}"
    + r"(?!" + _ORG_SFX + r")"
    + r"(?=" + _JOSA + r"|[^가-힣]|$)"
)

# given: 이름(2글자) + 호칭 — 호칭은 lookahead라 매칭 범위에 포함되지 않음
# 씨는 앞에 공백을 허용 ("소율 씨가" → "소율")
PERSON_GIVEN_RE = re.compile(
    r"(?<![가-힣])"
    r"[가-힣]{2}"
    r"(?=\s?씨|아|야|이랑|랑)"
)

# 이메일: 아이디(점·언더스코어 포함) @ 도메인(ASCII만) . TLD(ASCII만)
# 주의: 파이썬 \w는 한글을 포함하므로 도메인·TLD는 [A-Za-z0-9.-] / [A-Za-z]로 제한
EMAIL_RE = re.compile(
    r"[A-Za-z0-9._%+\-]+"   # 아이디
    r"@"
    r"[A-Za-z0-9.\-]+"      # 도메인 (ASCII만)
    r"\.[A-Za-z]+"           # TLD — 한글 조사 "로/으로" 침범 방지
)

# 주민등록번호: YYMMDD-NNNNNNN
# 월(01-12)·일(01-31) 유효성 확인. 체크섬 검증 금지 (dev 전부 불일치).
RRN_RE = re.compile(
    r"(?<!\d)"
    r"\d{2}"                          # YY
    r"(?:0[1-9]|1[0-2])"             # MM (01-12)
    r"(?:0[1-9]|[12]\d|3[01])"       # DD (01-31)
    r"-"
    r"\d{7}"                          # 뒷자리 7자리
    r"(?!\d)"
)

# 카드번호: 4-4-4-4 (하이픈만, dev 전부 이 형식)
# 주의: 4-4 조각이 전화번호 패턴과 겹칠 수 있으므로 전화보다 먼저 적용
CARD_RE = re.compile(
    r"(?<!\d)\d{4}-\d{4}-\d{4}-\d{4}(?!\d)"
)

# 전화번호: 010 또는 02 + 구분자(-, ., 공백, ·) + 번호
# 제외: 대표번호(1599- 등)는 010/02로 시작하지 않아 자동 제외
#       날짜(2026-xx-xx)는 010/02로 시작하지 않아 자동 제외
#       내선 4자리는 자릿수 부족으로 자동 제외
#       주민번호 앞자리 010XX는 RRN 적용 후 겹침 방지로 처리
PHONE_RE = re.compile(
    r"(?<!\d)"
    r"(?:010|02)"               # 휴대전화(010) 또는 서울(02)
    r"[-.\s·]?"                 # 구분자 (없어도 됨)
    r"\d{3,4}"                  # 국번 3-4자리
    r"[-.\s·]?"                 # 구분자
    r"\d{4}"                    # 번호 4자리
    r"(?!\d)"
)

# 계좌번호: 3-6-2 (하이픈만, dev 전부 이 형식)
# 주의: 하이픈 제거 시 11자리라 전화번호와 겹칠 수 있음 → 전화보다 나중에 적용
ACCOUNT_RE = re.compile(
    r"(?<!\d)\d{3}-\d{6}-\d{2}(?!\d)"
)

# 주소 시·도 시작 패턴 (내부 보조 상수)
_SIDO = (
    r"(?:서울특별시|부산광역시|대구광역시|인천광역시|광주광역시|대전광역시|울산광역시"
    r"|세종특별자치시|경기도|강원도|충청북도|충청남도|전라북도|전라남도|경상북도|경상남도|제주특별자치도)"
)
# 주소 끝 패턴: 동+호 / 호 / 층 (이 순서로 시도해야 101동 1202호가 통째로 잡힘)
_ADDR_END = r"(?:\d+동\s*\d+호|\d+호|\d+층)"

# 주소: 시·도로 시작, 동호·호·층으로 끝
# [^→]+  greedy + _ADDR_END backtracking = 가장 마지막 호·층을 끝 경계로 선택
#   → 1호선길처럼 도로명 속 숫자+호 가 먼저 잡히는 오탐 방지
#   → [^→] 는 화살표(→) 직전에서 절단 → 동일 문장 내 두 주소를 별개 엔티티로 반환
#   → 끝 뒤에 붙는 로/으로/에서/) 는 패턴 범위 밖이라 자동 제외
ADDRESS_RE = re.compile(_SIDO + r"[^→]+" + _ADDR_END)


class RuleDetector(BaseDetector):
    name = "rules"

    # ── 개별 탐지 함수 ──────────────────────────────────────────

    def find_email(self, text: str) -> list[Span]:
        """이메일 주소를 탐지한다. 뒤에 붙은 조사·괄호는 경계에 포함하지 않는다."""
        return [
            Span(start=m.start(), end=m.end(), label="email", text=m.group())
            for m in EMAIL_RE.finditer(text)
        ]

    def find_phone(self, text: str) -> list[Span]:
        """전화번호(010, 02)를 탐지한다. 7가지 구분자 형식 모두 지원."""
        return [
            Span(start=m.start(), end=m.end(), label="phone_number", text=m.group())
            for m in PHONE_RE.finditer(text)
        ]

    def find_resident_registration_number(self, text: str) -> list[Span]:
        """주민등록번호(YYMMDD-NNNNNNN)를 탐지한다. 월·일 유효성 확인."""
        return [
            Span(start=m.start(), end=m.end(), label="resident_registration_number", text=m.group())
            for m in RRN_RE.finditer(text)
        ]

    def find_account_number(self, text: str) -> list[Span]:
        """계좌번호(3-6-2 형식)를 탐지한다."""
        return [
            Span(start=m.start(), end=m.end(), label="account_number", text=m.group())
            for m in ACCOUNT_RE.finditer(text)
        ]

    def find_card_number(self, text: str) -> list[Span]:
        """카드번호(4-4-4-4 형식)를 탐지한다."""
        return [
            Span(start=m.start(), end=m.end(), label="card_number", text=m.group())
            for m in CARD_RE.finditer(text)
        ]

    def find_address(self, text: str) -> list[Span]:
        """주소(시·도 ~ 호·층)를 탐지한다.
        화살표(→)로 구분된 경우 별개 엔티티 2개로 반환한다."""
        return [
            Span(start=m.start(), end=m.end(), label="address", text=m.group())
            for m in ADDRESS_RE.finditer(text)
        ]

    # ── 인명 내부 탐지 함수 ──────────────────────────────────────

    # 이름으로 시작할 수 없는 태그 — 해당 위치에서 시작하는 후보는 제외
    # NNG(일반명사), NNB(의존명사), VV*(동사), VA*(형용사), VX*(보조동사), XR(어근),
    # MM(관형사: 전·연 등), XPN(접두사: 신- 등)
    # Kiwi는 VV-R 등 복합 태그를 쓰므로 prefix 2글자로 비교
    _BLOCK_PREFIXES = frozenset({"VV", "VA", "VX"})
    # XPN(신-) 제외: 성씨 "신"이 XPN으로 분류되는 경우 블록하면 신승현 등 미탐
    _BLOCK_FULL = frozenset({"NNG", "NNB", "XR", "MM"})

    @staticmethod
    def _kiwi_analysis(text: str) -> dict:
        """Kiwi 분석 결과를 세 집합으로 반환한다.
        mag          — MAG(부사) 구간: 후보 전체가 포함되면 제외
        josa_starts  — 조사(J*) 토큰의 시작 위치: spaced 후보의 성씨가 조사면 제외
        block_starts — _BLOCK_TAGS 토큰의 시작 위치: 후보 시작이 해당 태그이면 이름 아님"""
        if _kiwi is None:
            return {"mag": set(), "josa_starts": set(), "block_starts": set()}
        mag: set[tuple[int, int]] = set()
        josa_starts: set[int] = set()
        block_starts: set[int] = set()
        for tok in _kiwi.tokenize(text):
            if tok.tag == "MAG":
                mag.add((tok.start, tok.start + tok.len))
            elif tok.tag.startswith("J"):   # JKS·JKO·JX·JC·JKB 등 조사 전체
                josa_starts.add(tok.start)
            elif (tok.tag in RuleDetector._BLOCK_FULL
                  or tok.tag[:2] in RuleDetector._BLOCK_PREFIXES):
                block_starts.add(tok.start)
        return {"mag": mag, "josa_starts": josa_starts, "block_starts": block_starts}

    @staticmethod
    def _in_mag(start: int, end: int, mag: set[tuple[int, int]]) -> bool:
        """후보 [start:end] 가 하나의 MAG 구간에 완전히 포함되면 True."""
        return any(ms <= start and me >= end for ms, me in mag)

    def _find_full(self, text: str, kw: dict) -> list[Span]:
        spans = []
        for m in PERSON_FULL_RE.finditer(text):
            if self._in_mag(m.start(), m.end(), kw["mag"]):
                continue
            if _ORG_SFX_TAIL_RE.search(m.group()):
                continue  # 이름 자체에 org suffix 포함 ("고객센터" 등)
            if m.start() in kw["block_starts"]:
                # block됐어도 아래 신호 중 하나면 이름으로 허용
                after = text[m.end():m.end() + 5].lstrip()
                prefix = text[max(0, m.start() - 3):m.start()]
                if not (
                    after.startswith("】")             # 【이름】 패턴
                    or after.startswith("(")           # 이름(조직명) 패턴
                    or _TITLE_NIM_RE.match(after)      # 뒤에 직함·님·씨
                    or any(c in prefix for c in (",", "\n"))  # 목록·줄바꿈 뒤
                ):
                    continue
            spans.append(Span(start=m.start(), end=m.end(), label="person", text=m.group()))
        return spans

    def _find_spaced(self, text: str, kw: dict) -> list[Span]:
        spans = []
        for m in PERSON_SPACED_RE.finditer(text):
            start, end = m.start(), m.end()
            # 성씨 위치가 조사(이·가·을 등)면 사람 이름이 아님 (예: "…이 확인")
            if start in kw["josa_starts"]:
                continue
            if self._in_mag(start, end, kw["mag"]):
                continue
            if start in kw["block_starts"]:
                # block됐어도 【이름】 패턴이거나 뒤에 님/씨/직함이 오면 허용
                after = text[end:end + 5].lstrip()
                if not (after.startswith("】") or _TITLE_NIM_RE.match(after)):
                    continue
            spans.append(Span(start=start, end=end, label="person", text=m.group()))
        return spans

    def _find_given(self, text: str, kw: dict) -> list[Span]:
        spans = []
        for m in PERSON_GIVEN_RE.finditer(text):
            if self._in_mag(m.start(), m.end(), kw["mag"]):
                continue
            if m.start() in kw["block_starts"]:
                # "씨" 앞에서만 block_starts 예외 허용 (씨는 이름 신호가 매우 강함)
                # "아"/"야" 앞에서는 "센터야", "고객아" 등 오탐 방지를 위해 블록 유지
                after = text[m.end():m.end() + 2].lstrip()
                if not after.startswith("씨"):
                    continue
            spans.append(Span(start=m.start(), end=m.end(), label="person", text=m.group()))
        return spans

    def find_person(self, text: str) -> list[Span]:
        """이름을 탐지한다. full → spaced → given 순서로 수집하고 Kiwi 필터 적용."""
        kw = self._kiwi_analysis(text)
        return self._find_full(text, kw) + self._find_spaced(text, kw) + self._find_given(text, kw)

    # ── 겹침 방지 헬퍼 ──────────────────────────────────────────

    @staticmethod
    def _no_overlap(start: int, end: int, taken: list[Span]) -> bool:
        """이미 확정된 구간과 겹치지 않으면 True."""
        return all(end <= s.start or start >= s.end for s in taken)

    # ── 메인 탐지 ────────────────────────────────────────────────

    def detect(self, text: str) -> list[Span]:
        """우선순위 순서대로 탐지하고, 겹치는 구간은 먼저 잡힌 쪽을 우선한다.

        적용 순서:
          1. find_resident_registration_number  — 010XX 앞자리가 전화번호로 오인되는 것 방지
          2. find_card_number                   — 4-4 조각이 전화 패턴에 잡히기 전에 처리
          3. find_phone
          4. find_account_number                — 11자리 겹침 방지를 위해 전화 뒤에 처리
          5. find_email
          6. find_address
          7. find_person                        — 마지막 적용으로 숫자·이메일과 겹침 방지
        """
        candidates = (
            self.find_resident_registration_number(text)
            + self.find_card_number(text)
            + self.find_phone(text)
            + self.find_account_number(text)
            + self.find_email(text)
            + self.find_address(text)
            + self.find_person(text)
        )

        # 겹침 제거: 리스트 앞쪽(우선순위 높음)이 이미 점유한 구간은 건너뜀
        taken: list[Span] = []
        for span in candidates:
            if self._no_overlap(span.start, span.end, taken):
                taken.append(span)

        return self.postprocess(text, taken)
