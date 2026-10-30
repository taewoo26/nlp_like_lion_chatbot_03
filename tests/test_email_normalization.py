import pytest

from starter.pii_pipeline import normalize_value


@pytest.mark.parametrize('value,expected', [
    ('at@dot.com', 'at@dot.com'),
    ('User.Name+tag_test-X@EXAMPLE.COM', 'User.Name+tag_test-X@example.com'),
    ('hong(at)dot.com', 'hong@dot.com'),
    ('hong(at)example(dot)com', 'hong@example.com'),
    ('hong@example(dot)com', 'hong@example.com'),
    ('hong at example dot com', 'hong@example.com'),
    ('hong 골뱅이 example 닷컴', 'hong@example.com'),
    ('hong 골뱅이 example 점 넷', 'hong@example.net'),
    ('first(at)last@example.com', 'first(at)last@example.com'),
])
def test_only_approved_email_notation_is_restored(value, expected):
    assert normalize_value('email', value) == expected


@pytest.mark.parametrize('value', ['User @ example.com', 'hong at work', 'hello world',
                                 'hong at example dot com extra', 'hong@ example.com'])
def test_unconfirmed_spaces_and_prose_are_rejected(value):
    with pytest.raises(ValueError):
        normalize_value('email', value)
