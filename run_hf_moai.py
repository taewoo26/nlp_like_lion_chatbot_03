"""BCCard/MoAI-Privacy-Filter-INT8 탐지 결과 생성.

사용법
    python run_hf_moai.py                          # dev 기본 경로
    python run_hf_moai.py --input data/dev/pii_synth_finance_dev.jsonl --output preds/hf_moai_dev.jsonl
"""
import argparse

from detection.hf.moai import MoaiDetector

DEFAULT_INPUT = "data/dev/pii_synth_finance_dev.jsonl"
DEFAULT_OUTPUT = "preds/hf_moai_dev.jsonl"

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default=DEFAULT_INPUT)
    ap.add_argument("--output", default=DEFAULT_OUTPUT)
    a = ap.parse_args()

    detector = MoaiDetector()
    print(f"입력: {a.input}")
    detector.predict_file(a.input, a.output)
    print(f"저장: {a.output}")
