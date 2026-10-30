from unittest.mock import Mock

from detection.base import Span
from detection.combo import ComboDetector, POLICIES
from detection.extra_rules import drop_non_account_identifiers, filter_model_v2, tidy_name
from detection.rule.detector import RuleDetector


def detect_v2(text):
    detector = ComboDetector.__new__(ComboDetector)
    detector.policy = 'split_v2'
    detector.modes = POLICIES['split_v2']
    detector.rule = RuleDetector(use_kiwi=False)
    detector.model = Mock()
    detector.model.detect.return_value = []
    return [(span.label, span.text, span.start, span.end)
            for span in detector.detect(text)]


def test_foreign_registration_number_is_included_by_provisional_policy():
    text = '외국인등록번호 850101-5123456 확인'
    spans = detect_v2(text)
    assert ('resident_registration_number', '850101-5123456',
            text.index('850101'), text.index('850101') + 14) in spans


def test_split_phone_is_one_span_with_original_newline():
    for newline in ('\n', '\r\n'):
        text = f'연락처: 010-0123-{newline}4567'
        number = f'010-0123-{newline}4567'
        spans = detect_v2(text)
        assert ('phone_number', number, text.index('010'), len(text)) in spans
        assert not [span for span in spans if span[0] == 'phone_number' and span[1] != number]


def test_separate_numbers_across_newline_are_not_joined():
    text = '접수번호 2026-1234\n4567 확인'
    assert not [span for span in detect_v2(text) if span[0] == 'phone_number']


def test_address_newline_kept_but_zip_excluded():
    text = '(06236) 서울 강남구 테헤란로 123\n110동 905호'
    expected = '서울 강남구 테헤란로 123\n110동 905호'
    spans = detect_v2(text)
    assert ('address', expected, text.index('서울'), len(text)) in spans
    assert not [span for span in spans if span[0] == 'address' and span[1] != expected]


def make_span(text, value, label='person'):
    start = text.index(value)
    return Span(start, start + len(value), label, value)


def test_product_context_keeps_actual_person_cue():
    text = '신상품: 하린 포인트 카드\n고객명: 하린\n하린 씨가 방문했다.'
    first = make_span(text, '하린')
    second_start = text.index('하린', first.end)
    third_start = text.index('하린', second_start + 2)
    others = [Span(start, start + 2, 'person', '하린') for start in (second_start, third_start)]
    assert filter_model_v2(text, [first, *others]) == others


def test_route_context_keeps_person_with_honorific():
    text = '접수 경로: 금융감독원 이첩\n접수 경로: 이첩 씨'
    first = make_span(text, '이첩')
    second_start = text.rindex('이첩')
    second = Span(second_start, second_start + 2, 'person', '이첩')
    assert filter_model_v2(text, [first, second]) == [second]


def test_identifier_context_does_not_remove_later_account():
    text = '사건 번호: 경찰청 2026-921245 / 입금 계좌: 110-123-456789'
    event = make_span(text, '2026-921245', 'account_number')
    account = make_span(text, '110-123-456789', 'account_number')
    assert filter_model_v2(text, [event, account]) == [account]
    assert drop_non_account_identifiers(text, [event, account]) == [account]


def test_name_fragment_inside_word_is_dropped():
    text = '결제 알림 미수신 / 미수 씨에게 연락'
    first = make_span(text, '미수')
    second_start = text.rindex('미수')
    second = Span(second_start, second_start + 2, 'person', '미수')
    assert filter_model_v2(text, [first, second]) == [second]


def test_short_name_with_attached_subject_particle_is_kept():
    text = '심사역 안윤이 조건을 안내했다.'
    name = make_span(text, '안윤')
    assert filter_model_v2(text, [name]) == [name]


def test_particle_before_customer_noun_is_trimmed_but_name_ending_is_kept():
    text = '이은서는 고객 안내 문자를 보냈다.'
    candidate = make_span(text, '이은서는')
    assert tidy_name(text, candidate).text == '이은서'

    text = '이은아 고객이 방문했다.'
    candidate = make_span(text, '이은아')
    assert tidy_name(text, candidate).text == '이은아'


def test_given_name_with_honorific_particle_and_explicit_name_field():
    assert ('person', '보내', 0, 2) in detect_v2('보내 씨에게 자료를 요청했다.')
    text = '고객명: 새봄\n보험 보장 내용을 확인하세요.'
    assert ('person', '새봄', text.index('새봄'), text.index('새봄') + 2) in detect_v2(text)
    assert not [span for span in detect_v2('고객명: 전체 임직원') if span[0] == 'person']
