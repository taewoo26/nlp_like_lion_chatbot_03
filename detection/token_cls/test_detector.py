"""Decoder contract tests; runnable without model downloads."""
import unittest

from detection.base import Span
from detection.token_cls.detector import decode_bio, validate_spans


class DecoderTests(unittest.TestCase):
    def test_spaces_and_suffixes_are_not_corrected(self):
        text = '윤 선영은'
        tokens = [dict(start=0, end=1, tag='B-NAME'),
                  dict(start=2, end=5, tag='I-NAME')]
        self.assertEqual(decode_bio(text, tokens), [Span(0, 5, 'NAME', text)])

    def test_beginnings_and_type_changes(self):
        tokens = [dict(start=i, end=i+1, tag=t) for i, t in
                  enumerate(['I-NAME', 'B-NAME', 'I-PHONE', 'O', 'I-NAME'])]
        self.assertEqual([(s.label, s.start, s.end) for s in decode_bio('가나다라마', tokens)],
                         [('NAME', 0, 1), ('NAME', 1, 2), ('PHONE', 2, 3), ('NAME', 4, 5)])

    def test_duplicate_and_bad_offsets_rejected(self):
        s = Span(0, 1, 'person', '가')
        with self.assertRaises(ValueError):
            validate_spans('가', [s, s], {'person'})
        for bad in [Span(0, 2, 'person', '가'), Span(0, 1, 'person', '나'),
                    Span(0, 1, 'NAME', '가')]:
            with self.assertRaises(ValueError):
                validate_spans('가', [bad], {'person'})

    def test_invalid_tag_rejected(self):
        with self.assertRaises(ValueError):
            decode_bio('가', [dict(start=0, end=1, tag='S-NAME')])

    def test_empty_text(self):
        self.assertEqual(decode_bio('', []), [])


if __name__ == '__main__':
    unittest.main()
