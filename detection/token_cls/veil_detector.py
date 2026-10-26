"""추가 토큰 분류 모델: 1T/veil-pii-ko-lite (Veil, ONNX Runtime · CPU).

모델 정보 (모델 카드 기준, 2026-10-23 확인)
- Apache-2.0 · KoELECTRA-base v3 기반 · 약 110M · INT8 ONNX 143MB
- 학습: BCCard/privacy-filter-openpii-masking + KDPII (둘 다 CC BY 4.0) + 자체 합성 데이터
- 라벨 32종 × BIOES + O = 129 클래스, 제약 Viterbi 디코딩
- 최대 512토큰. 긴 문서는 저장소의 veil.py가 겹쳐 잘라서(stride 128) 처리

모델 카드 안내: 저장소의 veil.py(Viterbi + 구간 병합 + 조사 제거)를 그대로 써야 공개 성능이 재현됨.
→ MoAI 때와 같은 원칙으로, 디코딩은 veil.py 원본을 그대로 불러 쓰고 여기서는 라벨 대응만 한다.

실행
    python -m detection.run_detect --detector veil --split dev --limit 3
    python -m detection.run_detect --detector veil --split dev

설치: pip install "onnxruntime>=1.28,<1.29" "transformers>=5.6" "huggingface-hub>=1.5" numpy
모델 위치: 환경변수 VEIL_MODEL_DIR, 없으면 models/veil (없으면 자동 다운로드, 약 145MB)
CPU 스레드: 환경변수 VEIL_THREADS (기본 4, 모델 카드 기본값)
"""
import importlib.util
import json
import os
from pathlib import Path

from detection.base import BaseDetector, Span

MODEL_ID = "1T/veil-pii-ko-lite"

# 모델 라벨 32종 → 우리 7종. None = 채점 대상 아님(버림). MoAI 대응표와 같은 원칙
LABEL_MAP = {
    "PERSON": "person",
    "PHONE": "phone_number",
    "EMAIL": "email",
    "ADDRESS": "address",
    "RRN": "resident_registration_number",
    "FRN": "resident_registration_number",        # 외국인등록번호: 형식이 같아 주민번호로 묶음 (MoAI와 동일)
    "ACCOUNT_NUMBER": "account_number",
    "CARD_NUMBER": "card_number",
    "VIRTUAL_CARD_NUMBER": "card_number",         # MoAI와 동일
    # 버림
    "ORGANIZATION": None,   # 회사 안내로 채점 제외
    "SSN": None,            # 미국 사회보장번호 (MoAI와 동일하게 버림)
    "DATE": None, "GENERIC_ID": None, "USER_ID": None, "SECRET": None,
    "BUSINESS_ID": None, "PASSPORT": None, "DRIVER_LICENSE": None, "CI": None, "IPIN": None,
    "CARD_EXPIRY": None, "CVC": None, "TRANSACTION_APPROVAL_ID": None,
    "IPADDRESS": None, "MACADDRESS": None, "PORT": None, "URL": None, "ZIPCODE": None,
    "IMEI": None, "VEHICLE_PLATE": None, "SUBSCRIBER_ID": None, "DEVICE_SERIAL": None,
}

# 받을 파일: ONNX + 토크나이저 + veil.py (450MB짜리 model.safetensors는 안 받음)
DOWNLOAD_FILES = ["model.int8.onnx", "veil.py", "config.json", "tokenizer.json",
                  "tokenizer_config.json", "special_tokens_map.json", "vocab.txt", "labels.yaml"]


def _load_upstream(model_dir: Path):
    """models/veil/veil.py를 'veil_upstream'이라는 이름으로 불러온다 (원본 수정 없음)."""
    spec = importlib.util.spec_from_file_location("veil_upstream", model_dir / "veil.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class VeilDetector(BaseDetector):
    name = "veil"

    def __init__(self):
        model_dir = Path(os.getenv("VEIL_MODEL_DIR") or "models/veil")
        if not (model_dir / "model.int8.onnx").exists() or not (model_dir / "veil.py").exists():
            from huggingface_hub import snapshot_download
            print(f"모델 다운로드: {MODEL_ID} → {model_dir} (약 145MB)")
            snapshot_download(repo_id=MODEL_ID, local_dir=str(model_dir), allow_patterns=DOWNLOAD_FILES)

        upstream = _load_upstream(model_dir)
        threads = int(os.getenv("VEIL_THREADS", "4"))
        self.veil = upstream.Veil(str(model_dir / "model.int8.onnx"),
                                  tokenizer_dir=str(model_dir), threads=threads)

        # 대응표에 없는 모델 라벨 경고 (모델이 업데이트됐을 때 알아채기 위함)
        cfg = json.loads((model_dir / "config.json").read_text(encoding="utf-8"))
        model_labels = {v.split("-", 1)[1] for v in cfg["id2label"].values() if v != "O"}
        missing = model_labels - set(LABEL_MAP)
        if missing:
            print(f"⚠ 대응표에 없는 라벨(버려짐): {sorted(missing)}")
        print(f"[veil] {MODEL_ID} · INT8 ONNX · 라벨 {len(model_labels)}종 · 스레드 {threads}")

    def detect(self, text: str) -> list[Span]:
        out = []
        for e in self.veil.predict(text):          # [{start, end, label, score}, ...]
            ours = LABEL_MAP.get(e["label"])
            if ours:
                s, t = int(e["start"]), int(e["end"])
                out.append(Span(s, t, ours, text[s:t], round(float(e["score"]), 3)))
        return out
