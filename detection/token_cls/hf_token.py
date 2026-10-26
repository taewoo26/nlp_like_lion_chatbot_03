"""추가 토큰 분류 모델 (Hugging Face transformers · PyTorch · CPU).

회사 안내: 기준 모델(MoAI)과 *다른* 한국어 개인정보 탐지 모델을 1개 이상 찾아
같은 데이터 · 같은 채점 도구 · CPU 처리 시간으로 비교한다.

이 파일 하나로 여러 모델을 돌린다. 모델을 추가하려면 MODELS에 한 칸 넣고
detection/registry.py에 한 줄 넣으면 끝.

    python -m detection.run_detect --detector atonlee --split dev
    python -m detection.run_detect --detector piilot  --split dev

설치: pip install torch "transformers>=5.6" "huggingface-hub>=1.5"
모델 위치: models/<이름> (없으면 자동 다운로드, .gitignore에 models/ 포함)

동작
1) 토크나이저로 자르고 글자 위치(offset)를 같이 받는다
2) 512토큰보다 긴 문서는 겹치게 잘라서(창 이동) 각 토큰은 창 가운데에 가까운 쪽 예측을 쓴다
3) B-/I- 태그를 이어 붙여 하나의 구간으로 만든다
4) 모델 라벨을 우리 7종으로 바꾸고, 대응 없는 라벨은 버린다
"""
import os
from pathlib import Path

import numpy as np

from detection.base import BaseDetector, Span

# ---------------------------------------------------------------------------
# 모델 목록: 라벨 대응표는 각 모델 config.json의 id2label을 보고 작성 (2026-10-23 확인)
# None = 채점 대상 아님(버림)
# ---------------------------------------------------------------------------
MODELS = {
    "atonlee": {
        "repo": "atonlee/koelectra-ko-pii-ner",
        # Apache-2.0 · KoELECTRA-small v3 · 약 14M · BIO 29종
        # 학습: BCCard/pii-masking-openpii-finance, townboy/korean-pii-dataset (CC BY 4.0) + 직접 작성 예문
        "label_map": {
            "NAME": "person",
            "PHONE": "phone_number",
            "EMAIL": "email",
            "ADDRESS": "address",
            "RRN": "resident_registration_number",
            "ALIEN_ID": "resident_registration_number",  # 외국인등록번호: MoAI의 FRN과 같은 처리
            "ACCOUNT_NUMBER": "account_number",
            "CARD_NUMBER": "card_number",
            # 버림: 우리 데이터 채점 대상이 아님
            "ORGANIZATION": None, "DEPARTMENT": None, "POSITION": None, "PLACE": None,
            "ATTRIBUTE": None, "BIRTHDATE": None, "DATE": None, "TIME": None, "QUANTITY": None,
            "CARD_EXPIRY": None, "CVC": None, "DRIVER_LICENSE": None, "PASSPORT": None,
            "GENERIC_ID": None, "IPIN": None, "IP_ADDRESS": None, "SECRET": None,
            "URL": None, "USER_ID": None, "VEHICLE_NUMBER": None, "ZIPCODE": None,
        },
    },
    "piilot": {
        "repo": "ParkJunSeong/PIILOT_NER_Model",
        # Apache-2.0 · KoELECTRA-base v3 · 약 110M · BIO 8종 · 학습 데이터 미공개
        "label_map": {
            "p_nm": "person",
            "p_ph": "phone_number",
            "p_em": "email",
            "p_add": "address",
            "p_rrn": "resident_registration_number",
            "p_acn": "account_number",
            # 카드번호 라벨 없음 → card_number는 항상 0점 (보고서에 명시)
            "p_ip": None, "p_pp": None,
        },
    },
}

MAX_LEN = 512      # 두 모델 모두 max_position_embeddings 512
STRIDE = 128       # 긴 문서를 자를 때 겹치는 토큰 수


def bio_to_spans(tags: list[str], offsets: list[tuple[int, int]], probs: list[float]):
    """토큰별 BIO 태그 → (start, end, 원래라벨, 평균확률) 목록.

    B-X 또는 (앞과 다른) I-X에서 새 구간 시작, 같은 X의 I-가 이어지면 늘린다.
    O이거나 라벨이 바뀌면 끊는다. (I-로 시작하는 구간도 버리지 않고 시작으로 본다)
    """
    spans, cur = [], None
    for tag, (s, e), p in zip(tags, offsets, probs):
        if s == e:              # 특수 토큰
            continue
        if tag == "O":
            if cur:
                spans.append(cur); cur = None
            continue
        prefix, _, lab = tag.partition("-")
        if prefix == "I" and cur and cur[2] == lab:
            cur[1] = e; cur[3].append(p)
        else:
            if cur:
                spans.append(cur)
            cur = [s, e, lab, [p]]
    if cur:
        spans.append(cur)
    return [(s, e, lab, float(np.mean(ps))) for s, e, lab, ps in spans]


class HFTokenDetector(BaseDetector):
    key = ""   # MODELS의 키. 하위 클래스에서 지정

    def __init__(self):
        import torch
        from transformers import AutoModelForTokenClassification, AutoTokenizer

        cfg = MODELS[self.key]
        self.name = self.key
        self.label_map = cfg["label_map"]
        model_dir = Path(os.getenv(f"{self.key.upper()}_MODEL_DIR") or f"models/{self.key}")
        if not (model_dir / "config.json").exists():
            from huggingface_hub import snapshot_download
            print(f"모델 다운로드: {cfg['repo']} → {model_dir}")
            snapshot_download(
                repo_id=cfg["repo"], local_dir=str(model_dir),
                # PyTorch 가중치와 토크나이저만 (ONNX 파일 등은 받지 않음)
                allow_patterns=["*.json", "*.safetensors", "*.bin", "*.txt", "*.model"],
            )

        self.torch = torch
        torch.set_grad_enabled(False)
        self.tok = AutoTokenizer.from_pretrained(str(model_dir))
        self.model = AutoModelForTokenClassification.from_pretrained(str(model_dir)).eval()
        self.id2label = {int(k): v for k, v in self.model.config.id2label.items()}

        # 대응표에 없는 모델 라벨 경고 (모델이 바뀌었을 때 알아채기 위함)
        model_labels = {v.partition("-")[2] for v in self.id2label.values() if v != "O"}
        missing = model_labels - set(self.label_map)
        if missing:
            print(f"⚠ 대응표에 없는 라벨(버려짐): {sorted(missing)}")
        n_params = sum(p.numel() for p in self.model.parameters())
        print(f"[{self.key}] {cfg['repo']} · 파라미터 {n_params / 1e6:.1f}M · 라벨 {len(model_labels)}종")

    # 긴 문서: 토큰 창을 겹치게 옮기며 예측, 토큰마다 창 가운데에 가까운 예측을 채택
    def _predict(self, ids: list[int]):
        n, body = len(ids), MAX_LEN - 2
        best_logits = [None] * n
        best_dist = [-1] * n
        start = 0
        while True:
            end = min(start + body, n)
            chunk = [self.tok.cls_token_id] + ids[start:end] + [self.tok.sep_token_id]
            x = self.torch.tensor([chunk])
            logits = self.model(input_ids=x, attention_mask=self.torch.ones_like(x)).logits[0, 1:-1]
            logits = logits.numpy()
            for j in range(end - start):
                i = start + j
                dist = min(j, end - start - 1 - j)   # 창 가장자리까지 거리
                if dist > best_dist[i]:
                    best_dist[i], best_logits[i] = dist, logits[j]
            if end == n:
                break
            start = end - STRIDE
        return np.stack(best_logits) if n else np.zeros((0, len(self.id2label)))

    def detect(self, text: str) -> list[Span]:
        enc = self.tok(text, add_special_tokens=False, return_offsets_mapping=True)
        ids, offsets = enc["input_ids"], enc["offset_mapping"]
        if not ids:
            return []
        logits = self._predict(ids)
        z = logits - logits.max(-1, keepdims=True)
        prob = np.exp(z) / np.exp(z).sum(-1, keepdims=True)
        pred = prob.argmax(-1)
        tags = [self.id2label[int(k)] for k in pred]
        conf = prob.max(-1).tolist()

        out = []
        for s, e, lab, p in bio_to_spans(tags, offsets, conf):
            ours = self.label_map.get(lab)
            if ours:
                out.append(Span(s, e, ours, text[s:e], round(p, 3)))
        return out


class AtonleeDetector(HFTokenDetector):
    key = "atonlee"


class PiilotDetector(HFTokenDetector):
    key = "piilot"
