from detection.base import Span
from detection.combo import merge_spans


def test_rule_wins_on_overlap():
    rule = [Span(0, 3, 'person', '윤선영')]
    model = [Span(0, 4, 'person', '윤선영은')]
    assert merge_spans(rule, model, {}) == rule


def test_model_fills_missing_person():
    model = [Span(0, 3, 'person', '정하은')]
    assert merge_spans([], model, {}) == model


def test_rule_mode_drops_model_numbers():
    model = [Span(0, 4, 'phone_number', '1599')]
    assert merge_spans([], model, {'phone_number': 'rule'}) == []


def test_different_label_overlap_keeps_rule():
    text = '9410-1585-0643-1712'
    rule = [Span(0, 19, 'card_number', text)]
    model = [Span(0, 19, 'account_number', text)]
    assert merge_spans(rule, model, {}) == rule