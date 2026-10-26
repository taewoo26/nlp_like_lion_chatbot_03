"""Synthetic contracts for the isolated newline-person experiment."""
import traceback
import unittest

from detection.selective import rule_checks
from eval.person_newline_experiment import split_newline_person
from result_paths import ROOT


def entity(text, start=0, end=None, label='person'):
    end = len(text) if end is None else end
    return dict(label=label, start=start, end=end, text=text[start:end])


class NewlinePersonTests(unittest.TestCase):
    def test_only_newline_person_changes_and_offsets_are_original(self):
        text = 'xx 가상인\r\n\r\n예시인 yy'
        before = entity(text, 3, text.index(' yy'))
        result, audit = split_newline_person(text, [before])
        self.assertEqual([(p['start'], p['end']) for p in result], [(3, 6), (10, 13)])
        self.assertEqual(audit[0]['action'], 'split')
        self.assertEqual(before['text'], text[3:13])
        for p in result:
            self.assertEqual(p['text'], text[p['start']:p['end']])

    def test_nonperson_and_no_newline_are_unchanged(self):
        for text, label in [('가상인 예시인', 'person'), ('가상인\n예시인', 'address')]:
            old = entity(text, label=label)
            result, audit = split_newline_person(text, [old])
            self.assertEqual(result, [old])
            self.assertIsNot(result[0], old)
            self.assertEqual(audit, [])

    def test_keeps_horizontal_whitespace_and_one_character_fragments(self):
        text = '가상인 \n 일'
        result, _ = split_newline_person(text, [entity(text)])
        self.assertEqual([p['text'] for p in result], ['가상인 ', ' 일'])

    def test_single_piece_or_only_separators_roll_back(self):
        for text in ['가상인\n', '\r\n', '\n가상인']:
            old = entity(text)
            self.assertEqual(split_newline_person(text, [old])[0], [old])

    def test_context_all_or_keep_does_not_drop_rejected_pieces(self):
        patterns = rule_checks(ROOT / 'detection/rule/detector.py')
        # Neither piece has an original positive context, so preserve parent.
        text = '가상인\n예시인'
        old = entity(text)
        out, audit = split_newline_person(text, [old], patterns=patterns, require_context=True)
        self.assertEqual(out, [old])
        self.assertEqual(audit[0]['action'], 'keep_context_rejected')
        # Both pieces have preceding name-field context outside their slices.
        text = '성명: 가상인\n예시인 고객'
        old = entity(text, start=4, end=text.index(' 고객'))
        out, audit = split_newline_person(text, [old], patterns=patterns, require_context=True)
        self.assertEqual(len(out), 2)
        self.assertEqual(audit[0]['action'], 'split')

    def test_idempotent_and_empty(self):
        text = '가상인\n예시인'
        first, _ = split_newline_person(text, [entity(text)])
        self.assertEqual(split_newline_person(text, first)[0], first)
        self.assertEqual(split_newline_person('', []), ([], []))

    def test_bad_input_does_not_expose_source(self):
        text = 'SYNTHETIC_PRIVATE_VALUE'
        bad = entity(text, end=len(text) + 1)
        try:
            split_newline_person(text, [bad])
        except ValueError:
            self.assertNotIn(text, traceback.format_exc())
        else:
            self.fail('Invalid range accepted')
        with self.assertRaises(ValueError):
            split_newline_person(text, [], require_context=True)


if __name__ == '__main__':
    unittest.main()
