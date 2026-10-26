"""Synthetic live-ensemble contracts, without loading model/Kiwi instances."""

from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import traceback
import unittest
from unittest.mock import patch

from detection.base import BaseDetector, Span
from detection.ensemble import SelectiveEnsembleDetector
from detection.selective import selective, valid_rule
from detection.token_cls.detector import Prediction
from eval import ensemble_experiment


class EnsembleTests(unittest.TestCase):
    def setUp(self):
        for name in ('TokenClassificationV2Detector', 'RuleDetector'):
            patcher = patch('detection.ensemble.' + name)
            setattr(self, name, patcher.start())
            self.addCleanup(patcher.stop)
        self.v2 = self.TokenClassificationV2Detector.return_value
        self.rule = self.RuleDetector.return_value
        self.v2.predict.return_value = Prediction([], [], [], 1)
        self.rule.detect.return_value = []

    def test_shared_policy_is_used_by_experiment(self):
        self.assertIs(ensemble_experiment.selective, selective)
        self.assertIs(ensemble_experiment.valid_rule, valid_rule)

    def test_initialize_once_and_run_stages_in_order_on_each_call(self):
        events = []
        self.v2.predict.side_effect = lambda text: (events.append('v2') or Prediction([], [], [], 1))
        self.rule.detect.side_effect = lambda text: (events.append('rule') or [])
        detector = SelectiveEnsembleDetector(
            cache_dir='cache', local_files_only=True,
            max_length=256, stride=64, threads=2,
        )
        self.assertIsInstance(detector, BaseDetector)
        self.TokenClassificationV2Detector.assert_called_once_with(
            cache_dir='cache', local_files_only=True,
            max_length=256, stride=64, threads=2,
        )
        with patch('detection.ensemble.selective', wraps=selective) as combine:
            detector.predict('first')
            detector.detect('second')
            self.assertEqual(combine.call_count, 2)
        self.assertEqual(events, ['v2', 'rule', 'v2', 'rule'])
        self.TokenClassificationV2Detector.assert_called_once()
        self.RuleDetector.assert_called_once_with()

    def test_add_expand_duplicate_and_conflicts_keep_raw_fields(self):
        text = '전화: 0212345678'
        whole = Span(4, 14, 'phone_number', text[4:14])
        seed = Span(6, 14, 'phone_number', text[6:14])
        self.rule.detect.return_value = [whole]
        detector = SelectiveEnsembleDetector()
        cases = [([], [whole]), ([seed], [whole]), ([whole], [whole]),
                 ([Span(4, 8, 'account_number', text[4:8])], None),
                 ([Span(2, 8, 'phone_number', text[2:8])], None),
                 ([Span(4, 8, 'phone_number', text[4:8]),
                   Span(10, 14, 'phone_number', text[10:14])], None)]
        for baseline, expected in cases:
            with self.subTest(spans=len(baseline), label=baseline[0].label if baseline else ''):
                raw = Prediction(baseline, [seed], [dict(tag='B-PHONE')], 2)
                self.v2.predict.return_value = raw
                result = detector.predict(text)
                self.assertIsInstance(result, Prediction)
                self.assertEqual(result.spans, baseline if expected is None else expected)
                self.assertEqual(raw.spans, baseline)
                self.assertIs(result.raw_entities, raw.raw_entities)
                self.assertIs(result.tokens, raw.tokens)
                self.assertEqual(result.windows, raw.windows)
                self.assertEqual(result.raw_dict(), raw.raw_dict())

    def test_invalid_evidence_and_cross_label_containment_are_rejected(self):
        text = '전화: 0212345678'
        detector = SelectiveEnsembleDetector()
        self.rule.detect.return_value = [Span(4, 8, 'phone_number', text[4:8])]
        self.assertEqual(detector.detect(text), [])
        outer = Span(4, 14, 'account_number', text[4:14])
        self.v2.predict.return_value = Prediction([outer], [], [], 1)
        self.assertEqual(detector.detect(text), [outer])

    def test_name_context_and_exclusion_order(self):
        detector = SelectiveEnsembleDetector()
        for text, accepted in [('가상인 고객', True), ('가상인 카드', False),
                               ('성명: 가상인 카드', False), ('가상인 일반문장', False)]:
            start = text.index('가상인')
            self.rule.detect.return_value = [Span(start, start + 3, 'person', text[start:start + 3])]
            self.assertEqual(bool(detector.detect(text)), accepted)

    def test_timing_is_per_prediction_and_supports_scorer_row(self):
        detector = SelectiveEnsembleDetector()
        with patch('detection.ensemble.perf_counter', side_effect=[0, .01, .03, .034, .035]):
            first = detector.predict('document')
        for stage, expected in [('v2', 10), ('rule', 20), ('combine', 4), ('total', 35)]:
            self.assertAlmostEqual(first.timings_ms[stage], expected)
        second = detector.predict('another document')
        self.assertIsNot(first.timings_ms, second.timings_ms)
        row = dict(id='synthetic', entities=[s.to_dict() for s in first.spans], ms=first.ms)
        self.assertAlmostEqual(row['ms'], 35)

    def test_empty_and_invalid_inputs(self):
        detector = SelectiveEnsembleDetector()
        self.assertEqual(detector.detect(''), [])
        self.v2.predict.reset_mock()
        self.rule.detect.reset_mock()
        for text in (None, 1, []):
            with self.assertRaises(TypeError):
                detector.predict(text)
        self.v2.predict.assert_not_called()
        self.rule.detect.assert_not_called()

    def test_failures_do_not_expose_source_in_exception_or_output(self):
        detector = SelectiveEnsembleDetector()
        secret = 'SYNTHETIC_PRIVATE_VALUE'
        for stage in ('v2', 'rule', 'combine'):
            self.v2.predict.side_effect = None
            self.rule.detect.side_effect = None
            output = StringIO()
            with patch('detection.ensemble.selective', wraps=selective) as combine:
                target = {'v2': self.v2.predict, 'rule': self.rule.detect, 'combine': combine}[stage]
                target.side_effect = ValueError(secret)
                with redirect_stdout(output), redirect_stderr(output):
                    try:
                        detector.predict(secret)
                    except RuntimeError:
                        rendered = traceback.format_exc()
                    else:
                        self.fail('Prediction failure was not propagated')
                self.assertEqual(output.getvalue(), '')
                self.assertNotIn(secret, rendered)

    def test_initialization_failure_is_sanitized(self):
        self.RuleDetector.side_effect = ValueError('SYNTHETIC_PRIVATE_VALUE')
        try:
            SelectiveEnsembleDetector()
        except RuntimeError:
            self.assertNotIn('SYNTHETIC_PRIVATE_VALUE', traceback.format_exc())
        else:
            self.fail('Initialization failure was not propagated')


if __name__ == '__main__':
    unittest.main()
