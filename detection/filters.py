"""고객사 기준 후처리. split_clean 정책에서만 쓴다 (기존 split은 그대로).

clean()               : combo 결과에 마지막으로 적용
  - 추가: 지역번호 유선전화(031·051 …)·070  (02는 규칙에 이미 있음)
  - 제외: 성+직함(허부장)·성 한 글자 / 대표번호·내선 뒤 번호 / 지점·영업점 이름
filter_model_names()  : 규칙이 못 잡고 모델만 잡은 이름을 merge 전에 거름
  - 단어 중간에서 시작(미르보안→보안), 상품명 앞(하린 포인트), 단서 단어(내선·고객명)
  - Kiwi가 동사·어미로 본 것(달래), 단서 없는 1~2글자(을, 이첩), 성씨로 시작하지 않는 3글자 이상(미이수)
  - '전 서윤'처럼 모델이 성을 빼먹으면 앞의 성을 붙여 줌
"""
import re

from detection.base import Span
from detection.rule.kiwi_filter import _get_kiwi
from detection.rule.names import SURNAME_PAT, TITLES, has_strong_cue

# ---------- clean() ----------
LANDLINE = re.compile(r"(?<!\d)(?:0[3-6][1-5]|070)[-. ]?\d{3,4}[-. ]?\d{4}(?!\d)")
TITLE_ONLY = re.compile(rf"{SURNAME_PAT}(?:\s?{TITLES}님?)?")
REP_CUE = re.compile(r"(?:대표\s?번호|대표\s?전화|내선)\s*[:：]?\s*\(?$")
BRANCH_WORDS = r"(?:지점|본점|영업점|영업부|출장소|센터)"
BRANCH_END = re.compile(rf"{BRANCH_WORDS}$")
BRANCH_AFTER = re.compile(rf"\s?{BRANCH_WORDS}")

# ---------- filter_model_names() ----------
HONOR_AFTER = re.compile(r"\s?(?:님|씨)")
PERSON_JOSA_AFTER = re.compile(r"(?:이랑|랑|한테|에게|께서|께|아|야)(?![가-힣])")   # 은서랑, 지영아
PRODUCT_AFTER = re.compile(r"\s?(?:포인트|카드|통장|적금|예금|보험|대출|펀드|상품|서비스|앱)")
CUE_WORDS = re.compile(r"(?:내선|대표\s?번호|대표\s?전화|고객\s?명|성\s?명|이\s?름)")
STARTS_WITH_SURNAME = re.compile(SURNAME_PAT)
SURNAME_BEFORE = re.compile(rf"(?:^|(?<=[^가-힣]))({SURNAME_PAT}) $")             # '전 ' + 서윤


def _doc_tokens(text, span):
    return [t for t in _get_kiwi().tokenize(text) if t.start < span.end and span.start < t.start + t.len]


def _line_tokens(text, span):
    """구간이 있는 줄만 Kiwi로 분석. 문서 전체 분석과 결과가 다를 때가 있어 둘 다 본다.
    (문서 전체: 줄바꿈 너머와 붙여 '달래'를 고유명사로 봄 / 줄만: '대상:'을 고유명사로 봄)"""
    ls = text.rfind("\n", 0, span.start) + 1
    le = text.find("\n", span.end)
    le = len(text) if le == -1 else le
    toks = _get_kiwi().tokenize(text[ls:le])
    return [t for t in toks if ls + t.start < span.end and span.start < ls + t.start + t.len]


def _overlaps(s, e, spans):
    return any(s < o.end and o.start < e for o in spans)


def add_landlines(text, spans):
    spans = list(spans)
    for m in LANDLINE.finditer(text):
        if not _overlaps(m.start(), m.end(), spans):
            spans.append(Span(m.start(), m.end(), "phone_number", m.group()))
    return sorted(spans, key=lambda s: (s.start, s.end, s.label))


def drop_non_targets(text, spans):
    out = []
    for s in spans:
        if s.label == "person" and (TITLE_ONLY.fullmatch(s.text) or CUE_WORDS.fullmatch(s.text.replace(" ", ""))):
            continue                                  # 허부장, 허 부장님, 허 / 고객명, 내선
        if s.label == "phone_number" and REP_CUE.search(text[max(0, s.start - 12):s.start]):
            continue                                  # 대표번호 02-…, 내선 …
        if s.label == "address" and (BRANCH_END.search(s.text) or BRANCH_AFTER.match(text, s.end)):
            continue                                  # 여의도 지점
        out.append(s)
    return out


def clean(text, spans):
    return drop_non_targets(text, add_landlines(text, spans))


def filter_model_names(text, model_spans, rule_spans):
    """모델만 잡은 이름(오탐이 많음)을 거른다. 규칙과 겹치는 구간은 merge에서 처리되므로 그대로 둔다."""
    rule_names = {s.text.replace(" ", "") for s in rule_spans if s.label == "person"}
    givens = rule_names | {n[-2:] for n in rule_names if len(n) >= 3}
    out = []
    for s in model_spans:
        if s.label != "person" or _overlaps(s.start, s.end, rule_spans):
            out.append(s)
            continue
        plain = s.text.replace(" ", "")
        if s.start > 0 and re.match(r"[가-힣]", text[s.start - 1]):
            continue                                  # 이첩→'첩', 미르보안→'보안'
        if PRODUCT_AFTER.match(text, s.end) or CUE_WORDS.fullmatch(plain):
            continue                                  # 하린 포인트 카드 / 내선, 고객명
        views = [_doc_tokens(text, s), _line_tokens(text, s)] if len(plain) <= 2 else [[], []]
        if any(t.tag.startswith(("V", "E")) for toks in views for t in toks):
            continue                                  # 달래 (동사) — 풀네임은 Kiwi가 잘못 쪼갤 수 있어 제외
        cue = (has_strong_cue(text, s) or HONOR_AFTER.match(text, s.end)
               or PERSON_JOSA_AFTER.match(text, s.end))
        if len(plain) == 1 and not cue:
            continue                                  # 내, 을
        if len(plain) == 2:
            one_noun = any(len(toks) == 1 and toks[0].tag == "NNG" for toks in views)   # '대상'처럼 한 덩어리 일반명사
            kiwi_name = all(toks and toks[0].tag == "NNP" for toks in views)         # 두 분석 모두 고유명사
            if not one_noun and SURNAME_BEFORE.search(text[max(0, s.start - 3):s.start]):
                s = Span(s.start - 2, s.end, "person", text[s.start - 2:s.end])   # 전 서윤
            elif not (plain in givens or cue or kiwi_name):
                continue                              # 이첩, 대상
        if len(plain) >= 3:
            if not STARTS_WITH_SURNAME.match(plain):
                continue                              # 미이수, 미르보안
        out.append(s)
    return out