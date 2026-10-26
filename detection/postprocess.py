"""모든 탐지기 결과에 공통으로 적용하는 경계 후처리 (라벨 가이드 5장).

strict 채점은 시작·끝이 정답과 정확히 같아야 하므로,
탐지기가 조사·호칭·괄호까지 잡았을 때 잘라낸다.
"""
import re

from detection.base import LABELS, Span

# 이름 뒤에서 떼어낼 꼬리 (긴 것부터 검사)
# 호칭·여러 글자 꼬리는 항상 뗀다
PERSON_TAILS = [
    "님께서", "께서", "님이", "님은", "님을", "님의", "님께", "님",
    "씨가", "씨는", "씨를", "씨", "입니다", "이랑", "에게",
]
# 한 글자 조사는 이름 끝 글자와 겹칠 수 있어(김하은, 이수아) 4글자 이상일 때만 뗀다
PERSON_JOSA_1 = ["은", "는", "이", "가", "을", "를", "의", "과", "와", "도", "랑", "아", "야"]
COMPOUND_SURNAMES = ("남궁", "황보", "제갈", "선우", "독고", "사공", "서문", "동방")
# 주소·이메일·번호 뒤 조사
VALUE_TAILS = ["으로", "에서", "로", "에", "을", "를", "은", "는", "이", "가", "와", "과"]

EDGE_STRIP = " \t\n()[]{}<>\"'“”‘’,.;:/|·-"


def _strip_edges(text: str, s: int, e: int) -> tuple[int, int]:
    while s < e and text[s] in EDGE_STRIP:
        s += 1
    while e > s and text[e - 1] in EDGE_STRIP:
        e -= 1
    return s, e


def _strip_tail(text: str, s: int, e: int, tails: list[str], min_len: int) -> tuple[int, int]:
    piece = text[s:e]
    for t in tails:
        if piece.endswith(t) and len(piece) - len(t) >= min_len:
            return s, e - len(t)
    return s, e


def _rebalance_parens(text: str, s: int, e: int) -> tuple[int, int]:
    """가장자리 정리로 괄호 짝이 깨졌으면 되돌린다 (holdout 개선, 2026-10-26).
    예) '(05299) 서울특별시 …' → '05299) 서울…'이 되면 앞 '(' 복구
        '… 502호 (범어동)'     → '… (범어동'이 되면 뒤 ')' 복구"""
    piece = text[s:e]
    if piece.count("(") > piece.count(")") and text[e:e + 1] == ")":
        e += 1
    if piece.count(")") > piece.count("(") and s > 0 and text[s - 1] == "(":
        s -= 1
    return s, e


_KIWI = None


def _josa_tail_ok(text: str, s: int, e: int) -> bool:
    """이름 끝 '이·가'를 조사로 보고 뗄지 결정.
    기본은 뗀다 (한국 이름 + 조사: '권온유이 조건 안내', '윤선영이 연락함').
    단, 뒤에 바로 구분자(| / , ( ) 줄바꿈 끝)가 오면 표·항목 속 이름이라 조사가 붙을 자리가 아니므로
    이름의 일부로 보고 남긴다 ('| 왕리웨이 |', '대상자: 왕리웨이 /').
    Kiwi가 끝을 조사(J*)로 분석하면 항상 뗀다."""
    piece = text[s:e]
    if not piece.endswith(("이", "가")):
        return True
    global _KIWI
    if _KIWI is None:
        from kiwipiepy import Kiwi
        _KIWI = Kiwi()
    toks = _KIWI.tokenize(piece)
    if toks and toks[-1].tag.startswith("J"):
        return True
    after = text[e:e + 3].lstrip(" ")
    return not (after == "" or after[0] in "|/,()\n")


def clean_span(text: str, sp: Span) -> Span | None:
    s, e = _strip_edges(text, sp.start, sp.end)
    if sp.label == "person":
        # 이름은 최소 2글자. 꼬리를 뗀 뒤 다시 가장자리 정리
        s, e = _strip_tail(text, s, e, PERSON_TAILS, 2)
        piece = text[s:e].replace(" ", "")
        if len(piece) >= 4 and not piece.startswith(COMPOUND_SURNAMES) and _josa_tail_ok(text, s, e):
            s, e = _strip_tail(text, s, e, PERSON_JOSA_1, 3)
        s, e = _strip_edges(text, s, e)
    elif sp.label in ("address", "email"):
        s, e = _strip_tail(text, s, e, VALUE_TAILS, 4)
        s, e = _strip_edges(text, s, e)
        s, e = _rebalance_parens(text, s, e)
    elif sp.label != "address":
        # 번호류: 숫자로 시작해서 숫자로 끝나게 (국제번호 '+82'의 '+'는 유지)
        while s < e and not text[s].isdigit():
            s += 1
        if sp.label == "phone_number" and s > 0 and text[s - 1] == "+":
            s -= 1
        # 괄호 지역번호 '(010)0123-4567': 안에 ')'가 있으면 앞 '('까지 포함
        if sp.label == "phone_number" and s > 0 and text[s - 1] == "(" and ")" in text[s:e]:
            s -= 1
        while e > s and not text[e - 1].isdigit():
            e -= 1
    if s >= e or sp.label not in LABELS:
        return None
    return Span(s, e, sp.label, text[s:e], sp.confidence)


# 같은 구간 겹침 정리: 긴 것 우선, 같으면 먼저 나온 것
def resolve_overlaps(spans: list[Span]) -> list[Span]:
    out: list[Span] = []
    for sp in sorted(spans, key=lambda x: (-(x.end - x.start), x.start)):
        if all(sp.end <= o.start or o.end <= sp.start for o in out):
            out.append(sp)
    return sorted(out, key=lambda x: x.start)


def finalize(text: str, spans: list[Span]) -> list[Span]:
    cleaned = [c for c in (clean_span(text, sp) for sp in spans) if c]
    return resolve_overlaps(cleaned)


_WS = re.compile(r"\s+")
