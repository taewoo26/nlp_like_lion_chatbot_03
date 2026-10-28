import json
from pathlib import Path

import numpy as np
import onnxruntime as ort
from huggingface_hub import snapshot_download
from transformers import PreTrainedTokenizerFast

from detection.base import BaseDetector, Span

MODEL_ID = "BCCard/MoAI-Privacy-Filter-INT8"

# 모델 29종 라벨 → detection.base LABELS 매핑 (대응 없는 라벨은 None)
_LABEL_MAP = {
    "PERSON": "person",
    "PHONE": "phone_number",
    "EMAIL": "email",
    "ADDRESS": "address",
    "RRN": "resident_registration_number",
    "ACCOUNT_NUMBER": "account_number",
    "CARD_NUMBER": "card_number",
}


def _build_transition_mask(id2label: dict[int, str]) -> np.ndarray:
    """유효한 BIOES 전이만 허용하는 마스크 행렬 (num_labels x num_labels).

    유효 전이 규칙:
      O      → O, B-*, S-*
      B-X    → I-X, E-X  (동일 라벨만)
      I-X    → I-X, E-X  (동일 라벨만)
      E-X    → O, B-*, S-*
      S-X    → O, B-*, S-*
    """
    n = len(id2label)
    mask = np.full((n, n), -1e9, dtype=np.float32)

    for prev_id, prev_tag in id2label.items():
        for curr_id, curr_tag in id2label.items():
            if _valid_transition(prev_tag, curr_tag):
                mask[prev_id, curr_id] = 0.0

    return mask


def _valid_transition(prev: str, curr: str) -> bool:
    if prev == "O":
        return curr == "O" or curr.startswith("B-") or curr.startswith("S-")

    prev_prefix, _, prev_label = prev.partition("-")

    if prev_prefix in ("B", "I"):
        return curr == f"I-{prev_label}" or curr == f"E-{prev_label}"

    if prev_prefix in ("E", "S"):
        return curr == "O" or curr.startswith("B-") or curr.startswith("S-")

    return False


def _viterbi_decode(
    logits: np.ndarray,          # (seq_len, num_labels)
    transition_mask: np.ndarray,  # (num_labels, num_labels)
    biases: dict,
) -> list[int]:
    """Constrained BIOES Viterbi 디코딩."""
    seq_len, num_labels = logits.shape

    # 전이 행렬에 바이어스 적용 (현재 default는 모두 0.0)
    trans = transition_mask.copy()

    viterbi = np.full((seq_len, num_labels), -1e9, dtype=np.float32)
    backptr = np.zeros((seq_len, num_labels), dtype=np.int32)

    viterbi[0] = logits[0]

    for t in range(1, seq_len):
        # (num_labels,1) + (num_labels, num_labels) → (num_labels, num_labels)
        scores = viterbi[t - 1, :, None] + trans  # prev x curr
        best_prev = scores.argmax(axis=0)          # (num_labels,)
        viterbi[t] = scores[best_prev, np.arange(num_labels)] + logits[t]
        backptr[t] = best_prev

    # 역추적
    best_last = int(viterbi[-1].argmax())
    tags = [best_last]
    for t in range(seq_len - 1, 0, -1):
        tags.append(int(backptr[t, tags[-1]]))
    tags.reverse()

    return tags


def _tags_to_spans(tag_ids: list[int], id2label: dict[int, str], offsets: list[tuple], text: str) -> list[tuple]:
    """BIOES 태그 ID 시퀀스 → (start, end, label) 리스트."""
    spans = []
    current: dict | None = None

    for tag_id, (char_start, char_end) in zip(tag_ids, offsets):
        if char_start == char_end:
            continue

        tag = id2label[tag_id]

        if tag == "O":
            current = None
            continue

        prefix, _, label = tag.partition("-")

        if prefix == "S":
            spans.append((char_start, char_end, label))
            current = None
        elif prefix == "B":
            current = {"label": label, "start": char_start}
        elif prefix == "E" and current and current["label"] == label:
            spans.append((current["start"], char_end, label))
            current = None
        elif prefix == "I":
            pass
        else:
            current = None

    return spans


class MoaiDetector(BaseDetector):
    name = "hf_moai"

    def __init__(self):
        model_dir = Path(snapshot_download(repo_id=MODEL_ID))

        # AutoTokenizer는 비표준 TokenizersBackend 클래스를 인식하지 못해 직접 로드
        self._tokenizer = PreTrainedTokenizerFast(
            tokenizer_file=str(model_dir / "tokenizer.json"),
        )
        self._session = ort.InferenceSession(
            str(model_dir / "model_quantized.onnx"),
            providers=["CPUExecutionProvider"],
        )

        config = json.loads((model_dir / "config.json").read_text(encoding="utf-8"))
        self._id2label: dict[int, str] = {
            int(k): v for k, v in config["id2label"].items()
        }

        cal = json.loads((model_dir / "viterbi_calibration.json").read_text(encoding="utf-8"))
        self._biases: dict = cal["operating_points"]["default"]["biases"]
        self._transition_mask = _build_transition_mask(self._id2label)

    def detect(self, text: str) -> list[Span]:
        encoded = self._tokenizer(
            text,
            add_special_tokens=False,
            truncation=True,
            max_length=1024,
            return_offsets_mapping=True,
            return_tensors="np",
        )
        offsets: list[tuple] = encoded.pop("offset_mapping")[0].tolist()

        feeds = {
            inp.name: np.asarray(encoded[inp.name], dtype=np.int64)
            for inp in self._session.get_inputs()
        }
        logits: np.ndarray = self._session.run(["logits"], feeds)[0][0]  # (seq_len, num_labels)

        tag_ids = _viterbi_decode(logits, self._transition_mask, self._biases)
        raw_spans = _tags_to_spans(tag_ids, self._id2label, offsets, text)

        spans = []
        for start, end, model_label in raw_spans:
            base_label = _LABEL_MAP.get(model_label)
            if base_label is None:
                continue
            spans.append(Span(start=start, end=end, label=base_label, text=text[start:end]))

        return self.postprocess(text, spans)
