"""탐지 구간과 원문 위치 검증."""

def check_spans(text, spans):
    """후처리 결과가 원문 위치와 맞는지 확인해요. 틀리면 바로 에러를 내요."""
    for index, s in enumerate(spans):
        if not 0 <= s.start < s.end <= len(text):
            raise ValueError(f'범위 오류: index={index}')
        if s.text != text[s.start:s.end]:
            raise ValueError(f'text와 원문이 달라요: index={index}')
    return spans

