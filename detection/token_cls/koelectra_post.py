"""KoELECTRA 후처리 실험. dev 오류를 보고 만든 임시 후처리예요.

- v1: person 예측을 줄바꿈(CR/LF)에서 분리 (split_lines)
- 이름 사전, 정답, personas.json은 쓰지 않아요.
- 실제 하나의 이름이 여러 줄에 나뉘면 잘못 분리할 수 있어요.
- baseline(koelectra)은 건드리지 않아요. 설정이 바뀌면 새 --tag를 써요.
"""
import re

from detection.base import Span
from detection.validation import check_spans
from detection.token_cls.koelectra import KoElectraDetector


def _trim(text, start, end):
    """양 끝 공백을 줄인 (start, end)를 돌려줘요."""
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    return start, end


def split_person_lines(text, spans):
    """person 예측을 줄바꿈에서 나눠요. 다른 라벨은 그대로 둬요."""
    result = []
    for span in spans:
        if span.label != 'person':
            result.append(span)
            continue
        for part in re.finditer(r'[^\r\n]+', text[span.start:span.end]):
            start, end = _trim(text, span.start + part.start(), span.start + part.end())
            if start < end:
                result.append(Span(start, end, span.label, text[start:end]))
    return result




# 후처리 단계 목록. 새 후처리는 여기에 이름으로 등록해요. 한 번에 하나씩 추가해요.
STEPS = {
    'split_lines': split_person_lines,
}


class KoElectraPostDetector(KoElectraDetector):
    name = 'koelectra_post'
    default_steps = ('split_lines',)   # v1과 같은 동작

    def __init__(self, threads=2, steps=None):
        steps = tuple(self.default_steps if steps is None else steps)
        unknown = [s for s in steps if s not in STEPS]
        if unknown:
            raise ValueError(f'모르는 후처리 단계: {unknown}')
        # super().__init__ 안에서 detect()가 불리므로 먼저 설정해요.
        self.steps = steps
        super().__init__(threads=threads)

    def detect(self, text):
        spans = super().detect(text)
        for step in self.steps:
            spans = STEPS[step](text, spans)
        return check_spans(text, spans)