"""split_v2 정책용 보강 규칙 (팀 원본 split·split_clean은 그대로 둔다).

근거: holdout(개선 전 기록 후 개선용으로 전환)·F셋 오류 분석 (2026-10-27)
  - 번호류 미탐: 주민 30·계좌 32·카드 17·전화 25 (holdout) / 계좌 15·전화 12·카드 11·주민 6 (F)
    → 팀 정규식이 하이픈 정형 한 가지만 알고, split은 모델 번호를 버리기 때문
  - 주소: 약칭(서울·부산)·지번·쉼표 없는 동/호·아파트명·우편번호·(법정동) 미지원 → 경계·라벨 오류 46
  - 이름: '시우 드림'·'박별이 입원' 경계, '대상 고객: 전 찬' 미탐, 외국인 이름('마이클 존슨') 잘림

원칙: 특정 값·문서에 맞춘 규칙이 아니라 '형식' 규칙만 쓴다.
"""
import re
from functools import lru_cache

from detection.base import Span
from detection.rule.kiwi_filter import _get_kiwi
from detection.rule.names import NOT_GIVEN, SURNAME_PAT, TITLES, has_strong_cue

@lru_cache(maxsize=512)
def _tokenize(text):
    """Kiwi 분석 결과를 같은 글(문서·줄)마다 한 번만 계산해서 재사용 (속도 개선, 결과는 동일)."""
    return tuple(_get_kiwi().tokenize(text))


D = r"(?<![\d])"           # 앞에 숫자 없음
E = r"(?![\d])"            # 뒤에 숫자 없음
SEP = r"[-.\s]"            # 번호 구분자: 하이픈·점·공백 1개

# ---------------------------------------------------------------- 번호
# 주민·외국인등록번호: 앞 6자리가 실제 날짜, 뒷자리 첫 숫자 1~8 (13자리 계좌와 구별)
RRN = re.compile(rf"{D}(\d{{2}})(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])[-.\s]?[1-8]\d{{6}}{E}")

# 카드: 4-4-4-4 (구분자 섞임 허용) / 15자리 4-6-5
CARD16 = re.compile(rf"{D}\d{{4}}(?:{SEP}?\d{{4}}){{3}}{E}")
CARD15 = re.compile(rf"{D}\d{{4}}{SEP}\d{{6}}{SEP}\d{{5}}{E}")

# 전화
PHONE_PATTERNS = [
    # 국가번호: +82 10-1234-5678 / +82 (0)10 1234 5678 / +1-212-555-0123 / +86 138 0013 8000
    re.compile(r"\+\d{1,3}[-\s]?(?:\(0\)\s?)?\d{1,4}(?:[-.\s]\d{2,4}){1,3}(?![\d])"),
    # 82-10-1234-5678 (+ 없이 국가번호)
    re.compile(rf"(?<![\d+]){'82'}[-\s]1[016789][-\s]?\d{{3,4}}[-\s]?\d{{4}}{E}"),
    # 괄호 지역번호: (010)1234-5678 / (02) 345-6789
    re.compile(rf"\(0\d{{1,2}}\)\s?\d{{3,4}}{SEP}?\d{{4}}{E}"),
    # 휴대전화·유선·인터넷전화 (구분자 섞임 허용)
    re.compile(rf"(?<![\d+]){'01'}[016789]{SEP}?\d{{3,4}}{SEP}?\d{{4}}{E}"),
    re.compile(rf"(?<![\d+])0(?:2|[3-6][1-5]|70){SEP}?\d{{3,4}}{SEP}?\d{{4}}{E}"),
]

# 계좌: 숫자 묶음 2~4개(하이픈·공백), 숫자 합계 10~14자리
ACCOUNT_GROUPED = re.compile(r"(?<![\d+\-.:/])\d{2,7}(?:[-\s]\d{1,7}){1,3}(?![\d\-:/]|\.\d)")
DATE_START = re.compile(r"^(?:19|20)\d{2}[-.\s]\d{1,2}[-.\s]\d{1,2}")   # 2026-10-22 … 로 시작하면 날짜
# 계좌: 구분자 없는 10~14자리 → 앞에 계좌 문맥이 있을 때만
ACCOUNT_PLAIN = re.compile(rf"(?<![\d+\-.])\d{{10,14}}(?![\d\-]|\.\d)")   # 문장 끝 마침표는 허용
ACCOUNT_CUE = re.compile(r"(?:계좌|은행|입금|출금|환불|예금|account|Account|ACCOUNT|송금|이체)[^\n]{0,15}$")
BIZ_NO = re.compile(r"^\d{3}-\d{2}-\d{5}$")          # 사업자등록번호 (비대상)
ID_CUE = re.compile(r"(?:주문|접수|승인|문서|민원|결재|관리|송장|운송장|회원|사원|식별|증권|계약|신청|가맹점)\s?(?:번호|No\.?|ID)\s*[:：]?\s*$")

# ---------------------------------------------------------------- 주소
SIDO = (r"(?:서울특별시|서울시|서울|부산광역시|부산시|부산|대구광역시|대구시|대구|인천광역시|인천시|인천|"
        r"광주광역시|광주시|광주|대전광역시|대전시|대전|울산광역시|울산시|울산|세종특별자치시|세종시|세종|"
        r"경기도|경기|강원특별자치도|강원도|강원|충청북도|충북|충청남도|충남|전북특별자치도|전라북도|전북|"
        r"전라남도|전남|경상북도|경북|경상남도|경남|제주특별자치도|제주도|제주)")
ADDRESS = re.compile(
    rf"(?<![가-힣]){SIDO}"                                         # 우편번호는 span에서 제외 (AD-05)
    r"(?:\s[가-힣]+(?:시|군|구)){1,3}"                            # 성남시 분당구
    r"(?:"
    r"(?:\s[가-힣]+(?:읍|면))?\s[가-힣0-9]+(?:로|길)(?:\s?\d+번?길)?\s\d+(?:-\d+)?"   # 도로명 + 건물번호
    r"|(?:\s[가-힣]+(?:읍|면))?\s[가-힣0-9]+(?:동|리|가)\s\d+(?:-\d+)?(?:번지)?"      # 지번
    r"|\s[가-힣]+(?:읍|면)\s\d+(?:-\d+)?(?:번지)?"                                    # 배방읍 727-4
    r")"
    r"(?:,?\s(?:[가-힣A-Za-z]+(?:아파트|빌라|오피스텔|타워|맨션|빌딩|하우스)\s)?"      # 아파트 이름
    r"(?:[A-Za-z0-9]+동\s?)?(?:지하\s?|B)?\d+(?:호|층))?"            # 104동 1101호 / B동 5층 / B동 지하1층 / 302호
    r"(?:\s?\([가-힣0-9]+(?:동|가|리|읍|면)\))?"                   # (범어동)
)

# ---------------------------------------------------------------- 이름
NAME_CUE = re.compile(
    r"(?:고객명|고객 성명|성명|이름|성함|예금주|수취인|수령인|신청인|민원인|가입자|보호자|대리인|"
    r"담당자|작성자|접수자|대상 고객|Full Name|Name)(?:\([^)\n]{1,6}\))?\s*[:：]\s*"
)
CUE_VALUE = re.compile(rf"(?:{SURNAME_PAT})(?:\s[가-힣]{{1,2}}|[가-힣]{{1,3}})(?![가-힣])")
KIN_EVENTS = {"조모상", "조부상", "부친상", "모친상", "장인상", "장모상", "빙부상", "빙모상",
              "시부상", "시모상", "본인상", "배우자상", "형제상", "자매상", "자녀상", "외조모상", "외조부상"}
FORM_LABELS = {"고객명", "성명", "이름", "성함", "성함이", "배송지", "수령지", "청구지", "소명자", "신청인", "수취인",
               "예금주", "보정자", "담당자", "대상", "직통", "사번", "동호수", "반송일자", "정산월", "신청건"}
ROLE_WORDS = {"채무자", "채권자", "피보험자", "계약자", "보증인", "주회원", "가족회원", "본인회원", "가맹점",
              "대리인", "신청인", "민원인", "수취인", "예금주", "고객", "회원"}
NAME_CUE_END = re.compile(NAME_CUE.pattern + "$")
HONOR_TAIL = re.compile(r"(?:\s?(?:드림|올림|고객님|님|씨))+$")


def _overlaps(a, b):
    return a.start < b.end and b.start < a.end


def _digits(s):
    return sum(c.isdigit() for c in s)


def _add(spans, s, e, label, text):
    new = Span(s, e, label, text[s:e])
    if any(_overlaps(new, o) for o in spans):
        return
    spans.append(new)


# ---------------------------------------------------------------- 정규화 (COM-04·COM-07·PH-09·PH-10)
FULLWIDTH = {chr(0xFF10 + i): str(i) for i in range(10)}
FULLWIDTH.update({"－": "-", "．": ".", "（": "(", "）": ")", "＋": "+", "　": " ", "＠": "@",
                  "–": "-", "—": "-", "‐": "-", "‑": "-", "ㅡ": "-"})
KOR_DIGIT = {"공": "0", "영": "0", "일": "1", "이": "2", "삼": "3", "사": "4", "오": "5", "육": "6", "칠": "7", "팔": "8", "구": "9"}
KOR_DIGIT_RUN = re.compile(r"[공영일이삼사오육칠팔구](?:[\s\-]?[공영일이삼사오육칠팔구]){8,}")
OCR_TOKEN = re.compile(r"[0-9OolI][0-9OolI\-.]*[0-9OolI]")


def normalize(text):
    """규칙 탐지용 정규화. 글자 수를 바꾸지 않아서 위치가 원문과 그대로 맞는다.
    - 전각 숫자·기호, 여러 종류의 하이픈 → 반각
    - 한글 숫자 9자리 이상 연속(공일공 일이삼사 오육칠팔) → 숫자
    - 숫자 덩어리 안에 섞인 OCR 혼동 글자(O·o → 0, l·I → 1) → 숫자 (숫자 6개 이상, 글자가 숫자의 절반 이하)"""
    chars = [FULLWIDTH.get(c, c) for c in text]
    t = "".join(chars)
    for m in KOR_DIGIT_RUN.finditer(t):
        for i in range(m.start(), m.end()):
            chars[i] = KOR_DIGIT.get(chars[i], chars[i])
    t = "".join(chars)
    for m in OCR_TOKEN.finditer(t):                     # 공백 없이 붙은 한 덩어리 안에서만 (O1O-0l23-4567)
        seg = m.group()
        n_dig = sum(c.isdigit() for c in seg)
        n_let = sum(c in "OolI" for c in seg)
        if n_let and n_dig >= 6 and n_let * 2 <= n_dig:
            for i in range(m.start(), m.end()):
                chars[i] = {"O": "0", "o": "0", "l": "1", "I": "1"}.get(chars[i], chars[i])
    return "".join(chars)


# 상세주소만 (AD-06): 110동 905호 / 푸른마을아파트 905호 / 해든빌라 3동 201호
ADDRESS_DETAIL = re.compile(
    r"(?<![가-힣0-9])(?:[가-힣A-Za-z0-9]+(?:아파트|빌라|오피스텔|맨션|타워)\s?)?(?:[A-Za-z0-9]{1,4}동\s?\d{1,4}호"
    r"|(?<=아파트)\s?\d{1,4}호|(?<=빌라)\s?\d{1,4}호|(?<=오피스텔)\s?\d{1,4}호)")
ADDRESS_DETAIL2 = re.compile(r"(?<![가-힣0-9])[가-힣A-Za-z0-9]+(?:아파트|빌라|오피스텔|맨션)\s?(?:\d{1,4}동\s?)?\d{1,4}호")
# 회피 표기 이메일 (EM-07): hong(at)example.com / hong at example dot com / hong 골뱅이 example 닷컴
EMAIL_AT = re.compile(
    r"(?<![A-Za-z0-9._%+-])[A-Za-z0-9._%+-]+\s?(?:\(at\)|\[at\]|\sat\s|\s?골뱅이\s?)\s?[A-Za-z0-9-]+"
    r"(?:\s?(?:\.|\(dot\)|\[dot\]|\sdot\s|\s?닷\s?)\s?(?:[A-Za-z0-9-]+|컴|넷))*"
    r"\s?(?:\.|\(dot\)|\[dot\]|\sdot\s|\s?닷\s?)\s?(?:com|net|org|kr|co|컴|넷)\b")
# 운전면허번호 (처리: 삭제) — 12-34-567890-12 / 서울 12-345678-90. 7개 라벨 밖이라 채점(P/R)에는 들어가지 않음
DRIVER = re.compile(r"(?<![\d-])(?:\d{2}-\d{2}-\d{6}-\d{2}"
                    r"|(?:서울|부산|경기|강원|충북|충남|전북|전남|경북|경남|제주|대구|인천|광주|대전|울산)\s?\d{2}-\d{6}-\d{2})(?![\d-])")
DRIVER_CUE = re.compile(r"(?:운전\s?면허|면허)[^\n]{0,10}$")   # 지역명 없는 12-34-567890-12 형식은 '면허' 문맥이 있을 때만


def extra_spans(text):
    """보강 규칙으로 찾은 구간 (순서: 운전면허 → 주민 → 카드 → 전화 → 계좌 → 주소 → 이메일 회피 표기 → 항목명 뒤 이름).
    번호 규칙은 정규화한 글(norm)에서 찾고, 결과 글자는 원문(text)에서 가져온다."""
    spans = []
    norm = normalize(text)
    for m in DRIVER.finditer(norm):
        if not m.group()[0].isdigit() or DRIVER_CUE.search(norm[max(0, m.start() - 15):m.start()]):
            _add(spans, m.start(), m.end(), "driver_license", text)
    for m in RRN.finditer(norm):
        _add(spans, m.start(), m.end(), "resident_registration_number", text)
    for pat in (CARD15, CARD16):
        for m in pat.finditer(norm):
            _add(spans, m.start(), m.end(), "card_number", text)
    for pat in PHONE_PATTERNS:
        for m in pat.finditer(norm):
            if 9 <= _digits(m.group()) <= 15:
                _add(spans, m.start(), m.end(), "phone_number", text)
    for m in ACCOUNT_GROUPED.finditer(norm):
        g = m.group().strip()
        if not 10 <= _digits(g) <= 14 or BIZ_NO.match(g) or DATE_START.match(g) \
                or ID_CUE.search(text[max(0, m.start() - 12):m.start()]):
            continue
        if (" " in g or len(re.split(r"[-\s]", g)) == 2) and not ACCOUNT_CUE.search(text[max(0, m.start() - 25):m.start()]):
            continue                                     # 공백 구분·2덩어리(2026-999147)는 계좌 문맥이 있을 때만
        _add(spans, m.start(), m.start() + len(g), "account_number", text)
    for m in ACCOUNT_PLAIN.finditer(norm):
        if ACCOUNT_CUE.search(text[max(0, m.start() - 25):m.start()]):
            _add(spans, m.start(), m.end(), "account_number", text)
    for m in ADDRESS.finditer(norm):
        _add(spans, m.start(), m.end(), "address", text)
    for pat in (ADDRESS_DETAIL2, ADDRESS_DETAIL):                 # 상세주소만 (AD-06)
        for m in pat.finditer(norm):
            _add(spans, m.start(), m.end(), "address", text)
    for m in EMAIL_AT.finditer(norm):                             # hong(at)example.com (EM-07)
        _add(spans, m.start(), m.end(), "email", text)
    for cue in NAME_CUE.finditer(text):
        m = CUE_VALUE.match(text, cue.end())
        if not m:
            continue
        val = m.group()
        given = val[1:].strip() if not val.startswith(("남궁", "황보", "제갈", "선우", "독고", "사공", "서문")) else val[2:].strip()
        if given in NOT_GIVEN or re.fullmatch(TITLES, given) or len(val.replace(" ", "")) < 2:
            continue
        if " " in val:   # '전 직원'처럼 띄운 뒤가 일반명사면 제외 ('전 찬'은 통과)
            toks = [t for t in _tokenize(given) if t.tag == "NNG" and t.len >= 2]
            if toks:
                continue
        _add(spans, m.start(), m.end(), "person", text)
    return spans


def merge_extra(rule_spans, extra):
    """보강 구간을 규칙 결과에 합친다. 겹치는 규칙 구간을 완전히 감싸면(더 길면) 보강 구간으로 교체."""
    spans = list(rule_spans)
    for e in extra:
        over = [o for o in spans if _overlaps(e, o)]
        if not over:
            spans.append(e)
        elif all(e.start <= o.start and o.end <= e.end for o in over) and (e.end - e.start) > max(o.end - o.start for o in over):
            spans = [o for o in spans if o not in over] + [e]
    return sorted(spans, key=lambda s: (s.start, s.end))


# ---------------------------------------------------------------- 모델 결과 거르기 (가볍게)
NUMBER_DIGITS = {"phone_number": (9, 15), "card_number": (15, 16),
                 "resident_registration_number": (13, 13), "account_number": (10, 14)}


def _line_view(text, s):
    ls = text.rfind("\n", 0, s.start) + 1
    le = text.find("\n", s.end)
    le = len(text) if le == -1 else le
    return [t for t in _tokenize(text[ls:le]) if ls + t.start < s.end and s.start < ls + t.start + t.len]


def _doc_view(text, s):
    return [t for t in _tokenize(text) if t.start < s.end and s.start < t.start + t.len]


def filter_model_v2(text, spans):
    """모델 결과 중 모양이 확실히 틀린 것만 뺀다 (재현율 우선 — split_clean보다 훨씬 느슨)."""
    out = []
    for s in spans:
        plain = s.text.replace(" ", "")
        if "*" in s.text:
            continue                                            # 가려진 값 (hong***@example.com)
        if s.label in NUMBER_DIGITS:
            lo, hi = NUMBER_DIGITS[s.label]
            if not lo <= _digits(s.text) <= hi or "*" in s.text:
                continue
            if s.label == "account_number" and BIZ_NO.match(s.text):
                continue
            if s.label == "resident_registration_number" and not RRN.fullmatch(s.text):
                continue                                        # 1234567890123 (날짜 아님) → 주민번호 아님
        elif s.label == "address":
            if not re.search(r"\d", s.text):                   # 번지·호수 없는 '은행', '송' 등
                continue
        elif s.label == "person":
            s = tidy_name(text, s)                              # '시우 드림'·'승현아'·'로운이' → 이름만 남기고 판단
            plain = s.text.replace(" ", "")
            if s.start > 0 and re.match(r"[가-힣]", text[s.start - 1]):
                continue                                        # 미르보안 → '보안'
            cue = has_strong_cue(text, s) or re.match(r"\s?(?:님|씨)", text[s.end:s.end + 2])
            label_before = NAME_CUE_END.search(text[max(0, s.start - 15):s.start])
            table_cell = re.search(r"\|\s*$", text[max(0, s.start - 3):s.start]) and re.match(r"\s*\|", text[s.end:s.end + 3])
            if len(plain) == 1 and not (cue or label_before or table_cell):
                continue                                        # 사, 다, 을 (단, '성명: 린', '| 린 |', '린 님'은 통과)
            if len(plain) <= 2 and not cue and any(t.tag.startswith(("VV", "VA"))
                                       for view in (_doc_view(text, s), _line_view(text, s)) for t in view):
                continue                                        # 달래
            if len(plain) >= 3 and " " not in s.text and not re.match(SURNAME_PAT, plain) and not cue:
                continue                                        # 미수신, 미르보안 (띄어 쓴 외국인 이름 '응우옌 반 안'은 통과)
        out.append(s)
    return out


# ---------------------------------------------------------------- split_v3: 두 모델(KoELECTRA + Veil) 합치기
def _name_evidence(text, s):
    """모델 판단 말고 문맥 근거가 있는가: 뒤 호칭·직함, 앞 항목명(성명: 등), 표 칸."""
    cue = has_strong_cue(text, s) or re.match(r"\s?(?:님|씨)", text[s.end:s.end + 2])
    label_before = NAME_CUE_END.search(text[max(0, s.start - 15):s.start])
    table_cell = re.search(r"\|\s*$", text[max(0, s.start - 3):s.start]) and re.match(r"\s*\|", text[s.end:s.end + 3])
    return bool(cue or label_before or table_cell)


# 라벨별 담당 (dev·test·holdout 단독 측정 근거, 2026-10-27)
#   이름: KoELECTRA 재현율↑(.995~.999) · Veil 정밀도↑(.93~.96) → KoELECTRA 후보 + 애매하면 Veil 동의
#   주소: 둘 다 재현율 1.000, 정밀도 KoELECTRA↑(.93~1.00 vs .85~.89) → KoELECTRA만
#   전화·계좌·카드·주민: Veil 재현율·정밀도↑ (holdout 계좌 R .951/P 1.00 vs .854/.70) → Veil만 (+규칙)
#   이메일: 둘 다 1.000 → 규칙 + KoELECTRA
K_LABELS = {"person", "address", "email"}
V_LABELS = {"phone_number", "account_number", "card_number", "resident_registration_number"}


def combine_models(text, k_raw, v_raw):
    """KoELECTRA(k)와 Veil(v) 결과를 합친다.
    - 번호·주소: 두 모델 모두 모양 검사(filter_model_v2) 통과분을 합집합
    - 이름: KoELECTRA 통과분은 그대로 / 모양 검사에서 떨어진 KoELECTRA 이름은 Veil도 이름이라고 하면 살림
            Veil 이름은 KoELECTRA도 이름이라고 하거나(동의) 문맥 근거가 있을 때만 추가
    """
    k_ok = [x for x in filter_model_v2(text, k_raw) if x.label in K_LABELS]
    v_ok = [x for x in filter_model_v2(text, v_raw) if x.label in V_LABELS or x.label == "person"]
    v_person_raw = [x for x in v_raw if x.label == "person"]
    k_person_raw = [x for x in k_raw if x.label == "person"]

    out = list(k_ok)
    ok_keys = {(x.start, x.end) for x in k_ok if x.label == "person"}
    for k in k_person_raw:                                     # ① 모양 검사에서 떨어졌지만 Veil이 동의 → 살림
        t = tidy_name(text, k)
        if (t.start, t.end) in ok_keys or "*" in t.text:
            continue
        if any(_overlaps(t, v) for v in v_person_raw) and not any(_overlaps(t, o) for o in out):
            out.append(t)
    for v in v_ok:                                             # ② Veil 결과 추가
        if any(_overlaps(v, o) for o in out):
            continue
        if v.label != "person":
            out.append(v)                                      # 번호·주소는 모양 검사 통과분
        elif any(_overlaps(v, k) for k in k_person_raw) or _name_evidence(text, v):
            out.append(v)                                      # 이름은 동의 또는 문맥 근거
    return sorted(out, key=lambda x: (x.start, x.end))


DIGIT_RUN = re.compile(r"\d[\d\-.\s()+]{6,}\d")


def veil_chunks(text, k_raw):
    """Veil이 필요한 줄만 골라 [시작, 끝) 구간 목록으로 돌려준다 (속도 개선).
    Veil이 쓰이는 곳: 번호(전화·계좌·카드·주민) + 애매한 이름 확인 + 항목명 뒤 이름.
      - 숫자가 9자리 이상 이어지는 줄
      - KoELECTRA 이름 중 모양 검사에서 떨어진 이름이 있는 줄 (Veil 동의로 살릴 후보)
      - '성명:'·'수취인:' 같은 항목명이 있는 줄
    연속한 줄은 하나로 묶어 Veil 호출 횟수를 줄인다."""
    ok = {(x.start, x.end) for x in filter_model_v2(text, [k for k in k_raw if k.label == "person"])}
    dropped = [k for k in k_raw if k.label == "person" and (tidy_name(text, k).start, tidy_name(text, k).end) not in ok]
    lines, pos = [], 0
    for line in text.split("\n"):
        lines.append((pos, pos + len(line)))
        pos += len(line) + 1
    need = []
    for ls, le in lines:
        seg = text[ls:le]
        hit = any(sum(c.isdigit() for c in m.group()) >= 9 for m in DIGIT_RUN.finditer(seg)) \
            or NAME_CUE.search(seg) or any(ls <= k.start < le for k in dropped)
        need.append(bool(hit and seg.strip()))
    chunks, i = [], 0
    while i < len(lines):
        if not need[i]:
            i += 1
            continue
        j = i
        while j + 1 < len(lines) and need[j + 1]:
            j += 1
        chunks.append((lines[i][0], lines[j][1]))
        i = j + 1
    return chunks


def widen_addresses(spans, model_spans):
    """합친 결과의 주소를 모델이 완전히 감싸며 더 길게 잡았으면 모델 구간으로."""
    out = []
    for s in spans:
        if s.label == "address":
            for m in model_spans:
                if (m.label == "address" and m.start <= s.start and s.end <= m.end and (m.end - m.start) > (s.end - s.start)
                        and not any(_overlaps(m, o) for o in spans if o is not s)):
                    s = m
                    break
        out.append(s)
    return out


# ---------------------------------------------------------------- 이름 다듬기 (합친 뒤)
def widen_names(text, spans, model_spans):
    spans = _join_foreign(text, spans, model_spans)
    """규칙 이름을 모델 이름이 완전히 감싸고 더 길면 모델 구간으로 (마이클 → 마이클 존슨)."""
    out = []
    for s in spans:
        if s.label == "person":
            for m in model_spans:
                if (m.label == "person" and m.start == s.start and s.end < m.end and (m.end - m.start) > (s.end - s.start)
                        and not re.search(r"[\n,/|:;()]", m.text)
                        and not any(_overlaps(m, o) for o in spans if o is not s)
                        and not re.fullmatch(rf"\s?{TITLES}(?:님)?|\s?(?:님|씨|드림|고객님)", text[s.end:m.end])):
                    s = Span(m.start, m.end, "person", m.text)
                    break
        out.append(s)
    return out


def _batchim(ch):
    """한글 글자에 받침이 있는가 (조사 '이/은/을/과'는 받침 뒤, '가/는/를/와'는 받침 없는 글자 뒤에 붙음)."""
    return "가" <= ch <= "힣" and (ord(ch) - 0xAC00) % 28 != 0


ROLE_PREFIX = re.compile(r"^(?:채무자|채권자|피보험자|계약자|보증인|신청인|민원인|수취인|수령인|예금주|"
                         r"고객|방문자|대리인|담당자|작성자|가입자|"
                         r"[가-힣]+(?:보안|은행|카드|증권|보험|생명|캐피탈|솔루션|시스템|테크|컨설팅|법인))\s+")
SURNAME_BEFORE = re.compile(rf"(?:^|(?<=[^가-힣]))(?:{SURNAME_PAT}) $")
JOSA_START = "가는도랑를의와한에께만"


def _join_foreign(text, spans, model_spans):
    """'마리나' + ' ' + '페트로바'처럼 모델이 따로 잡은 두 단어를 하나의 이름으로 (뒤 단어가 성씨로 시작하지 않을 때)."""
    out = sorted(spans, key=lambda x: x.start)
    for m in model_spans:
        if m.label != "person" or not re.fullmatch(r"[가-힣]{2,5}", m.text) or re.match(SURNAME_PAT, m.text):
            continue
        for i, s in enumerate(out):
            if s.label == "person" and text[s.end:m.start] == " " and not any(_overlaps(m, o) for o in out):
                out[i] = Span(s.start, m.end, "person", text[s.start:m.end])
                break
    return out


def propagate_names(text, spans):
    """같은 문서에서 이름으로 확정된 1~2글자 이름이 다른 곳에 단독으로 나오면 그것도 이름 ('린 고객' → 표 안 '린')."""
    names = {s.text for s in spans if s.label == "person" and 1 <= len(s.text) <= 2}
    out = list(spans)
    for n in names:
        for m in re.finditer(rf"(?<![가-힣]){re.escape(n)}(?=(?:이|가|의|에게|님|씨|은|는)?(?![가-힣]))", text):
            new = Span(m.start(), m.end(), "person", n)
            if not any(_overlaps(new, o) for o in out):
                out.append(new)
    return sorted(out, key=lambda x: (x.start, x.end))


def tidy_name(text, s):
    """이름 앞뒤 정리 (한국어 조사 규칙 기준).
    - 호칭: '시우 드림' → '시우'          - 역할어: '채무자 제갈 현' → '제갈 현'
    - 조사: '박별이 입원'·'선우 진은'·'선우 진에(게)' → 이름만 ('별'·'진'에 받침이 있으니 조사)
    - 이름의 일부인 '이': '왕 리웨' + '이' → '왕 리웨이' ('웨'에 받침이 없어 '이'는 조사가 될 수 없음)
    """
    if s.label != "person":
        return s
    start, end = s.start, s.end
    m = HONOR_TAIL.search(text[start:end])
    if m and m.start() > 0:
        end = start + m.start()
    m = ROLE_PREFIX.match(text[start:end])
    if m and end - (start + m.end()) >= 2:
        start += m.end()
    trimmed = False
    for _ in range(2):
        core = text[start:end]
        nxt = text[end:end + 1]
        if not (len(core.replace(" ", "")) >= 3 and "가" <= core[-2] <= "힣"):
            break
        last, prev = core[-1], core[-2]
        particle = ((last in "은이을과아" and _batchim(prev)) or (last in "는가를와야" and not _batchim(prev))
                    or last in "에의")                    # 아·야 = 부르는 말 (지영아, 윤서야)
        if particle and re.match(rf"\s*(?:고객|님|씨|{TITLES})", text[end:end + 6]):
            break                                       # '이은아 고객'·'박별이 대리' → 이름의 일부로 둠
        if particle and (not re.match(r"[가-힣]", nxt) or (last == "에" and nxt == "게")
                         or (last == "이" and nxt in JOSA_START + "고며다라지었")):   # 서현이가·안이고요
            end -= 1
            trimmed = True
        else:
            break
    core = text[start:end]
    nxt = text[end:end + 1]
    if not trimmed and nxt == "이" and core and "가" <= core[-1] <= "힣" and not _batchim(core[-1]) \
            and re.match(r"(?:[^가-힣]|$|[의가는를에와도])", text[end + 1:end + 2] or " "):
        end += 1
    core = text[start:end]
    if len(core) == 2 and " " not in core and SURNAME_BEFORE.search(text[max(0, start - 3):start]):
        toks = _line_view(text, Span(start, end, "person", core))
        if not (len(toks) == 1 and toks[0].tag == "NNG"):                # '전 대상'은 제외
            start -= 2                                                   # 서윤 → 전 서윤
    if (start, end) == (s.start, s.end):
        return s
    return Span(start, end, "person", text[start:end])


def drop_name_words(spans):
    """경조사 용어·서식 항목명은 이름이 아님 (규칙·모델 어느 쪽에서 왔든)."""
    bad = KIN_EVENTS | FORM_LABELS | ROLE_WORDS
    return [s for s in spans if not (s.label == "person" and s.text.replace(" ", "") in bad) and "*" not in s.text]


# ---------------------------------------------------------------- 제외규칙 (규칙 정의서 ver0.2)
SHARED_MAIL = re.compile(r"^(?:privacy|help|helpdesk|support|info|admin|contact|cs|hr|noreply|no-reply|webmaster|"
                         r"service|customer|marketing|sales|team|office|recruit|notice|master|biz)(?:[._-][A-Za-z0-9._-]*)?@", re.I)
CORP_RRN_CUE = re.compile(r"(?:법인\s?등록\s?번호|법인\s?번호)\s*[:：]?\s*$")
CORP_ACCOUNT_CUE = re.compile(r"(?:수납\s?계좌|모\s?계좌|납부\s?계좌|법인\s?계좌)[^\n]{0,15}$")
ZIP_HEAD = re.compile(r"^\(?\d{5}\)?\s?")
MASK_CHARS = re.compile(r"[*xX＊]")


def apply_exclusions(text, spans):
    """규칙 정의서의 제외규칙을 마지막에 한 번 더 적용한다.
    COM-05 가려진 값(*, x, X) / EM-09 공용 메일 / RRN-13 법인등록번호 / AC-11 법인 수납계좌 / AD-05 우편번호 제외."""
    out = []
    for s in spans:
        before = text[max(0, s.start - 20):s.start]
        if s.label in NUMBER_DIGITS and MASK_CHARS.search(s.text):
            continue                                            # COM-05: 900101-5XXXXXX, 010-****-4567
        if s.label == "email" and SHARED_MAIL.match(s.text.replace(" ", "")):
            continue                                            # EM-09: privacy@, help@
        if s.label == "resident_registration_number" and CORP_RRN_CUE.search(before):
            continue                                            # RRN-13
        if s.label == "account_number" and CORP_ACCOUNT_CUE.search(before):
            continue                                            # AC-11
        if s.label == "address":
            m = ZIP_HEAD.match(s.text)
            if m and m.end() < len(s.text):                     # AD-05: (05299) 는 빼고 주소만
                s = Span(s.start + m.end(), s.end, "address", text[s.start + m.end():s.end])
        out.append(s)
    return out