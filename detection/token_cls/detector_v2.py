"""KoELECTRA inference followed by the unchanged financial V2 postprocessor."""

from dataclasses import replace

from detection.base import BaseDetector, Span
from detection.token_cls.detector import Prediction, TokenClassificationDetector
from detection.token_cls.postprocess_v2 import recover_financial_spans


class TokenClassificationV2Detector(BaseDetector):
    """Load once, then reuse predict(text) or detect(text) across documents.

    predict() returns the existing Prediction interface: spans contain V2
    results, while raw_entities, tokens and windows retain inference output.
    Neither document contents nor postprocessing decisions are logged.
    """

    name = "koelectra_token_cls_v2"

    def __init__(self, *, cache_dir="models/huggingface", local_files_only=False,
                 max_length=512, stride=128, threads=1):
        self._detector = TokenClassificationDetector(
            cache_dir=cache_dir, local_files_only=local_files_only,
            max_length=max_length, stride=stride, threads=threads,
        )

    def predict(self, text: str) -> Prediction:
        if not isinstance(text, str):
            raise TypeError("text must be a string")
        try:
            prediction = self._detector.predict(text)
            recovered, _ = recover_financial_spans(
                text, prediction.spans, prediction.raw_entities,
            )
            return replace(prediction, spans=recovered)
        except Exception:
            # Existing span-validation errors can include source text.
            raise RuntimeError("KoELECTRA V2 prediction failed") from None

    def detect(self, text: str) -> list[Span]:
        return self.predict(text).spans
