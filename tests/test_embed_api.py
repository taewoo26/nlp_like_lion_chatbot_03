"""API 임베딩: 묶음/한 문장 요청, 재시도(Retry-After), 차원 검사, 캐시."""
from unittest.mock import Mock

import pytest

from starter import config, embed


def _resp(status, data=None, headers=None):
    r = Mock()
    r.status_code = status
    r.ok = status < 400
    r.text = "err"
    r.headers = headers or {}
    r.json.return_value = {"data": data or []}
    return r


@pytest.fixture
def api(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "EMBED_PROVIDER", "api")
    monkeypatch.setattr(config, "EMBED_API_KEY", "k")
    monkeypatch.setattr(config, "EMBED_API_BASE", "https://x/v1")
    monkeypatch.setattr(config, "EMBED_MODEL", "m")
    monkeypatch.setattr(config, "EMBED_DIM", 3)
    monkeypatch.setattr(config, "OUT_DIR", tmp_path)
    monkeypatch.setattr(embed, "_cache", None)
    monkeypatch.setenv("EMBED_CACHE", "0")
    monkeypatch.setenv("EMBED_INTERVAL", "0")
    sleeps = []
    monkeypatch.setattr(embed.time, "sleep", sleeps.append)
    return sleeps


def test_batch_1_sends_string_per_text(api, monkeypatch):
    monkeypatch.setenv("EMBED_BATCH", "1")
    calls = []
    def post(url, headers, json, timeout):
        calls.append(json["input"])
        return _resp(200, [{"embedding": [1, 0, 0]}])     # CLOVA 응답에는 index 가 없을 수 있음
    monkeypatch.setattr(embed.requests, "post", post)
    out = embed.embed(["a", "b"])
    assert calls == ["a", "b"] and len(out) == 2


def test_batch_sends_list_and_orders_by_index(api, monkeypatch):
    monkeypatch.setenv("EMBED_BATCH", "64")
    data = [{"index": 1, "embedding": [0, 1, 0]}, {"index": 0, "embedding": [1, 0, 0]}]
    monkeypatch.setattr(embed.requests, "post", lambda *a, **k: _resp(200, data))
    out = embed.embed(["a", "b"])
    assert out[0].tolist() == [1, 0, 0]


def test_retry_follows_retry_after_header(api, monkeypatch):
    monkeypatch.setenv("EMBED_BATCH", "1")
    seq = iter([_resp(429, headers={"Retry-After": "7"}), _resp(200, [{"embedding": [1, 0, 0]}])])
    monkeypatch.setattr(embed.requests, "post", lambda *a, **k: next(seq))
    assert len(embed.embed(["a"])) == 1
    assert api == [7.0]


def test_bad_request_is_not_retried(api, monkeypatch):
    monkeypatch.setenv("EMBED_BATCH", "1")
    post = Mock(return_value=_resp(400))
    monkeypatch.setattr(embed.requests, "post", post)
    with pytest.raises(RuntimeError, match="400"):
        embed.embed(["a"])
    assert post.call_count == 1


def test_dimension_mismatch_raises(api, monkeypatch):
    monkeypatch.setenv("EMBED_BATCH", "1")
    monkeypatch.setattr(embed.requests, "post", lambda *a, **k: _resp(200, [{"embedding": [1, 0]}]))
    with pytest.raises(RuntimeError, match="차원"):
        embed.embed(["a"])


def test_cache_resumes_without_calling_api_again(api, monkeypatch, tmp_path):
    monkeypatch.setenv("EMBED_BATCH", "1")
    monkeypatch.setenv("EMBED_CACHE", "1")
    post = Mock(return_value=_resp(200, [{"embedding": [1, 0, 0]}]))
    monkeypatch.setattr(embed.requests, "post", post)
    embed.embed(["가명 문장 하나"])
    monkeypatch.setattr(embed, "_cache", None)            # 새 실행처럼 파일에서 다시 읽기
    out = embed.embed(["가명 문장 하나"])
    assert post.call_count == 1 and out[0].tolist() == [1, 0, 0]
    assert "가명 문장" not in (tmp_path / "embed_cache.jsonl").read_text(encoding="utf-8")