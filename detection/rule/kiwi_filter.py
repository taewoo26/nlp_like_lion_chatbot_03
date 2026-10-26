"""이름 후보를 Kiwi 품사로 거른다 (이름 탐지 2단계).

1단계(names.py)는 '성씨로 시작하는 글자'를 모두 후보로 잡아서 재현율은 높지만 오탐이 많다
(연락처, 민원인, 연회비 …). Kiwi가 후보의 첫 토큰을 고유명사(NNP)로 볼 때만 남긴다.
같은 문서에서 한 번 통과한 문자열은 다른 위치에서도 통과시킨다(Kiwi가 일관되지 않을 때 보완).
"""
from detection.base import Span

_kiwi = None


def _get_kiwi():
    global _kiwi
    if _kiwi is None:
        from kiwipiepy import Kiwi
        _kiwi = Kiwi()
    return _kiwi


def filter_names(text: str, spans: list[Span]) -> list[Span]:
    if not spans:
        return []
    toks = [(t.tag, t.start, t.start + t.len) for t in _get_kiwi().tokenize(text)]
    passed = set()
    for s in spans:
        inside = [t for t in toks if t[1] < s.end and s.start < t[2]]
        if inside and inside[0][0] == "NNP":
            passed.add(s.text)
    return [s for s in spans if s.text in passed]
