"""이름(person) 탐지: 후보를 넓게 잡고(1단계), 문맥 단서로 확정하거나 Kiwi로 거른다(2단계).

원칙
- 일반 성씨 목록만 쓴다. (personas.json 같은 정답 인물 목록은 쓰지 않는다)
- 정답 구간에는 조사·호칭이 들어가지 않으므로 '이름 + (호칭) + (조사)'에서 이름만 잡는다.
- 이름만 나오는 경우(given)는 같은 문서에서 이미 잡은 풀네임에서 이름 부분을 가져와 찾는다.
- 이름 바로 뒤에 호칭·'씨'·'드림'·'입니다'가 오면 '강한 문맥 단서'로 보고 Kiwi 없이도 이름으로 확정한다.
"""
import re

from detection.base import Span

# 일반적인 한 글자 성씨 (특정 데이터셋에서 가져온 것이 아님)
SURNAMES = ("김이박최정강조윤장임한오서신권황안송류전홍고문양손배백허유남심노하곽성차주우구민나진지엄채원천방공"
            "현함변염여추도소석선설마길연위표명기반왕금옥육인맹제모탁국어은편용예봉경사부가복태목형피두감음빈동온호")
# 두 글자 성씨
SURNAMES2 = ("남궁", "황보", "제갈", "선우", "독고", "사공", "서문")
SURNAME_PAT = rf"(?:{'|'.join(SURNAMES2)}|[{SURNAMES}])"

# 이름 바로 뒤에 올 수 있는 호칭 / 조사 (정답 구간에는 포함되지 않는다)
TITLES = (r"(?:고객|상담원|담당자|팀장|부장|차장|과장|대리|사원|주임|책임|선임|수석|이사|상무|전무|대표|"
          r"실장|본부장|센터장|매니저|선생)")
HONORIFICS = r"(?:님|씨|고객님|고객|상담원|담당자|팀장|부장|차장|과장|대리|사원|주임|책임|매니저|선생님)"
JOSA = r"(?:은|는|이|가|을|를|과|와|랑|이랑|아|야|도|의|에게|에서|께서|께|으로|로|만|조차|부터|까지)"
TAIL = rf"(?={HONORIFICS}?{JOSA}?(?:입니다)?(?![가-힣]))"

# 이름 바로 뒤에 이런 말이 오면 이름일 가능성이 매우 높다 (강한 문맥 단서)
RIGHT_CUE = re.compile(rf"(?:\s?{TITLES}(?:님)?{JOSA}?|님{JOSA}?|\s?씨|\s?드림|입니다)(?![가-힣])")

# 이름이 될 수 없는 끝 글자 (예: '전화를' → 전 + 화를) / 직함 앞에 자주 오는 일반 명사
NOT_NAME_END = "를을는의에도만과와"
GENERIC_NOUNS = {"연락처", "민원인", "신청인", "계약자", "관리자", "대리인", "대표자", "연회비"}

# '성 + 공백 + 두 글자'가 이름이 아니라 직함/일반어일 때 제외
NOT_GIVEN = {"대리", "과장", "부장", "팀장", "차장", "사원", "주임", "상무", "이사", "대표",
             "고객", "직원", "담당", "상담", "임원", "책임", "선임", "수석"}

FULL = re.compile(rf"(?<![가-힣])({SURNAME_PAT}[가-힣]{{2}}){TAIL}")
SPACED = re.compile(rf"(?<![가-힣])({SURNAME_PAT}) ([가-힣]{{2}}){TAIL}")
GIVEN_SSI = re.compile(r"(?<![가-힣])([가-힣]{2})(?=\s?씨(?![가-힣]))")   # '지영 씨' (성 없이 이름 + 씨)


def _plausible(stem: str) -> bool:
    plain = stem.replace(" ", "")
    return plain[-1] not in NOT_NAME_END and plain not in GENERIC_NOUNS


def detect_names(text: str) -> list[Span]:
    """이름 후보를 넓게 잡는다 (재현율 우선, 오탐은 뒤 단계에서 거른다)."""
    spans, taken = [], []

    def free(s, e):
        return not any(s < b and a < e for a, b in taken)

    def add(s, e):
        spans.append(Span(s, e, "person", text[s:e]))
        taken.append((s, e))

    givens = set()
    for m in SPACED.finditer(text):                       # 윤 선영
        if m.group(2) in NOT_GIVEN or not _plausible(m.group(2)):
            continue
        s, e = m.start(1), m.end(2)
        if free(s, e):
            add(s, e)
            givens.add(m.group(2))
    for m in FULL.finditer(text):                         # 윤선영
        s, e = m.start(1), m.end(1)
        if _plausible(m.group(1)) and free(s, e):
            add(s, e)
            givens.add(m.group(1)[-2:])
    for g in givens:                                      # 승현아 (같은 문서의 풀네임에서 가져온 이름)
        for m in re.finditer(rf"(?<![가-힣]){re.escape(g)}{TAIL}", text):
            if free(m.start(), m.end()):
                add(m.start(), m.end())
    for m in GIVEN_SSI.finditer(text):                    # 지영 씨
        if free(m.start(1), m.end(1)):
            add(m.start(1), m.end(1))
    return sorted(spans, key=lambda s: s.start)


def has_strong_cue(text: str, span: Span) -> bool:
    """이름 바로 뒤에 호칭·씨·드림·입니다가 오는가 (Kiwi 없이도 이름으로 확정할 근거)."""
    return RIGHT_CUE.match(text, span.end) is not None


def trim_particle(text: str, span: Span, spans: list[Span]) -> Span:
    """이름 끝에 딸려 온 조사를 떼어 경계 오류를 줄인다 (예: 은서랑 → 은서, 소율이(랑) → 소율)."""
    s, nxt = span.text, text[span.end:span.end + 1]
    if len(s) != 3:
        return span
    cut = 0
    if s[-1] == "랑":                                       # 은서랑
        cut = 1
    elif s[-1] == "이" and nxt == "랑":                     # 소율이랑
        cut = 1
    elif s[-1] in "아야" and nxt in (",", "，"):            # 지영아, (호격)
        others = {o.text[-2:] for o in spans if o is not span and len(o.text) >= 3}
        if s[:2] in others:                                 # 같은 문서의 풀네임에 그 이름이 있을 때만
            cut = 1
    if not cut:
        return span
    return Span(span.start, span.end - cut, "person", text[span.start:span.end - cut])