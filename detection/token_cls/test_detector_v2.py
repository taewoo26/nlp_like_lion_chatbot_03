"""Integration contracts without loading model weights or using private data."""

from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import traceback
import unittest
from unittest.mock import patch

from detection.base import BaseDetector, Span
from detection.token_cls import TokenClassificationV2Detector
from detection.token_cls.detector import Prediction


class V2DetectorTests(unittest.TestCase):
    def setUp(self):
        factory = patch('detection.token_cls.detector_v2.TokenClassificationDetector')
        self.factory = factory.start()
        self.addCleanup(factory.stop)
        self.baseline = self.factory.return_value

    def test_model_initialized_once_and_reused_with_original_options(self):
        self.baseline.predict.return_value = Prediction([], [], [], 1)
        detector = TokenClassificationV2Detector(
            cache_dir='cache', local_files_only=True,
            max_length=256, stride=64, threads=2,
        )
        self.factory.assert_called_once_with(
            cache_dir='cache', local_files_only=True,
            max_length=256, stride=64, threads=2,
        )
        self.assertIsInstance(detector, BaseDetector)
        detector.predict('first document')
        detector.detect('second document')
        self.factory.assert_called_once()
        self.assertEqual(self.baseline.predict.call_count, 2)

    def test_phone_recovery_preserves_original_prediction_and_raw_interface(self):
        text = '전화: 0212345678'
        raw = [Span(4, len(text), 'OTHER', text[4:])]
        original = Prediction([], raw, [dict(tag='B-OTHER')], 2)
        self.baseline.predict.return_value = original
        detector = TokenClassificationV2Detector()
        output = StringIO()
        with redirect_stdout(output), redirect_stderr(output):
            result = detector.predict(text)
        self.assertEqual(output.getvalue(), '')
        self.assertEqual(result.spans, [Span(4, len(text), 'phone_number', text[4:])])
        self.assertEqual(original.spans, [])
        self.assertIs(result.raw_entities, original.raw_entities)
        self.assertIs(result.tokens, original.tokens)
        self.assertEqual(result.windows, original.windows)
        self.assertEqual(result.raw_dict(), original.raw_dict())
        self.assertEqual(detector.detect(text), result.spans)

    def test_account_extension_uses_raw_support(self):
        text = '계좌: 123-456789-01'
        seed = Span(6, len(text), 'account_number', text[6:])
        raw = [Span(4, 6, 'OTHER', text[4:6])]
        self.baseline.predict.return_value = Prediction([seed], raw, [], 1)
        result = TokenClassificationV2Detector().detect(text)
        self.assertEqual(result, [Span(4, len(text), 'account_number', text[4:])])

    def test_conflicting_mapped_entity_is_preserved(self):
        text = '전화: 0212345678'
        mapped = [Span(4, len(text), 'person', text[4:])]
        raw = [Span(4, len(text), 'OTHER', text[4:])]
        self.baseline.predict.return_value = Prediction(mapped, raw, [], 1)
        self.assertEqual(TokenClassificationV2Detector().detect(text), mapped)

    def test_empty_and_invalid_inputs(self):
        self.baseline.predict.return_value = Prediction([], [], [], 1)
        detector = TokenClassificationV2Detector()
        self.assertEqual(detector.detect(''), [])
        self.baseline.predict.reset_mock()
        for value in (None, 1, []):
            with self.assertRaises(TypeError):
                detector.detect(value)
        self.baseline.predict.assert_not_called()

    def test_inference_and_postprocessing_errors_hide_source_in_traceback(self):
        detector = TokenClassificationV2Detector()
        secret = 'SYNTHETIC_PRIVATE_VALUE'
        for phase in ('inference', 'postprocessing'):
            with self.subTest(phase=phase):
                self.baseline.predict.side_effect = None
                self.baseline.predict.return_value = Prediction([], [], [], 1)
                with patch('detection.token_cls.detector_v2.recover_financial_spans') as recover:
                    if phase == 'inference':
                        self.baseline.predict.side_effect = ValueError(secret)
                    else:
                        recover.side_effect = ValueError(secret)
                    try:
                        detector.predict(secret)
                    except RuntimeError:
                        rendered = traceback.format_exc()
                    else:
                        self.fail('Prediction failure was not propagated')
                    self.assertNotIn(secret, rendered)
                    self.assertIn('KoELECTRA V2 prediction failed', rendered)


if __name__ == '__main__':
    unittest.main()
