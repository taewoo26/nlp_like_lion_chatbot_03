import unittest
from types import SimpleNamespace
import numpy as np
from detection.token_cls.koelectra import bio_to_spans, KoElectraDetector


class KoElectraTests(unittest.TestCase):
    def test_bio_and_original_unicode_offsets(self):
        text = '🙂 윤 선영 김민준'
        spans = bio_to_spans(text, [(0, 1), (2, 3), (4, 6), (7, 9), (9, 10)],
                             ['O', 'B-NAME', 'I-NAME', 'B-NAME', 'I-NAME'])
        self.assertEqual([(s.start, s.end, s.text) for s in spans], [(2, 6, '윤 선영'), (7, 10, '김민준')])

    def test_unmatched_i_and_excluded_label(self):
        spans = bio_to_spans('가나다', [(0, 1), (1, 2), (2, 3)], ['I-NAME', 'B-PLACE', 'I-PHONE'])
        self.assertEqual([(s.label, s.text) for s in spans], [('person', '가'), ('phone_number', '다')])

    def test_o_splits_entity(self):
        spans = bio_to_spans('가나다', [(0, 1), (1, 2), (2, 3)], ['B-NAME', 'O', 'I-NAME'])
        self.assertEqual([s.text for s in spans], ['가', '다'])

    def test_long_input_and_overlap(self):
        detector = KoElectraDetector.__new__(KoElectraDetector)
        detector.labels = ['O', 'B-NAME', 'I-NAME']
        calls = []
        class Tokenizer:
            cls_token_id = 5000
            sep_token_id = 5001
            def __call__(self, text, **kwargs):
                return {'input_ids': list(range(len(text))), 'offset_mapping': [(i, i + 1) for i in range(len(text))]}
        def run(_, feeds):
            ids = feeds['input_ids'][0]
            self.assertEqual((ids[0], ids[-1]), (5000, 5001))
            self.assertLessEqual(len(ids), 512)
            calls.append((ids[1], ids[-2]))
            logits = np.zeros((1, len(ids), 3))
            logits[:, :, 0] = 1
            for j, token in enumerate(ids):
                if token == 1000: logits[0, j, 1] = 10
                if token in (1001, 1002): logits[0, j, 2] = 10
            return [logits]
        detector.tokenizer = Tokenizer()
        detector.session = SimpleNamespace(get_inputs=lambda: [SimpleNamespace(name='input_ids')], run=run)
        spans = detector.detect('가' * 1100)
        self.assertEqual([(s.start, s.end) for s in spans], [(1000, 1003)])
        self.assertEqual(calls, [(0, 509), (382, 891), (764, 1099)])


if __name__ == '__main__':
    unittest.main()
