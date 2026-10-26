"""조합 탐지기: 라벨별로 방식을 나눠 쓴다 (회사 안내 "라벨별 조합 가능, 비교표 인용").

근거 (dev·test 비교표)
- 번호·이메일·주소: 정규식이 dev·test 모두 1.000, 미끼 오탐 0
  ↔ Veil은 회사 대표번호 등 미끼 오탐이 많음 (test decoy_company_phone 33건)
- 이름: 규칙은 성 없는 이름에 약함 (test form=given 0.705) ↔ Veil은 1.000
  → 이름만 규칙 ∪ Veil (합집합)으로 서로 놓친 것을 메운다

버전
- hybrid      : 정규식(번호·이메일·주소) + 이름(규칙 ∪ Veil)
- hybrid_addr : 위 + 정규식이 못 잡은 주소를 Veil로 보완 (처음 보는 주소 형식 대비)

겹칠 때 우선순위: 정규식 > 규칙 이름 > Veil 이름 (규칙 쪽 경계가 더 정확했음)
"""
import re

from detection.base import BaseDetector, Span
from detection.rule.name_detector import NameDetector
from detection.rule.regex_detector import RegexDetector
from detection.token_cls.veil_detector import VeilDetector


def _free(sp: Span, taken: list[tuple[int, int]]) -> bool:
    return all(sp.end <= a or b <= sp.start for a, b in taken)


class HybridDetector(BaseDetector):
    name = "hybrid"
    veil_labels = {"person"}            # Veil에서 가져올 라벨

    def __init__(self):
        self.regex = RegexDetector()
        self.names = NameDetector()
        self.veil = VeilDetector()

    def detect(self, text: str) -> list[Span]:
        spans = self.regex.detect(text)
        taken = [(s.start, s.end) for s in spans]

        # 1) 규칙 이름 (정규식과 겹치지 않는 것만)
        for sp in self.names.detect(text):
            if _free(sp, taken):
                spans.append(sp)
                taken.append((sp.start, sp.end))

        # 2) Veil: 지정한 라벨만, 이미 잡힌 구간과 겹치지 않는 것만 추가
        for sp in self.veil.detect(text):
            if sp.label in self.veil_labels and _free(sp, taken):
                spans.append(sp)
                taken.append((sp.start, sp.end))
        return spans


class HybridAddrDetector(HybridDetector):
    name = "hybrid_addr"
    veil_labels = {"person", "address"}


# ---------------------------------------------------------------------------
# v2: Veil이 "혼자" 잡은 이름 중 오탐 두 유형을 거른다 (dev 오류 14건 분석 결과)
#  1) 한 글자 이름: "정 부장", "허 부장" → 성만 쓰고 직함을 붙인 경우.
#     데이터셋 라벨 기준(surname_title_unlabeled)상 성 하나만으로는 사람을 특정할 수 없어 개인정보 아님
#  2) 상품·회사 이름: "하린 포인트 카드", "봄날 체크카드", "미르보안"
#     → 바로 뒤에 상품·금융 단어가 오면 사람 이름이 아니라 상품명/회사명
# 확인: dev 정답 이름 중 한 글자 0건, 뒤에 상품 단어가 오는 경우 0건 → 재현율 손실 없음
# 규칙이 잡은 이름에는 적용하지 않음 (Veil 단독 탐지분만)
# ---------------------------------------------------------------------------
PRODUCT_AFTER = re.compile(
    r"\s?(체크카드|신용카드|포인트|카드(?!번호)|보안|은행|증권|페이|플러스|적금|예금|보험|생명|캐피탈)")


def veil_person_ok(text: str, sp: Span) -> bool:
    name = text[sp.start:sp.end].replace(" ", "")
    if len(name) < 2:
        return False
    if PRODUCT_AFTER.match(text, sp.end):
        return False
    return True


class HybridV2Detector(HybridDetector):
    name = "hybrid_v2"

    def detect(self, text: str) -> list[Span]:
        spans = self.regex.detect(text)
        taken = [(s.start, s.end) for s in spans]
        for sp in self.names.detect(text):
            if _free(sp, taken):
                spans.append(sp)
                taken.append((sp.start, sp.end))
        for sp in self.veil.detect(text):
            if sp.label == "person" and _free(sp, taken) and veil_person_ok(text, sp):
                spans.append(sp)
                taken.append((sp.start, sp.end))
        return spans


# ---------------------------------------------------------------------------
# v3: 자체 검증셋 A(GPT·Gemini 생성 20건) 오류 분석 결과 반영
#  - 번호: 정규식 v2 (계좌 4덩어리, 문서번호 속 숫자 제외, 대표번호 제외)
#  - 이름: 규칙 v2
#      확실한 단서(바로 뒤 직함·호칭, 바로 앞 '고객명:' 등)가 있는 이름 → 규칙만으로 채택
#      약한 단서만 있는 이름(줄 어딘가의 단서, 화자 형식) → Veil도 이름이라고 할 때만 채택 (경계는 규칙 것)
#      나머지 이름 → Veil (v2 필터 적용)
#  근거: A셋에서 규칙 이름 정밀도 0.70 (대화 로그에서 '주세요', '배송지' 등을 이름으로 잡음)
# ---------------------------------------------------------------------------
from detection.rule.name_detector_v2 import NameDetectorV2  # noqa: E402
from detection.rule.regex_detector_v2 import RegexDetectorV2  # noqa: E402


def _overlaps(sp: Span, others: list[Span]) -> bool:
    return any(not (sp.end <= o.start or o.end <= sp.start) for o in others)


class HybridV3Detector(HybridDetector):
    name = "hybrid_v3"

    def __init__(self):
        self.regex = RegexDetectorV2()
        self.names = NameDetectorV2()
        self.veil = VeilDetector()

    def detect(self, text: str) -> list[Span]:
        spans = self.regex.detect(text)
        taken = [(s.start, s.end) for s in spans]
        veil_names = [sp for sp in self.veil.detect(text)
                      if sp.label == "person" and veil_person_ok(text, sp)]

        for sp in self.names.detect(text):
            if not _free(sp, taken):
                continue
            if sp.confidence == "rule:weak" and not _overlaps(sp, veil_names):
                continue                      # 약한 단서 + 모델 불일치 → 버림
            spans.append(sp)
            taken.append((sp.start, sp.end))

        for sp in veil_names:
            if _free(sp, taken):
                spans.append(sp)
                taken.append((sp.start, sp.end))
        return spans


# ---------------------------------------------------------------------------
# v4: 번호·주소도 "정규식 ∪ Veil" (이름은 v3 그대로)
# 근거: 처음 보는 문서(C·D셋)에서 정규식이 새 형식의 주소·카드·계좌를 놓침
#       (C셋 주소 0.429, D셋 카드 0.917) ↔ Veil 단독은 mask_rec 0.996~0.999
# 단, dev에서 Veil 번호·주소 오탐은 두 유형뿐이었음 → 형식 검사로 차단
#   - 대표번호 1599-0000을 전화로 (12건)  → 개인 전화는 0으로 시작
#   - 내선 4자리(7999), 상호명(다온편의점)을 주소로 (7건) → 주소는 행정구역 단위 + 숫자 필수
# 우선순위: 정규식 > Veil. 단 Veil 구간이 정규식 구간을 완전히 감싸면 더 긴 Veil 구간 사용
#   (예: '… 판교역로 235, B동 5층'에서 'B동 5층' 누락, '5392 1234-5678-9012'에서 뒷부분만 계좌로 잡힘)
# ---------------------------------------------------------------------------
ADDR_UNIT = re.compile(r"[가-힣]+(?:시|도|구|군)\s")
CARD_SHAPE = re.compile(r"\d{4}[-. ]\d{4}[-. ]\d{4}[-. ]\d{4}")


def _digits(s: str) -> str:
    return re.sub(r"\D", "", s)


def veil_number_ok(text: str, sp: Span) -> bool:
    v = text[sp.start:sp.end]
    d = _digits(v)
    before = text[max(0, sp.start - 12):sp.start]
    if sp.label == "phone_number":
        return d.startswith("0") and 9 <= len(d) <= 11 and not REP_NUMBER.search(before)
    if sp.label == "card_number":
        return len(d) == 16
    if sp.label == "resident_registration_number":
        return len(d) == 13
    if sp.label == "account_number":
        return 10 <= len(d) <= 16 and not ID_PREFIX.search(before)
    if sp.label == "address":
        return bool(re.search(r"\d", v)) and bool(ADDR_UNIT.search(v + " "))
    if sp.label == "email":
        return "@" in v
    return False


from detection.rule.regex_detector_v2 import ID_PREFIX, REP_NUMBER  # noqa: E402


class HybridV4Detector(HybridV3Detector):
    name = "hybrid_v4"

    def detect(self, text: str) -> list[Span]:
        veil_all = self.veil.detect(text)
        veil_names = [sp for sp in veil_all if sp.label == "person" and veil_person_ok(text, sp)]
        veil_nums = [sp for sp in veil_all if sp.label != "person" and veil_number_ok(text, sp)]

        # 1) 번호·주소: 정규식 → 주소는 Veil이 더 길게 감싸면 교체 → 나머지 Veil 추가
        spans = self.regex.detect(text)
        # 4-4-4-4 (16자리) 숫자를 계좌 4덩어리 패턴이 잡은 경우 → 카드 (구분자가 섞여 카드 정규식이 놓친 경우)
        for i, s in enumerate(spans):
            if s.label == "account_number" and CARD_SHAPE.fullmatch(s.text):
                spans[i] = Span(s.start, s.end, "card_number", s.text, s.confidence)
        # Veil 구간이 정규식 구간을 완전히 감싸면 더 긴 Veil 구간으로 교체
        #  (주소 'B동 5층' 누락, 구분자가 섞인 카드 '5392 1234-5678-9012'의 뒷부분만 계좌로 잡힌 경우 등)
        for v in veil_nums:
            inside = [s for s in spans if v.start <= s.start and s.end <= v.end
                      and (s.start, s.end) != (v.start, v.end)]
            others = [s for s in spans if s not in inside]
            if inside and _free(v, [(o.start, o.end) for o in others]):
                spans = others + [v]
        taken = [(s.start, s.end) for s in spans]
        for v in veil_nums:
            if _free(v, taken):
                spans.append(v)
                taken.append((v.start, v.end))

        # 2) 이름: v3와 동일
        for sp in self.names.detect(text):
            if not _free(sp, taken):
                continue
            if sp.confidence == "rule:weak" and not _overlaps(sp, veil_names):
                continue
            spans.append(sp)
            taken.append((sp.start, sp.end))
        for sp in veil_names:
            if _free(sp, taken):
                spans.append(sp)
                taken.append((sp.start, sp.end))
        return spans


# ---------------------------------------------------------------------------
# v5: holdout 개선 (개선 전 평가 기록 후 수정 — 커밋 9d44bd4 이후)
#  - 정규식 v3: 국제번호 +82, 주소 앞 우편번호·뒤 (○○동)
#  - 공통 후처리: '+' 유지, 괄호 짝 복구
#  - 이름 오탐 필터 (규칙·Veil 공통):
#      경조사 용어 '조모상', '장인상' 등 (부고 문서에서 '김미영 사원 조모상')
#      회사·상품 꼬리로 끝나는 이름 '미르보안' (표 안의 회사명)
# ---------------------------------------------------------------------------
from detection.rule.regex_detector_v3 import RegexDetectorV3  # noqa: E402

KIN_EVENTS = {"조부상", "조모상", "부친상", "모친상", "장인상", "장모상", "빙부상", "빙모상",
              "시부상", "시모상", "외조부상", "외조모상", "본인상"}
ORG_TAIL = re.compile(r"(보안|은행|카드|증권|보험|생명|캐피탈|테크|시스템|솔루션|컴퍼니|그룹|파트너스|랩스|소프트)$")


def person_ok_v5(sp: Span) -> bool:
    if sp.label != "person":
        return True
    name = sp.text.replace(" ", "")
    return name not in KIN_EVENTS and not ORG_TAIL.search(name)


class HybridV5Detector(HybridV4Detector):
    name = "hybrid_v5"

    def __init__(self):
        super().__init__()
        self.regex = RegexDetectorV3()

    def detect(self, text: str) -> list[Span]:
        out = []
        for sp in super().detect(text):
            if sp.label == "person" and not sp.text:
                sp = Span(sp.start, sp.end, sp.label, text[sp.start:sp.end], sp.confidence)
            if person_ok_v5(sp):
                out.append(sp)
        return out


# ---------------------------------------------------------------------------
# v6: 자체 검증셋 E(형식 변형 집중 63건) 오류 분석 반영
#  - 정규식 v4: 해외·국제 전화(+1, +81, +86, 82-10), 괄호 지역번호, 15자리 카드, 지번 주소, 사업자번호 제외
#  - Veil 번호 수용 기준 확대: 전화는 '+'·'82'로 시작해도 OK(최대 15자리), 카드는 15·16자리
#  - 이름: Veil 구간이 규칙 구간을 완전히 감싸면 더 긴 Veil 구간 사용 ('마이클' → '마이클 존슨')
#  - 공통 후처리: 괄호 지역번호 유지, 외국인 이름 끝 '이' 보존('왕리웨이')
# ---------------------------------------------------------------------------
from detection.rule.regex_detector_v4 import RegexDetectorV4  # noqa: E402


def veil_number_ok_v6(text: str, sp: Span) -> bool:
    v = text[sp.start:sp.end].strip()
    d = _digits(v)
    before = text[max(0, sp.start - 12):sp.start]
    if sp.label == "phone_number":
        intl = v.startswith("+") or (d.startswith("82") and not d.startswith("0"))
        return (d.startswith("0") or intl) and 9 <= len(d) <= 15 and not REP_NUMBER.search(before)
    if sp.label == "card_number":
        return len(d) in (15, 16)
    return veil_number_ok(text, sp)


def _wider_name_ok(text: str, v: Span, sp: Span, rule_names: list[Span]) -> bool:
    """Veil 이름 구간(v)이 규칙 이름(sp)을 감쌀 때, v를 써도 되는 경우만 True.
    ('마이클' → '마이클 존슨'은 OK. dev에서 Veil이 '김은영\n\n윤 선영'처럼 두 사람을 한 구간으로
     묶은 경우가 있어 아래 조건으로 막음)
    - 구간 안에 줄바꿈·쉼표·구분자가 없을 것
    - 구간 안에 다른 규칙 이름이 없을 것 (두 사람을 하나로 묶지 않게)
    - 덧붙는 부분이 직함·호칭이 아닐 것 ('김은영 대리')"""
    vt = text[v.start:v.end]
    if re.search(r"[\n,/|·:;()]", vt):
        return False
    if any(o is not sp and not (o.end <= v.start or v.end <= o.start) for o in rule_names):
        return False
    extra = (text[v.start:sp.start] + " " + text[sp.end:v.end]).strip()
    from detection.rule.name_detector_v2 import TITLES
    return bool(extra) and not any(extra == t or extra.startswith(t) for t in TITLES)


class HybridV6Detector(HybridV5Detector):
    name = "hybrid_v6"

    def __init__(self):
        super().__init__()
        self.regex = RegexDetectorV4()

    def detect(self, text: str) -> list[Span]:
        veil_all = self.veil.detect(text)
        veil_names = [sp for sp in veil_all if sp.label == "person" and veil_person_ok(text, sp)]
        veil_nums = [sp for sp in veil_all if sp.label != "person" and veil_number_ok_v6(text, sp)]

        # 1) 번호·주소 (v4와 같은 순서)
        spans = self.regex.detect(text)
        for i, s in enumerate(spans):
            if s.label == "account_number" and CARD_SHAPE.fullmatch(s.text):
                spans[i] = Span(s.start, s.end, "card_number", s.text, s.confidence)
        for v in veil_nums:
            inside = [s for s in spans if v.start <= s.start and s.end <= v.end
                      and (s.start, s.end) != (v.start, v.end)]
            others = [s for s in spans if s not in inside]
            if inside and _free(v, [(o.start, o.end) for o in others]):
                spans = others + [v]
        taken = [(s.start, s.end) for s in spans]
        for v in veil_nums:
            if _free(v, taken):
                spans.append(v)
                taken.append((v.start, v.end))

        # 2) 이름: 규칙 → (Veil이 더 길게 감싸면 Veil 구간) → 남은 Veil
        rule_names = [sp for sp in self.names.detect(text)]
        names = []
        for sp in rule_names:
            if not _free(sp, taken):
                continue
            if sp.confidence == "rule:weak" and not _overlaps(sp, veil_names):
                continue
            wider = [v for v in veil_names
                     if v.start <= sp.start and sp.end <= v.end and (v.end - v.start) > (sp.end - sp.start)
                     and _free(v, taken) and _wider_name_ok(text, v, sp, rule_names)]
            names.append(wider[0] if wider else sp)
        for sp in names:
            if _free(sp, [(n.start, n.end) for n in spans if n.label == "person"]):
                spans.append(sp)
        taken = [(s.start, s.end) for s in spans]
        for sp in veil_names:
            if _free(sp, taken):
                spans.append(sp)
                taken.append((sp.start, sp.end))

        out = []
        for sp in spans:
            if not sp.text:
                sp = Span(sp.start, sp.end, sp.label, text[sp.start:sp.end], sp.confidence)
            if person_ok_v5(sp):
                out.append(sp)
        return out
