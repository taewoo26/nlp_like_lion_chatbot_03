"""임베딩. OpenAI 호환 /embeddings API 또는 테스트용 로컬 해시 임베딩.

주의: 이 함수에는 반드시 가명처리된(마스킹된) 텍스트만 넣어야 합니다.

API 설정 (.env)
  EMBED_BATCH     한 요청에 보낼 문장 수 (기본 64).
                  CLOVA Studio(bge-m3)는 배열 입력을 400 "convert error"로 거부하므로 1 로 둔다.
  EMBED_INTERVAL  요청 사이 최소 간격(초, 기본 0). 속도 제한(429)이 잦으면 0.5~1 로 늘린다.
  EMBED_CACHE     1 이면 결과를 outputs/embed_cache.jsonl 에 저장하고 다시 쓰지 않는다 (기본 1).
                  적재가 중간에 멈춰도 다시 실행하면 이미 받은 문장은 API를 다시 부르지 않는다.
                  키는 (모델, 문장)의 SHA-256 이라 파일에 문장 원문이 남지 않는다.
"""
import hashlib
import json
import re
import time

import numpy as np
import requests

from . import config

_RETRY_STATUS = {429, 500, 502, 503, 504}   # 속도 제한·일시 오류만 재시도
_MAX_RETRY = 8
_MAX_WAIT = 60.0
_last_call = [0.0]
_cache = None


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


def _setting(name, default, cast):
    value = cast(config.env(name, default))
    if value < 0:
        raise ValueError(f"{name} 는 0 이상이어야 합니다.")
    return value


# ---------------------------------------------------------------- 캐시
def _cache_path():
    return config.OUT_DIR / "embed_cache.jsonl"


def _cache_key(text):
    return hashlib.sha256(f"{config.EMBED_MODEL}\n{text}".encode()).hexdigest()


def _load_cache():
    global _cache
    if _cache is None:
        _cache = {}
        path = _cache_path()
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    row = json.loads(line)
                    _cache[row["k"]] = row["v"]
    return _cache


def _save_cache(key, vec):
    config.OUT_DIR.mkdir(exist_ok=True)
    with open(_cache_path(), "a", encoding="utf-8") as f:
        f.write(json.dumps({"k": key, "v": [float(x) for x in vec]}) + "\n")
    _load_cache()[key] = list(vec)


# ---------------------------------------------------------------- API
def _wait_time(response, attempt):
    """서버가 알려준 대기 시간(Retry-After)이 있으면 따르고, 없으면 2^n 초."""
    retry_after = response.headers.get("Retry-After") if response is not None else None
    try:
        wait = float(retry_after)
    except (TypeError, ValueError):
        wait = 2.0 ** attempt
    return min(max(wait, 1.0), _MAX_WAIT)


def _post(payload):
    url = config.EMBED_API_BASE.rstrip("/") + "/embeddings"
    headers = {"Authorization": f"Bearer {config.EMBED_API_KEY}"}
    interval = _setting("EMBED_INTERVAL", "0", float)
    for attempt in range(_MAX_RETRY + 1):
        gap = interval - (time.monotonic() - _last_call[0])
        if gap > 0:
            time.sleep(gap)
        _last_call[0] = time.monotonic()
        r = requests.post(url, headers=headers, json=payload, timeout=60)
        if r.status_code in _RETRY_STATUS and attempt < _MAX_RETRY:
            wait = _wait_time(r, attempt)
            print(f"  임베딩 API {r.status_code} → {wait:.0f}초 후 재시도 ({attempt + 1}/{_MAX_RETRY})", flush=True)
            time.sleep(wait)
            continue
        if not r.ok:
            limits = {k: v for k, v in r.headers.items() if "ratelimit" in k.lower() or k.lower() == "retry-after"}
            raise RuntimeError(f"임베딩 API 오류 {r.status_code}: {r.text[:200]} {limits or ''}".rstrip())
        return r.json()["data"]
    raise RuntimeError("임베딩 API 재시도 횟수를 넘었습니다.")


def _request(part, size):
    payload = {"model": config.EMBED_MODEL, "encoding_format": "float",
               "input": part[0] if size == 1 else part}
    data = _post(payload)
    if len(data) != len(part):
        raise RuntimeError(f"임베딩 응답 개수 {len(data)} 이 요청 {len(part)} 과 다릅니다.")
    if size > 1:
        data = sorted(data, key=lambda d: d["index"])
    return [d["embedding"] for d in data]


def _api(texts):
    if not config.EMBED_API_KEY:
        raise RuntimeError("EMBED_API_KEY 가 비어 있습니다. .env 를 확인하거나 EMBED_PROVIDER=local 로 테스트하세요.")
    size = _setting("EMBED_BATCH", "64", int) or 1
    use_cache = config.env("EMBED_CACHE", "1").strip() == "1"
    cache = _load_cache() if use_cache else {}
    keys = [_cache_key(t) for t in texts]
    todo = [i for i, k in enumerate(keys) if k not in cache]
    if len(todo) > 20:
        print(f"  임베딩 API 요청 {len(todo)}건 (캐시 {len(texts) - len(todo)}건 재사용)", flush=True)
    fresh = {}
    for n in range(0, len(todo), size):
        idx = todo[n:n + size]
        for i, vec in zip(idx, _request([texts[i] for i in idx], size)):
            fresh[keys[i]] = vec
            if use_cache:
                _save_cache(keys[i], vec)
        done = n + len(idx)
        if len(todo) > 20 and (done % 50 < len(idx) or done == len(todo)):
            print(f"  … {done}/{len(todo)}", flush=True)
    out = [np.array(fresh.get(k, cache.get(k)), dtype=np.float32) for k in keys]
    bad = {len(v) for v in out} - {config.EMBED_DIM}
    if bad:
        raise RuntimeError(f"임베딩 차원 {sorted(bad)} 이 EMBED_DIM={config.EMBED_DIM} 과 다릅니다.")
    return out


def embed(texts):
    return _api(texts) if config.EMBED_PROVIDER == "api" else _local(texts)