"""Synthetic positive/negative contracts; no model or dataset access."""
import unittest

from detection.base import Span
from detection.token_cls.postprocess import recover_financial_spans


def entity(text, value, label):
    start = text.index(value)
    return Span(start, start + len(value), label, value)


class RecoveryTests(unittest.TestCase):
    def test_phone_requires_format_and_contact_context(self):
        for prefix, number, expected in (
                ('전화: ', '0212345678', True),
                ('직통: ', '031-123-4567', True),
                ('연락처: ', '010 1234 5678', True),
                ('민원번호: ', '0212345678', False),
                ('식별자: ', '0212345678', False),
                ('전화: ', '0101234567', False),
                ('전화: ', '1588-1234', False),
                ('전화: ', '9912345678', False)):
            with self.subTest(prefix=prefix, number=number):
                text = prefix + number
                result, _ = recover_financial_spans(text, [], [entity(text, number, 'GENERIC_ID')])
                self.assertEqual(bool(result), expected)

    def test_personal_contact_card_not_email_alone(self):
        text = '가상인 드림\n운영팀 | 0212345678 | sample@example.invalid'
        number = entity(text, '0212345678', 'GENERIC_ID')
        email = entity(text, 'sample@example.invalid', 'EMAIL')
        name = entity(text, '가상인', 'NAME')
        self.assertEqual(len(recover_financial_spans(text, [], [number, email, name])[0]), 1)
        self.assertEqual(recover_financial_spans(text, [], [number, email])[0], [])
        far = '가상인\n다른 내용\n' + text.split('\n')[1]
        self.assertEqual(recover_financial_spans(far, [], [entity(far, '가상인', 'NAME'),
            entity(far, number.text, 'GENERIC_ID'), entity(far, email.text, 'EMAIL')])[0], [])

    def test_phone_conflicting_field_and_existing_prediction_block_recovery(self):
        for prefix in ('접수번호 전화: ', '고객센터 전화: ', '대표전화: ', '계좌 연락처: '):
            text = prefix + '0212345678'
            self.assertEqual(recover_financial_spans(text, [], [entity(text, '0212345678', 'GENERIC_ID')])[0], [])
        text = '전화: 0212345678'
        mapped = [entity(text, '0212345678', 'account_number')]
        self.assertEqual(recover_financial_spans(text, mapped, [entity(text, '0212345678', 'GENERIC_ID')])[0], mapped)

    def test_generic_fragment_inside_long_identifier_rejected(self):
        for before, after in (('X', ''), ('', '7'), ('', '-99')):
            text = '전화: ' + before + '0212345678' + after
            self.assertEqual(recover_financial_spans(text, [], [entity(text, '0212345678', 'GENERIC_ID')])[0], [])

    def test_account_recovers_both_ends_without_mutation(self):
        text = '입금 계좌: 가상은행 123-456789-01'
        seed = entity(text, '3-456789', 'account_number')
        raw = [entity(text, '12', 'DRIVER_LICENSE'),
               entity(text, seed.text, 'ACCOUNT_NUMBER'), entity(text, '-01', 'DRIVER_LICENSE')]
        mapped = [seed]
        result, events = recover_financial_spans(text, mapped, raw)
        self.assertEqual(result, [entity(text, '123-456789-01', 'account_number')])
        self.assertEqual(mapped, [seed])
        self.assertEqual(seed.text, '3-456789')
        self.assertEqual(len(events), 1)
        self.assertNotIn('text', events[0])

    def test_account_missing_evidence_or_context_stays_unchanged(self):
        for prefix in ('계좌: ', '번호: ', '계좌:\n', '계좌 운전면허: '):
            text = prefix + '123-456789-01'
            seed = entity(text, '3-456789-01', 'account_number')
            for label in ('DRIVER_LICENSE', 'GENERIC_ID', None):
                raw = [entity(text, seed.text, 'ACCOUNT_NUMBER')]
                if label:
                    raw.append(entity(text, '12', label))
                result, _ = recover_financial_spans(text, [seed], raw)
                self.assertEqual(result != [seed], prefix == '계좌: ' and label == 'DRIVER_LICENSE')

    def test_driver_license_without_account_seed_not_mapped(self):
        text = '계좌: 123-456789-01'
        self.assertEqual(recover_financial_spans(text, [], [entity(text, '123-456789-01', 'DRIVER_LICENSE')])[0], [])

    def test_account_conflicting_mapped_or_raw_label_blocks_expansion(self):
        text = '계좌: 123-456789-01'
        seed = entity(text, '3-456789-01', 'account_number')
        raw = [entity(text, '12', 'DRIVER_LICENSE'), entity(text, seed.text, 'ACCOUNT_NUMBER')]
        other = entity(text, '12', 'resident_registration_number')
        self.assertEqual(recover_financial_spans(text, [seed, other], raw)[0], [seed, other])
        self.assertEqual(recover_financial_spans(text, [seed], raw + [entity(text, '12', 'PHONE')])[0], [seed])

    def test_account_does_not_expand_across_unknown_digits(self):
        text = '계좌: 123-456789-01'
        seed = entity(text, '3-456789-01', 'account_number')
        raw = [entity(text, '1', 'DRIVER_LICENSE'), entity(text, seed.text, 'ACCOUNT_NUMBER')]
        self.assertEqual(recover_financial_spans(text, [seed], raw)[0], [seed])

    def test_correct_predictions_unchanged_and_switches_independent(self):
        text = '계좌: 123-456789-01\n전화: 0212345678'
        seed = entity(text, '3-456789-01', 'account_number')
        raw = [entity(text, '12', 'DRIVER_LICENSE'), entity(text, seed.text, 'ACCOUNT_NUMBER'),
               entity(text, '0212345678', 'GENERIC_ID')]
        phone_only, _ = recover_financial_spans(text, [seed], raw, account=False)
        account_only, _ = recover_financial_spans(text, [seed], raw, phone=False)
        self.assertEqual(phone_only[0], seed)
        self.assertEqual(len(phone_only), 2)
        self.assertEqual(account_only, [entity(text, '123-456789-01', 'account_number')])
        corrected, _ = recover_financial_spans(text, [seed], raw)
        again, events = recover_financial_spans(text, corrected, raw)
        self.assertEqual(again, corrected)
        self.assertEqual(events, [])
        self.assertEqual(recover_financial_spans(text, [seed], raw, phone=False, account=False)[0], [seed])

    def test_invalid_offsets_fail_closed(self):
        with self.assertRaises(ValueError):
            recover_financial_spans('전화: 0212345678', [], [Span(0, 2, 'GENERIC_ID', 'wrong')])

    def test_normal_target_and_unrelated_spans_preserved(self):
        text = '가상인 계좌: 123-456789-01\n전화: 0212345678'
        mapped = [entity(text, '가상인', 'person'), entity(text, '123-456789-01', 'account_number'),
                  entity(text, '0212345678', 'phone_number')]
        raw = [entity(text, '가상인', 'NAME'), entity(text, '123-456789-01', 'ACCOUNT_NUMBER'),
               entity(text, '0212345678', 'GENERIC_ID')]
        result, events = recover_financial_spans(text, mapped, raw)
        self.assertEqual(result, mapped)
        self.assertEqual(events, [])

    def test_evaluation_distinguishes_new_boundary_fp_and_spurious(self):
        from detection.token_cls.replay_improved import evaluate
        gold = dict(label='account_number', start=0, end=2)
        docs = [dict(id='synthetic', text='1234 abc', gold=[gold])]
        before = [[dict(gold)]]
        after = [[dict(label='account_number', start=0, end=4), dict(label='person', start=5, end=8)]]
        values = evaluate(docs, after, before)
        self.assertEqual(len(values['new_spurious']), 1)
        self.assertEqual(len(values['new_strict_fp']), 2)
        self.assertEqual(len(values['new_partial_fp']), 1)
        self.assertEqual(len(values['lost_baseline_exact']), 1)
        self.assertEqual(values['newly_masked_non_gold_chars'], 5)


if __name__ == '__main__':
    unittest.main()
