import unittest
from types import SimpleNamespace
from detection.token_cls.veil import VeilDetector, LABEL_MAP


class VeilAdapterTests(unittest.TestCase):
    def test_mapping_preserves_original_offsets(self):
        detector = VeilDetector.__new__(VeilDetector)
        text = '🙂 윤 선영, ０１０'
        detector.runtime = SimpleNamespace(predict=lambda text: [
            {'start': 2, 'end': 6, 'label': 'PERSON'},
            {'start': 8, 'end': 11, 'label': 'PHONE'},
            {'start': 0, 'end': 1, 'label': 'ORGANIZATION'},
        ])
        spans = detector.detect(text)
        self.assertEqual([(s.label, s.text) for s in spans], [('person', '윤 선영'), ('phone_number', '０１０')])
        self.assertEqual(len(LABEL_MAP), 7)

    def test_invalid_offsets_raise(self):
        detector = VeilDetector.__new__(VeilDetector)
        detector.runtime = SimpleNamespace(predict=lambda text: [{'start': -1, 'end': 2, 'label': 'PERSON'}])
        with self.assertRaises(ValueError):
            detector.detect('문장')

    def test_blank_does_not_call_model(self):
        detector = VeilDetector.__new__(VeilDetector)
        self.assertEqual(detector.detect(' \n'), [])


if __name__ == '__main__':
    unittest.main()
