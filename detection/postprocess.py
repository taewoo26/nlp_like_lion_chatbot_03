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


def clean_span(text: str, sp: Span) -> Span | None:
    s, e = _strip_edges(text, sp.start, sp.end)
    if sp.label == "person":
        # 이름은 최소 2글자. 꼬리를 뗀 뒤 다시 가장자리 정리
        s, e = _strip_tail(text, s, e, PERSON_TAILS, 2)
        piece = text[s:e].replace(" ", "")
        if len(piece) >= 4 and not piece.startswith(COMPOUND_SURNAMES):
            s, e = _strip_tail(text, s, e, PERSON_JOSA_1, 3)
        s, e = _strip_edges(text, s, e)
    elif sp.label in ("address", "email"):
        s, e = _strip_tail(text, s, e, VALUE_TAILS, 4)
        s, e = _strip_edges(text, s, e)
    elif sp.label != "address":
        # 번호류: 숫자로 시작해서 숫자로 끝나게
        while s < e and not text[s].isdigit():
            s += 1
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
