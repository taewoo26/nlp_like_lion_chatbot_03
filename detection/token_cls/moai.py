"""MoAI INT8 CPU adapter; constrained BIOES Viterbi in NumPy.

References:
https://huggingface.co/BCCard/MoAI-Privacy-Filter-INT8
https://github.com/openai/privacy-filter/blob/main/opf/_core/decoding.py
The recurrence/transition semantics follow the public decoder, reimplemented
without torch. End-to-end parity with BC Card's private evaluation is unverified.
"""
import json
from pathlib import Path

import numpy as np

from detection.base import BaseDetector, Span

REVISION = "60731c85761bd46d23db2e742082a7223696e1c1"
LABEL_MAP = {
    "PERSON": "person", "PHONE": "phone_number", "EMAIL": "email",
    "ADDRESS": "address", "RRN": "resident_registration_number",
    "ACCOUNT_NUMBER": "account_number", "CARD_NUMBER": "card_number",
}
BIAS_NAMES = (
    "background_stay", "background_to_start", "end_to_background",
    "end_to_start", "inside_to_continue", "inside_to_end",
)


class BIOESDecoder:
    def __init__(self, id2label, biases):
        self.labels = [id2label[str(i)] for i in range(len(id2label))]
        self.tags = [("O", None) if x == "O" else tuple(x.split("-", 1))
                     for x in self.labels]
        if "O" not in self.labels or any(t not in "BIOES" for t, _ in self.tags):
            raise ValueError("Invalid BIOES label configuration")
        expected = {"transition_bias_" + name for name in BIAS_NAMES}
        if set(biases) != expected or not all(np.isfinite(float(v)) for v in biases.values()):
            raise ValueError("Invalid Viterbi calibration")
        n = len(self.labels)
        self.start = np.array([0.0 if t in ("O", "B", "S") else -np.inf for t, _ in self.tags])
        self.end = np.array([0.0 if t in ("O", "E", "S") else -np.inf for t, _ in self.tags])
        self.transitions = np.full((n, n), -np.inf)
        for i, (prev, category) in enumerate(self.tags):
            for j, (nxt, next_category) in enumerate(self.tags):
                key = None
                if prev in ("O", "E", "S") and nxt in ("O", "B", "S"):
                    key = ("background_" if prev == "O" else "end_") + (
                        ("stay" if prev == "O" else "to_background") if nxt == "O" else "to_start")
                elif prev in ("B", "I") and category == next_category and nxt in ("I", "E"):
                    key = "inside_to_continue" if nxt == "I" else "inside_to_end"
                if key:
                    self.transitions[i, j] = biases["transition_bias_" + key]

    def decode(self, logits):
        logits = np.asarray(logits, dtype=np.float64)
        if logits.ndim != 2 or logits.shape[1] != len(self.labels) or not np.isfinite(logits).all():
            raise ValueError("Invalid token logits")
        if not len(logits):
            return []
        # Log-softmax subtracts the same constant from all paths per token;
        # raw logits therefore give the same maximum-scoring path.
        score = logits[0] + self.start
        back = []
        for row in logits[1:]:
            candidates = score[:, None] + self.transitions
            previous = candidates.argmax(axis=0)
            back.append(previous)
            score = candidates[previous, np.arange(len(self.labels))] + row
        path = [int((score + self.end).argmax())]
        for previous in reversed(back):
            path.append(int(previous[path[-1]]))
        return path[::-1]

    def spans(self, text, offsets, path):
        if len(offsets) != len(path):
            raise ValueError("Offset and label counts differ")
        spans = []
        begin = None
        for i, label_id in enumerate(path):
            tag, category = self.tags[label_id]
            if tag in ("B", "S"):
                begin = int(offsets[i][0])
            if tag in ("E", "S"):
                end = int(offsets[i][1])
                start = begin
                if start is None or not 0 <= start <= end <= len(text):
                    raise ValueError("Invalid entity offsets")
                # Boundary whitespace only; do not remove Korean particles/titles.
                while start < end and text[start].isspace():
                    start += 1
                while end > start and text[end - 1].isspace():
                    end -= 1
                if start < end and category in LABEL_MAP:
                    spans.append(Span(start, end, LABEL_MAP[category], text[start:end]))
                begin = None
        return sorted({(s.start, s.end, s.label): s for s in spans}.values(), key=lambda s: (s.start, s.end, s.label))


class MoAIDetector(BaseDetector):
    name = "moai"

    def __init__(self, model_dir=None, threads=2):
        import onnxruntime as ort
        from transformers import AutoTokenizer

        if threads < 1:
            raise ValueError("threads must be positive")
        self.model_dir = Path(model_dir) if model_dir else (
            Path(__file__).resolve().parents[2] / "models" / "moai-int8" / REVISION)
        if not (self.model_dir / "model_quantized.onnx").is_file():
            raise FileNotFoundError("모델 준비 필요: python -m eval.moai_smoke --revision " + REVISION)
        config = json.loads((self.model_dir / "config.json").read_text())
        calibration = json.loads((self.model_dir / "viterbi_calibration.json").read_text())
        self.decoder = BIOESDecoder(config["id2label"], calibration["operating_points"]["default"]["biases"])
        self.tokenizer = AutoTokenizer.from_pretrained(self.model_dir, local_files_only=True)
        options = ort.SessionOptions()
        options.intra_op_num_threads = threads
        options.inter_op_num_threads = 1
        self.session = ort.InferenceSession(str(self.model_dir / "model_quantized.onnx"),
                                            sess_options=options, providers=["CPUExecutionProvider"])
        self.detect("모델 준비 확인.")  # warmup excluded from per-document timer

    def detect(self, text):
        encoded = self.tokenizer(text, add_special_tokens=False, return_offsets_mapping=True,
                                 truncation=False, return_tensors="np")
        length = encoded["input_ids"].shape[1]
        if length == 0:
            return []
        total = np.zeros((length, len(self.decoder.labels)), dtype=np.float64)
        counts = np.zeros((length, 1))
        # 1024-token windows, 128-token overlap, mean logits in overlapping areas.
        for start in range(0, length, 896):
            end = min(start + 1024, length)
            feeds = {entry.name: np.asarray(encoded[entry.name][:, start:end], dtype=np.int64)
                     for entry in self.session.get_inputs()}
            logits = self.session.run(["logits"], feeds)[0]
            if logits.shape != (1, end - start, len(self.decoder.labels)):
                raise ValueError(f"Unexpected logits shape: {logits.shape}")
            total[start:end] += logits[0]
            counts[start:end] += 1
            if end == length:
                break
        path = self.decoder.decode(total / counts)
        return self.decoder.spans(text, encoded["offset_mapping"][0], path)


def main():
    import time
    detector = MoAIDetector()
    text = "김민수 고객의 연락처는 010-1234-5678입니다."
    start = time.perf_counter()
    spans = detector.detect(text)
    print(f"입력: {text}")
    for span in spans:
        print(span.to_dict())
    print(f"전체 탐지: {(time.perf_counter() - start) * 1000:.1f}ms")


if __name__ == "__main__":
    main()
