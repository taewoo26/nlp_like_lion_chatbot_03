"""CPU-independent checks for constrained decoding and character reconstruction."""
import itertools
import unittest
from types import SimpleNamespace

import numpy as np
from detection.token_cls.moai import BIOESDecoder, BIAS_NAMES, MoAIDetector


class DecoderTests(unittest.TestCase):
    def setUp(self):
        self.labels = dict(enumerate(['O', 'B-PERSON', 'I-PERSON', 'E-PERSON', 'S-PERSON']))
        self.biases = {'transition_bias_' + k: 0.0 for k in BIAS_NAMES}
        self.decoder = BIOESDecoder({str(k): v for k, v in self.labels.items()}, self.biases)

    def test_matches_exhaustive_valid_paths(self):
        rng = np.random.default_rng(12)
        for length in (1, 2, 4):
            logits = rng.normal(size=(length, 5))
            valid = []
            for path in itertools.product(range(5), repeat=length):
                if path[0] not in (0, 1, 4) or path[-1] not in (0, 3, 4):
                    continue
                if any((b not in (2, 3) if a in (1, 2) else b not in (0, 1, 4))
                       for a, b in zip(path, path[1:])):
                    continue
                valid.append(path)
            best = max(valid, key=lambda p: sum(logits[t, k] for t, k in enumerate(p)))
            self.assertEqual(self.decoder.decode(logits), list(best))

    def test_forbids_unfinished_or_initial_inside(self):
        self.assertEqual(self.decoder.decode([[0, 5, 100, 100, 2]]), [4])

    def test_bias_changes_path(self):
        biases = {**self.biases, 'transition_bias_background_to_start': 10.0}
        decoder = BIOESDecoder({str(k): v for k, v in self.labels.items()}, biases)
        logits = [[5, -100, -100, -100, -100], [1, -100, -100, -100, 0]]
        self.assertEqual(self.decoder.decode(logits), [0, 0])
        self.assertEqual(decoder.decode(logits), [0, 4])

    def test_whitespace_and_unicode_offsets(self):
        text = '🙂 윤 선영 님'
        offsets = [(0, 1), (1, 3), (3, 6), (6, 7), (7, 8)]
        spans = self.decoder.spans(text, offsets, [0, 1, 2, 3, 0])
        self.assertEqual([(s.start, s.end, s.text) for s in spans], [(2, 6, '윤 선영')])

    def test_empty(self):
        self.assertEqual(self.decoder.decode(np.zeros((0, 5))), [])

    def test_window_overlap_and_tail_are_preserved(self):
        detector = MoAIDetector.__new__(MoAIDetector)
        detector.decoder = self.decoder
        length = 2050
        calls = []
        def tokenize(text, **kwargs):
            return {'input_ids': np.arange(length)[None, :],
                    'offset_mapping': np.array([[(i, i + 1) for i in range(length)]])}
        def run(outputs, feeds):
            ids = feeds['input_ids'][0]
            calls.append((int(ids[0]), int(ids[-1])))
            logits = np.full((1, len(ids), 5), -50.0)
            logits[:, :, 0] = 5.0
            for index, token in enumerate(ids):
                if token in (1000, length - 1):
                    logits[0, index, 4] = 10.0
            return [logits]
        detector.tokenizer = tokenize
        detector.session = SimpleNamespace(
            get_inputs=lambda: [SimpleNamespace(name='input_ids')], run=run)
        spans = detector.detect('가' * length)
        self.assertEqual([(s.start, s.end) for s in spans], [(1000, 1001), (2049, 2050)])
        self.assertEqual(calls, [(0, 1023), (896, 1919), (1792, 2049)])


if __name__ == '__main__':
    unittest.main()
