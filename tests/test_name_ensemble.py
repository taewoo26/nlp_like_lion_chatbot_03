from detection.base import Span
from eval.name_ensemble_experiment import combine_names


def test_unconfirmed_name_is_only_removed_by_agreement():
    text = '김민수 확인'
    name = Span(0, 3, 'person', '김민수')
    outputs, _, _ = combine_names(text, [], [name], [])
    assert outputs['baseline'] == [name]
    assert outputs['union'] == [name]
    assert outputs['agreement'] == []
    assert outputs['agreement_cue'] == []


def test_agreement_requires_exact_boundary():
    text = '김민수 확인'
    full = Span(0, 3, 'person', '김민수')
    part = Span(1, 3, 'person', '민수')
    outputs, _, _ = combine_names(text, [], [full], [part])
    assert outputs['agreement'] == []
    outputs, _, _ = combine_names(text, [], [full], [full])
    assert outputs['agreement'] == [full]


def test_rule_names_and_structured_baseline_are_preserved():
    text = '김민수 a@b.com'
    name = Span(0, 3, 'person', '김민수')
    email = Span(4, len(text), 'email', 'a@b.com')
    wrong = Span(4, len(text), 'person', 'a@b.com')
    outputs, _, _ = combine_names(text, [name, email], [], [wrong])
    assert all(spans == [name, email] for spans in outputs.values())


def test_strong_cue_preserves_single_model_name_and_policy_drops_title():
    text = '허 부장님, 김민수 씨'
    name_start = text.index('김민수')
    name = Span(name_start, name_start + 3, 'person', '김민수')
    title = Span(0, 1, 'person', '허')
    outputs, _, _ = combine_names(text, [], [title, name], [])
    assert outputs['agreement'] == []
    assert outputs['agreement_cue'] == [name]
    assert outputs['baseline'] == [name]
