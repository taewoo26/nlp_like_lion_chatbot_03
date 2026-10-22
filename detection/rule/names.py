"""이름(person) 후보 탐지 1단계: 성씨 + 문맥 규칙 (Kiwi 없이).

원칙
- 일반 성씨 목록만 쓴다. (personas.json 같은 정답 인물 목록은 쓰지 않는다)
- 정답 구간에는 조사·호칭이 들어가지 않으므로 '이름 + (호칭) + (조사)'에서 이름만 잡는다.
- 이름만 나오는 경우(given)는 같은 문서에서 이미 잡은 풀네임에서 이름 부분을 가져와 찾는다.
"""
import re

from detection.base import Span

# 일반적인 한 글자 성씨 (특정 데이터셋에서 가져온 것이 아님)
SURNAMES = ("김이박최정강조윤장임한오서신권황안송류전홍고문양손배백허유남심노하곽성차주우구민나진지엄채원천방공"
            "현함변염여추도소석선설마길연위표명기반왕금옥육인맹제모탁국어은편용예봉경사부가복태목형피두감음빈동온호")

# 이름 바로 뒤에 올 수 있는 호칭 / 조사 (정답 구간에는 포함되지 않는다)
HONORIFICS = r"(?:님|씨|고객님|고객|상담원|담당자|팀장|부장|차장|과장|대리|사원|주임|매니저|선생님)"
JOSA = r"(?:은|는|이|가|을|를|과|와|랑|이랑|아|야|도|의|에게|에서|께서|께|으로|로|만|조차|부터|까지)"
TAIL = rf"(?={HONORIFICS}?{JOSA}?(?![가-힣]))"

# '성 + 공백 + 두 글자'가 이름이 아니라 직함/일반어일 때 제외
NOT_GIVEN = {"대리", "과장", "부장", "팀장", "차장", "사원", "주임", "상무", "이사", "대표",
             "고객", "직원", "담당", "상담", "임원"}

FULL = re.compile(rf"(?<![가-힣])([{SURNAMES}][가-힣]{{2}}){TAIL}")
SPACED = re.compile(rf"(?<![가-힣])([{SURNAMES}]) ([가-힣]{{2}}){TAIL}")


def detect_names(text: str) -> list[Span]:
    spans, taken = [], []

    def free(s, e):
        return not any(s < b and a < e for a, b in taken)

    def add(s, e):
        spans.append(Span(s, e, "person", text[s:e]))
        taken.append((s, e))

    givens = set()
    for m in SPACED.finditer(text):                       # 윤 선영
        if m.group(2) in NOT_GIVEN:
            continue
        s, e = m.start(1), m.end(2)
        if free(s, e):
            add(s, e)
            givens.add(m.group(2))
    for m in FULL.finditer(text):                         # 윤선영
        s, e = m.start(1), m.end(1)
        if free(s, e):
            add(s, e)
            givens.add(m.group(1)[1:])
    for g in givens:                                      # 승현아 (같은 문서의 풀네임에서 가져온 이름)
        for m in re.finditer(rf"(?<![가-힣]){re.escape(g)}{TAIL}", text):
            if free(m.start(), m.end()):
                add(m.start(), m.end())
    return sorted(spans, key=lambda s: s.start)
