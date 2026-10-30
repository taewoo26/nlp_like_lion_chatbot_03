from detection.base import Span
from detection.combo import drop_contextual_non_names, drop_surname_title, merge_spans
from detection.rule.detector import RuleDetector
from detection.combo import ComboDetector, POLICIES
from unittest.mock import Mock
import pytest


@pytest.mark.parametrize('policy', ['union', 'split'])
def test_existing_policy_calls_name_filters(policy):
    text = '허 부장님, 내선 7999. 허민수 씨에게 연락했다.'
    full_start = text.index('허민수')
    full = Span(full_start, full_start + 3, 'person', '허민수')
    extension = text.index('내선')
    det = ComboDetector.__new__(ComboDetector)
    det.policy = policy
    det.modes = POLICIES[policy]
    det.rule = Mock()
    det.rule.detect.return_value = []
    det.model = Mock()
    det.model.detect.return_value = [Span(0, 1, 'person', '허'),
                                   Span(extension, extension + 1, 'person', '내'), full]
    assert det.detect(text) == [full]


@pytest.mark.parametrize('policy', ['split_clean', 'split_v2'])
def test_new_policies_keep_their_processing(monkeypatch, policy):
    det = ComboDetector.__new__(ComboDetector)
    det.policy = policy
    det.modes = POLICIES[policy]
    det.rule = Mock()
    det.rule.detect.return_value = []
    det.model = Mock()
    det.model.detect.return_value = []
    def unexpected(*args):
        raise AssertionError('기존 필터가 새 정책에 적용됨')
    monkeypatch.setattr('detection.combo.drop_contextual_non_names', unexpected)
    monkeypatch.setattr('detection.combo.drop_surname_title', unexpected)
    det._detect_v2 = Mock(return_value=[])
    filter_names = Mock(return_value=[])
    clean_spans = Mock(return_value=[])
    monkeypatch.setattr('detection.combo.filter_model_names', filter_names)
    monkeypatch.setattr('detection.combo.clean', clean_spans)
    assert det.detect('확인') == []
    if policy == 'split_v2':
        det._detect_v2.assert_called_once()
        filter_names.assert_not_called()
        clean_spans.assert_not_called()
    else:
        filter_names.assert_called_once()
        clean_spans.assert_called_once()


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


def test_surname_title_is_dropped_but_full_name_is_kept():
    text = '허 부장님께 남 부장, 허민수 부장님이 왔다.'
    short = Span(0, 1, 'person', '허')
    title_start = text.index('남 부장')
    title = Span(title_start, title_start + 4, 'person', '남 부장')
    full_start = text.index('허민수')
    full = Span(full_start, full_start + 3, 'person', '허민수')
    assert drop_surname_title(text, [short, title, full]) == [full]


def test_geographic_phone_included_except_public_line():
    det = RuleDetector()
    text = '집 031-123-4567, 담당자 044-1234-5678, 대표번호: 051-234-5678, 내선 7999'
    phones = [s.text for s in det.detect(text) if s.label == 'phone_number']
    assert phones == ['031-123-4567', '044-1234-5678']


def test_existing_seoul_phone_and_branch_policy():
    det = RuleDetector()
    text = '담당자 02-3961-1452, 여의도 지점, 고객센터 1599-0000'
    spans = det.detect(text)
    assert [s.text for s in spans if s.label == 'phone_number'] == ['02-3961-1452']
    assert not [s for s in spans if s.label == 'address']


def test_extension_and_transfer_fragments_are_not_names():
    text = '내선 7999\n접수 경로: 금융감독원 이첩\n이첩 씨에게 전달'
    bad = [Span(text.index('내선'), text.index('내선') + 1, 'person', '내'),
           Span(text.index('이첩'), text.index('이첩') + 2, 'person', '이첩')]
    name_start = text.rindex('이첩')
    name = Span(name_start, name_start + 2, 'person', '이첩')
    assert drop_contextual_non_names(text, bad + [name]) == [name]


def test_verb_dallae_is_dropped_but_name_dallae_is_kept():
    text = '보고서 달래. 달래 씨가 왔다.'
    verb_start = text.index('달래')
    name_start = text.rindex('달래')
    verb = Span(verb_start, verb_start + 2, 'person', '달래')
    name = Span(name_start, name_start + 2, 'person', '달래')
    assert drop_contextual_non_names(text, [verb, name]) == [name]


def test_route_field_uses_word_type_and_name_cue():
    text = '접수 경로: 우편 접수\n접수 경로: 이첩 씨'
    route_start = text.index('우편')
    route_word = Span(route_start, route_start + 2, 'person', '우편')
    name_start = text.index('이첩')
    name = Span(name_start, name_start + 2, 'person', '이첩')
    assert drop_contextual_non_names(text, [route_word, name]) == [name]


def test_rule_agreement_keeps_model_name():
    text = '접수 경로: 달래 씨'
    start = text.index('달래')
    name = Span(start, start + 2, 'person', '달래')
    assert drop_contextual_non_names(text, [name], [name]) == [name]


def test_bare_name_with_subject_particle_is_kept():
    text = '달래가 왔다.'
    name = Span(0, 2, 'person', '달래')
    assert drop_contextual_non_names(text, [name]) == [name]


def test_form_field_keys_are_not_names():
    text = '을: 다솜콜솔루션\n대상: 김민준 / 010-1234-5678'
    party = Span(0, 1, 'person', '을')
    target_start = text.index('대상')
    target = Span(target_start, target_start + 2, 'person', '대상')
    assert drop_contextual_non_names(text, [party, target]) == []


def test_person_name_before_colon_is_kept():
    text = '김민준: 확인했습니다.\n은서: 알겠어.'
    full = Span(0, 3, 'person', '김민준')
    given_start = text.index('은서')
    given = Span(given_start, given_start + 2, 'person', '은서')
    assert drop_contextual_non_names(text, [full, given]) == [full, given]
