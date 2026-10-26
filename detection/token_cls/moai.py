"""기준 토큰 분류 모델: BCCard/MoAI-Privacy-Filter-INT8 (ONNX Runtime).

회사 안내
- ONNX Runtime으로 실행 (transformers pipeline으로는 로드 안 됨)
- 모델 카드의 Viterbi 디코딩 코드를 그대로 써야 공개 성능이 재현됨 → 아래 4.3절 코드 원문 그대로
- 모델 라벨 18종을 우리 7종에 직접 대응

모델 정보 (모델 카드 기준): Apache-2.0 · INT8 ONNX 약 1.6GB · 기반 openai/privacy-filter
· 최대 입력 1024 토큰 · 라벨 18종 × BIOES + O = 73 클래스

설치
    pip install "onnxruntime>=1.28,<1.29" "huggingface-hub>=1.5" "transformers>=5.6" numpy
모델 위치: 환경변수 MOAI_MODEL_DIR (미리 받아둔 폴더) 가 있으면 사용, 없으면 snapshot_download로 자동 다운로드
"""
import json
import os
from pathlib import Path

import numpy as np

from detection.base import BaseDetector, Span

MODEL_ID = "BCCard/MoAI-Privacy-Filter-INT8"
MAX_TOKENS = 1024

# 모델 라벨 18종 → 우리 7종. None = 채점 대상 아님(버림)
LABEL_MAP = {
    "PERSON": "person",
    "PHONE": "phone_number",
    "EMAIL": "email",
    "ADDRESS": "address",
    "RRN": "resident_registration_number",
    "FRN": "resident_registration_number",   # 외국인등록번호: 형식(6-7)이 같아 주민번호로 묶음
    "ACCOUNT_NUMBER": "account_number",
    "CARD_NUMBER": "card_number",
    "VIRTUAL_CARD_NUMBER": "card_number",    # 모델 버전에 따라 있을 수 있음
    "SECRET": None, "USER_ID": None, "PASSPORT": None, "DRIVER_LICENSE": None,
    "GENERIC_ID": None, "ZIPCODE": None, "DATE": None, "CARD_EXPIRY": None,
    "CVC": None, "IPIN": None,
}


# ---------------------------------------------------------------------------
# 모델 카드 4.3절 Viterbi 참조 구현 (원문 그대로, 수정하지 말 것)
# ---------------------------------------------------------------------------
def transition_bias(
    from_prefix: str,
    to_prefix: str,
    biases: dict[str, float],
) -> float:
    if from_prefix == "O" and to_prefix == "O":
        return biases["transition_bias_background_stay"]
    if from_prefix == "O" and to_prefix in {"B", "S"}:
        return biases["transition_bias_background_to_start"]
    if from_prefix in {"E", "S"} and to_prefix == "O":
        return biases["transition_bias_end_to_background"]
    if from_prefix in {"E", "S"} and to_prefix in {"B", "S"}:
        return biases["transition_bias_end_to_start"]
    if from_prefix in {"B", "I"} and to_prefix == "I":
        return biases["transition_bias_inside_to_continue"]
    if from_prefix in {"B", "I"} and to_prefix == "E":
        return biases["transition_bias_inside_to_end"]
    raise ValueError(f"Unsupported transition family: {from_prefix} -> {to_prefix}")


def constrained_viterbi(
    logits: np.ndarray,
    labels: tuple[str, ...],
    biases: dict[str, float],
) -> list[int]:
    scores = np.asarray(logits, dtype=np.float32)
    num_classes = len(labels)
    parsed = [
        ("O", None) if tag == "O" else tuple(tag.split("-", 1))
        for tag in labels
    ]
    start_scores = np.full(num_classes, -np.inf, dtype=np.float32)
    end_scores = np.full(num_classes, -np.inf, dtype=np.float32)
    transition_scores = np.full(
        (num_classes, num_classes),
        -np.inf,
        dtype=np.float32,
    )

    for class_id, (tag_prefix, _) in enumerate(parsed):
        if tag_prefix in {"O", "B", "S"}:
            start_scores[class_id] = 0.0
        if tag_prefix in {"O", "E", "S"}:
            end_scores[class_id] = 0.0

    for from_id, (from_prefix, from_label) in enumerate(parsed):
        for to_id, (to_prefix, to_label) in enumerate(parsed):
            allowed = (
                from_prefix in {"O", "E", "S"}
                and to_prefix in {"O", "B", "S"}
            ) or (
                from_prefix in {"B", "I"}
                and to_prefix in {"I", "E"}
                and from_label == to_label
            )
            if allowed:
                transition_scores[from_id, to_id] = transition_bias(
                    from_prefix,
                    to_prefix,
                    biases,
                )

    delta = start_scores + scores[0]
    backpointers = np.zeros((scores.shape[0], num_classes), dtype=np.int64)
    for token_id in range(1, scores.shape[0]):
        candidates = delta[:, None] + transition_scores
        backpointers[token_id] = np.argmax(candidates, axis=0)
        delta = np.max(candidates, axis=0) + scores[token_id]

    last_class = int(np.argmax(delta + end_scores))
    path = [last_class]
    for token_id in range(scores.shape[0] - 1, 0, -1):
        path.append(int(backpointers[token_id, path[-1]]))
    return list(reversed(path))


def decode_spans(
    tags: list[str],
    offsets: list[list[int]],
    text: str,
) -> list[dict[str, int | str]]:
    spans = []
    current = None

    def flush() -> None:
        nonlocal current
        if current is None:
            return
        start = current["start"]
        end = current["end"]
        while start < end and text[start].isspace():
            start += 1
        while end > start and text[end - 1].isspace():
            end -= 1
        if start < end:
            spans.append(
                {
                    "start": start,
                    "end": end,
                    "label": current["label"],
                }
            )
        current = None

    for tag, (start, end) in zip(tags, offsets, strict=True):
        if tag == "O":
            flush()
            continue
        tag_prefix, label = tag.split("-", 1)
        if tag_prefix in {"B", "S"} or current is None or current["label"] != label:
            flush()
            current = {
                "start": start,
                "end": end,
                "label": label,
            }
        else:
            current["end"] = end
        if tag_prefix in {"E", "S"}:
            flush()
    flush()
    return spans
# --------------------------------------------------------------------------- (원문 끝)


class MoAIDetector(BaseDetector):
    name = "moai"

    def __init__(self, operating_point: str = "default", keep_unmapped: bool = False):
        import onnxruntime as ort
        from transformers import AutoTokenizer

        model_dir = Path(os.getenv("MOAI_MODEL_DIR") or "models/moai")
        if not (model_dir / "model_quantized.onnx").exists():
            from huggingface_hub import snapshot_download
            snapshot_download(repo_id=MODEL_ID, local_dir=str(model_dir))

        self.tokenizer = AutoTokenizer.from_pretrained(model_dir)
        config = json.loads((model_dir / "config.json").read_text(encoding="utf-8"))
        self.labels = tuple(config["id2label"][str(i)] for i in range(len(config["id2label"])))
        calibration = json.loads((model_dir / "viterbi_calibration.json").read_text(encoding="utf-8"))
        self.biases = calibration["operating_points"][operating_point]["biases"]
        self.session = ort.InferenceSession(
            str(model_dir / "model_quantized.onnx"), providers=["CPUExecutionProvider"]
        )
        self.keep_unmapped = keep_unmapped
        # 모델이 실제로 가진 라벨이 LABEL_MAP에 다 있는지 확인 (보고서 '라벨 대응' 근거)
        model_types = {t.split("-", 1)[1] for t in self.labels if t != "O"}
        unknown = model_types - set(LABEL_MAP)
        if unknown:
            print(f"⚠ LABEL_MAP에 없는 모델 라벨: {sorted(unknown)} → 버림 처리")

    def _run(self, text: str) -> list[dict]:
        enc = self.tokenizer(
            text, return_offsets_mapping=True, add_special_tokens=False,
            truncation=True, max_length=MAX_TOKENS, return_tensors="np",
        )
        offsets = enc.pop("offset_mapping")[0].tolist()
        feeds = {
            mi.name: np.asarray(enc[mi.name], dtype=np.int64) for mi in self.session.get_inputs()
        }
        logits = self.session.run(["logits"], feeds)[0]
        length = int(feeds["attention_mask"][0].sum())
        if length >= MAX_TOKENS:
            print(f"⚠ 입력이 {MAX_TOKENS}토큰에서 잘렸습니다 (문서 길이 {len(text)}자). 청크 처리 필요")
        path = constrained_viterbi(logits[0, :length], self.labels, self.biases)
        tags = [self.labels[c] for c in path]
        return decode_spans(tags, offsets[:length], text)

    def detect(self, text: str) -> list[Span]:
        out = []
        for sp in self._run(text):
            mapped = LABEL_MAP.get(sp["label"])
            if mapped is None and not self.keep_unmapped:
                continue
            label = mapped or f"model:{sp['label']}"
            out.append(Span(sp["start"], sp["end"], label, text[sp["start"]:sp["end"]], sp["label"]))
        return out
