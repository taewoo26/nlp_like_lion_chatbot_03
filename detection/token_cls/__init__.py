"""Frozen token classification baseline and its V2 integration."""

from detection.token_cls.detector import TokenClassificationDetector
from detection.token_cls.detector_v2 import TokenClassificationV2Detector

__all__ = ["TokenClassificationDetector", "TokenClassificationV2Detector"]
