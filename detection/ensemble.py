"""Live KoELECTRA V2 + rule detection using the frozen selective C policy."""

from dataclasses import dataclass
from pathlib import Path
from time import perf_counter

from detection.base import BaseDetector, Span
from detection.rule.detector import RuleDetector
from detection.selective import rule_checks, selective
from detection.token_cls.detector import LABEL_MAP, Prediction, validate_spans
from detection.token_cls.detector_v2 import TokenClassificationV2Detector


@dataclass
class SelectivePrediction(Prediction):
    """Prediction with per-call milliseconds; raw_dict() retains V2 raw data.

    timings_ms includes v2, rule, combine, and total. total excludes detector
    initialization and file I/O, and includes final span validation. Instances
    own their timings so a later call never overwrites an earlier measurement.
    """

    timings_ms: dict[str, float]

    @property
    def ms(self) -> float:
        """Document processing time for run_compare's optional ms field."""
        return self.timings_ms['total']


class SelectiveEnsembleDetector(BaseDetector):
    """Initialize model/Kiwi once; run V2, rules, then selective C per document.

    predict(text) returns SelectivePrediction; detect(text) returns list[Span].
    Prediction.spans holds C output and the raw fields hold KoELECTRA output.
    No source text or candidate decisions are written to logs or files.
    """

    name = 'koelectra_v2_selective_ensemble'

    def __init__(self, *, cache_dir='models/huggingface', local_files_only=False,
                 max_length=512, stride=128, threads=1):
        try:
            self._v2 = TokenClassificationV2Detector(
                cache_dir=cache_dir, local_files_only=local_files_only,
                max_length=max_length, stride=stride, threads=threads,
            )
            self._rule = RuleDetector()
            self._patterns = rule_checks(Path(__file__).parent / 'rule' / 'detector.py')
        except Exception:
            raise RuntimeError('Selective ensemble initialization failed') from None

    def predict(self, text: str) -> SelectivePrediction:
        if not isinstance(text, str):
            raise TypeError('text must be a string')
        try:
            started = perf_counter()
            v2 = self._v2.predict(text)
            v2_finished = perf_counter()
            rules = self._rule.detect(text)
            rule_finished = perf_counter()
            combined, _ = selective(
                text, [s.to_dict() for s in v2.spans],
                [s.to_dict() for s in rules], self._patterns,
            )
            spans = [Span(e['start'], e['end'], e['label'], text[e['start']:e['end']])
                     for e in combined]
            validate_spans(text, spans, set(LABEL_MAP.values()))
            combined_finished = perf_counter()
            result = SelectivePrediction(
                spans=spans, raw_entities=v2.raw_entities, tokens=v2.tokens,
                windows=v2.windows,
                timings_ms=dict(v2=(v2_finished - started) * 1000,
                                rule=(rule_finished - v2_finished) * 1000,
                                combine=(combined_finished - rule_finished) * 1000),
            )
            result.timings_ms['total'] = (perf_counter() - started) * 1000
            return result
        except Exception:
            # Upstream validation exceptions may embed original span text.
            raise RuntimeError('Selective ensemble prediction failed') from None

    def detect(self, text: str) -> list[Span]:
        return self.predict(text).spans
