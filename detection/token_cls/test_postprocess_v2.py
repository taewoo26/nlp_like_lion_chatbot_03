"""Synthetic contracts for label independence and conservative context gates."""
import unittest

from detection.base import Span
from detection.token_cls.postprocess_v2 import recover_financial_spans


def entity(text, value, label):
    start = text.index(value)
    return Span(start, start + len(value), label, value)


class V2Tests(unittest.TestCase):
    def test_phone_support_label_is_irrelevant(self):
        text = '연락처: 0212345678'
        for label in ('GENERIC_ID', 'DRIVER_LICENSE', 'OTHER', 'UNKNOWN_NEW_TYPE'):
            with self.subTest(label=label):
                result, audit = recover_financial_spans(text, [], [entity(text, '0212345678', label)])
                self.assertEqual(result, [entity(text, '0212345678', 'phone_number')])
                self.assertEqual(audit[0]['action'], 'add')

    def test_phone_unseen_requires_direct_field_and_personal_card(self):
        for field, supported in (('전화: ', True), ('', False)):
            text = '가상인 드림\n팀 | ' + field + '0212345678 | sample@example.invalid'
            raw = [entity(text, '가상인', 'NAME'), entity(text, 'sample@example.invalid', 'EMAIL')]
            result, _ = recover_financial_spans(text, [], raw)
            self.assertEqual(bool(result), supported)
        text = '전화: 0212345678'
        self.assertEqual(recover_financial_spans(text, [], [])[0], [])

    def test_personal_card_with_model_support_needs_no_keyword(self):
        text = '가상인 드림\n팀 | 0212345678 | sample@example.invalid'
        raw = [entity(text, '가상인', 'NAME'), entity(text, 'sample@example.invalid', 'EMAIL'),
               entity(text, '0212345678', 'OTHER')]
        self.assertEqual(len(recover_financial_spans(text, [], raw)[0]), 1)
        self.assertEqual(recover_financial_spans(text, [], raw[1:])[0], [])

    def test_other_mapped_pii_never_retyped(self):
        text = '전화: 0212345678'
        for label in ('account_number', 'resident_registration_number', 'person', 'card_number'):
            mapped = [entity(text, '0212345678', label)]
            self.assertEqual(recover_financial_spans(text, mapped, [entity(text, '0212345678', 'OTHER')])[0], mapped)

    def test_company_numbers_excluded_even_with_person_email(self):
        for field in ('대표전화: ', '고객센터 | 전화: ', '문의전화: ', '공용 전화: ', '대표번호: '):
            text = '가상인\n' + field + '0212345678 | sample@example.invalid'
            raw = [entity(text, '가상인', 'NAME'), entity(text, 'sample@example.invalid', 'EMAIL'),
                   entity(text, '0212345678', 'OTHER')]
            self.assertEqual(recover_financial_spans(text, [], raw)[0], [])

    def test_phone_keyword_cannot_leak_between_fields_or_sentences(self):
        for prefix in ('전화:\n접수번호: ', '전화: 확인 후 기록. 식별자: ',
                       '전화: 확인; 식별자: ', '전화: | 식별자: ', '전화: 그 다음 임의 값 '):
            text = prefix + '0212345678'
            self.assertEqual(recover_financial_spans(text, [], [entity(text, '0212345678', 'OTHER')])[0], [])

    def test_invalid_phone_shapes_and_long_identifiers_rejected(self):
        for value in ('0101234567', '9912345678', 'X0212345678', '02123456789',
                      '0212345678-99', '0212345678 99', '1588-1234'):
            text = '전화: ' + value
            self.assertEqual(recover_financial_spans(text, [], [entity(text, value, 'OTHER')])[0], [])

    def test_partial_or_crossing_raw_evidence_is_insufficient(self):
        text = '전화: 0212345678'
        for value in ('02123', '전화: 0212345678'):
            self.assertEqual(recover_financial_spans(text, [], [entity(text, value, 'OTHER')])[0], [])

    def test_phone_existing_correct_or_split_preserved(self):
        text = '전화: 0212345678'
        for values in (('0212345678',), ('02123', '45678')):
            mapped = [entity(text, value, 'phone_number') for value in values]
            self.assertEqual(recover_financial_spans(text, mapped, [])[0], mapped)

    def test_account_support_label_is_irrelevant_and_both_ends_recovered(self):
        text = '입금 계좌: 가상은행 123-456789-01'
        seed = entity(text, '3-456789', 'account_number')
        for label in ('DRIVER_LICENSE', 'GENERIC_ID', 'OTHER', 'UNKNOWN_NEW_TYPE'):
            raw = [entity(text, '12', label), entity(text, seed.text, 'ACCOUNT_NUMBER'),
                   entity(text, '-01', label)]
            result, _ = recover_financial_spans(text, [seed], raw)
            self.assertEqual(result, [entity(text, '123-456789-01', 'account_number')])
            self.assertEqual(seed.text, '3-456789')

    def test_account_contiguous_variant_supported_with_evidence(self):
        text = '계좌번호: 123456789012'
        seed = entity(text, '3456789012', 'account_number')
        result, _ = recover_financial_spans(text, [seed], [entity(text, '12', 'OTHER')])
        self.assertEqual(result, [entity(text, '123456789012', 'account_number')])

    def test_account_requires_seed_and_missing_digit_evidence(self):
        text = '계좌: 123-456789-01'
        seed = entity(text, '3-456789-01', 'account_number')
        self.assertEqual(recover_financial_spans(text, [], [entity(text, '123-456789-01', 'OTHER')])[0], [])
        for raw in ([], [entity(text, seed.text, 'ACCOUNT_NUMBER')], [entity(text, '1', 'OTHER')]):
            self.assertEqual(recover_financial_spans(text, [seed], raw)[0], [seed])

    def test_account_context_locality_and_conflicts(self):
        for prefix in ('번호: ', '계좌:\n', '계좌: 확인 후 기록. 번호: ',
                       '계좌: | 번호: ', '계좌 운전면허: ', '계좌 연락처: '):
            text = prefix + '123-456789-01'
            seed = entity(text, '3-456789-01', 'account_number')
            raw = [entity(text, '12', 'OTHER')]
            self.assertEqual(recover_financial_spans(text, [seed], raw)[0], [seed])
        text = '계좌: 123-456789-01'
        seed = entity(text, '3-456789-01', 'account_number')
        other = entity(text, '12', 'phone_number')
        self.assertEqual(recover_financial_spans(text, [seed, other], [entity(text, '12', 'OTHER')])[0], [seed, other])

    def test_account_weak_seed_and_larger_raw_entity_rejected(self):
        text = '계좌: 123-456789-01'
        seed = entity(text, '789', 'account_number')
        self.assertEqual(recover_financial_spans(text, [seed], [entity(text, '123-456789-01', 'OTHER')])[0], [seed])
        seed = entity(text, '3-456789-01', 'account_number')
        self.assertEqual(recover_financial_spans(text, [seed], [entity(text, text, 'OTHER')])[0], [seed])

    def test_independent_switches_idempotence_and_no_mutation(self):
        text = '계좌: 123-456789-01\n전화: 0212345678'
        seed = entity(text, '3-456789-01', 'account_number')
        mapped = [seed]
        raw = [entity(text, '12', 'OTHER'), entity(text, '0212345678', 'OTHER')]
        phone_only, _ = recover_financial_spans(text, mapped, raw, account=False)
        account_only, _ = recover_financial_spans(text, mapped, raw, phone=False)
        both, audit = recover_financial_spans(text, mapped, raw)
        self.assertEqual(phone_only[0], seed)
        self.assertEqual(len(phone_only), 2)
        self.assertEqual(len(account_only), 1)
        self.assertEqual(mapped, [seed])
        self.assertEqual(recover_financial_spans(text, both, raw)[0], both)
        self.assertEqual(recover_financial_spans(text, mapped, raw, phone=False, account=False)[0], mapped)
        self.assertTrue(all('text' not in row for row in audit))

    def test_invalid_offsets_rejected(self):
        with self.assertRaises(ValueError):
            recover_financial_spans('전화: 0212345678', [], [Span(0, 2, 'OTHER', 'invalid')])


if __name__ == '__main__':
    unittest.main()
