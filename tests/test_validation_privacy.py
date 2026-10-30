import pytest

from detection.base import Span
from detection.validation import check_spans


@pytest.mark.parametrize('text,span', [
    ('고객', Span(0, 3, 'person', '윤선영')),
    ('김민준', Span(0, 3, 'person', '윤선영')),
])
def test_invalid_spans_do_not_put_original_values_in_errors(text, span):
    with pytest.raises(ValueError) as error:
        check_spans(text, [span])
    assert '윤선영' not in str(error.value)
    assert '김민준' not in str(error.value)
    assert 'index=0' in str(error.value)
