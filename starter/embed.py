"""임베딩. 기존 local 해시, 로컬 BGE-M3 dense, OpenAI 호환 API.

주의: 이 함수에는 반드시 가명처리된(마스킹된) 텍스트만 넣어야 합니다.
"""
import hashlib
import re
from functools import lru_cache

import numpy as np
import requests

from . import config

_BATCH = 64
_BGE_DIM = 1024
_BGE_MAX_TOKENS = 8192


def _local(texts):
    """API 없이 파이프라인 동작만 확인하는 문자 n-gram 해시 임베딩 (검색 품질 낮음)."""
    out = []
    for t in texts:
        v = np.zeros(config.EMBED_DIM, dtype=np.float32)
        s = re.sub(r"\s+", " ", t)
        for n in (2, 3):
            for i in range(len(s) - n + 1):
                h = int(hashlib.md5(s[i:i + n].encode()).hexdigest()[:8], 16)
                v[h % config.EMBED_DIM] += 1.0
        norm = np.linalg.norm(v)
        out.append(v / norm if norm else v)
    return out


def _api(texts):
    if not config.EMBED_API_KEY:
        raise RuntimeError("EMBED_API_KEY 가 비어 있습니다. .env 를 확인하거나 EMBED_PROVIDER=local 로 테스트하세요.")
    out = []
    for i in range(0, len(texts), _BATCH):
        r = requests.post(
            config.EMBED_API_BASE.rstrip("/") + "/embeddings",
            headers={"Authorization": f"Bearer {config.EMBED_API_KEY}"},
            json={"model": config.EMBED_MODEL, "input": texts[i:i + _BATCH]},
            timeout=60,
        )
        r.raise_for_status()
        data = sorted(r.json()["data"], key=lambda d: d["index"])
        out.extend(np.array(d["embedding"], dtype=np.float32) for d in data)
    if out and len(out[0]) != config.EMBED_DIM:
        raise RuntimeError(f"임베딩 차원 {len(out[0])} 이 EMBED_DIM={config.EMBED_DIM} 과 다릅니다.")
    return out


@lru_cache(maxsize=1)
def _bge_model(model_name, device):
    if model_name != "BAAI/bge-m3":
        raise ValueError("BGE-M3에는 EMBED_MODEL=BAAI/bge-m3를 사용하세요.")
    if device not in {"cpu", "mps"}:
        raise ValueError("EMBED_DEVICE는 cpu 또는 mps여야 합니다.")
    if device == "mps":
        import torch
        if not torch.backends.mps.is_available():
            raise RuntimeError("이 PyTorch 환경에서 MPS를 사용할 수 없습니다.")
    try:
        from sentence_transformers import SentenceTransformer
        return SentenceTransformer(model_name, device=device, local_files_only=True)
    except Exception as exc:
        raise RuntimeError(
            f"BGE-M3 로딩 실패 ({type(exc).__name__}): "
            "모델 캐시와 패키지 호환성을 확인하세요."
        ) from None

def _bge(texts):
    if config.EMBED_DIM != _BGE_DIM:
        raise ValueError("BGE-M3는 EMBED_DIM=1024가 필요합니다.")
    if config.EMBED_BATCH_SIZE < 1 or not 1 <= config.EMBED_MAX_TOKENS <= _BGE_MAX_TOKENS:
        raise ValueError("BGE-M3 배치 크기 또는 최대 토큰 수 설정이 잘못됐습니다.")
    if not texts:
        return []
    model = _bge_model(config.EMBED_MODEL, config.EMBED_DEVICE)
    if model.max_seq_length is None or model.max_seq_length < config.EMBED_MAX_TOKENS:
        raise RuntimeError("BGE-M3 모델의 실제 입력 한도가 EMBED_MAX_TOKENS보다 작습니다.")
    # SentenceTransformer의 묵시적 절단을 허용하지 않는다. 원문은 오류에 포함하지 않는다.
    for index, value in enumerate(texts):
        try:
            length = len(model.tokenizer(value, add_special_tokens=True, truncation=False)["input_ids"])
        except Exception:
            raise RuntimeError(f"BGE-M3 입력 길이 검사 실패: index={index}") from None
        if length > config.EMBED_MAX_TOKENS:
            raise ValueError(f"BGE-M3 입력 토큰 한도 초과: index={index}, tokens={length}")
    try:
        vectors = model.encode(texts, batch_size=config.EMBED_BATCH_SIZE,
                               normalize_embeddings=True, convert_to_numpy=True,
                               show_progress_bar=False)
    except Exception:
        raise RuntimeError("BGE-M3 임베딩 추론 실패") from None
    values = np.asarray(vectors, dtype=np.float32)
    if values.shape != (len(texts), _BGE_DIM) or not np.isfinite(values).all():
        raise RuntimeError("BGE-M3 임베딩 출력 개수·차원 또는 값 오류")
    if not np.allclose(np.linalg.norm(values, axis=1), 1.0, atol=1e-3):
        raise RuntimeError("BGE-M3 임베딩 정규화 오류")
    return list(values)


def embed(texts):
    if config.EMBED_PROVIDER == "local":
        return _local(texts)
    if config.EMBED_PROVIDER == "bge-m3":
        return _bge(texts)
    if config.EMBED_PROVIDER == "api":
        return _api(texts)
    raise ValueError("지원하지 않는 EMBED_PROVIDER")
