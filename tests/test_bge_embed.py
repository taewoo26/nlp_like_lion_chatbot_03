"""BGE-M3 boundary tests; no model download or external API is needed."""
import numpy as np
import pytest
import sys
import types

from starter import config, ingest, pii_pipeline, search, db
from starter import embed as embeddings


class FakeModel:
    max_seq_length = 8192

    def __init__(self):
        self.inputs = []
        self.calls = 0

    def tokenizer(self, text, **kwargs):
        return {"input_ids": list(range(len(text) + 2))}

    def encode(self, texts, **kwargs):
        self.calls += 1
        self.inputs.extend(texts)
        assert kwargs["normalize_embeddings"] is True
        assert kwargs["batch_size"] == 8
        out = np.zeros((len(texts), 1024), dtype=np.float32)
        out[:, 0] = 1
        return out


@pytest.fixture
def bge(monkeypatch):
    model = FakeModel()
    monkeypatch.setattr(config, "EMBED_PROVIDER", "bge-m3")
    monkeypatch.setattr(config, "EMBED_MODEL", "BAAI/bge-m3")
    monkeypatch.setattr(config, "EMBED_DIM", 1024)
    monkeypatch.setattr(config, "EMBED_DEVICE", "cpu")
    monkeypatch.setattr(config, "EMBED_BATCH_SIZE", 8)
    monkeypatch.setattr(config, "EMBED_MAX_TOKENS", 8192)
    monkeypatch.setattr(embeddings, "_bge_model", lambda *_: model)
    return model


def test_bge_reuses_model_for_document_and_query(bge):
    assert len(embeddings.embed(["masked document", "another chunk"])) == 2
    assert len(embeddings.embed(["masked query"])) == 1
    assert bge.calls == 2
    assert bge.inputs == ["masked document", "another chunk", "masked query"]


def test_bge_rejects_long_input_without_logging_text(bge, monkeypatch):
    monkeypatch.setattr(config, "EMBED_MAX_TOKENS", 4)
    with pytest.raises(ValueError, match="index=0") as error:
        embeddings.embed(["secret-text"])
    assert "secret-text" not in str(error.value)
    assert bge.calls == 0


def test_bge_rejects_bad_dimension_and_never_falls_back(bge, monkeypatch):
    monkeypatch.setattr(bge, "encode", lambda texts, **kwargs: np.zeros((len(texts), 1536)))
    with pytest.raises(RuntimeError, match="차원"):
        embeddings.embed(["masked"])


def test_bge_loads_once_and_forces_local_files(monkeypatch):
    calls = []

    def constructor(*args, **kwargs):
        calls.append((args, kwargs))
        return FakeModel()

    monkeypatch.setitem(sys.modules, "sentence_transformers",
                        types.SimpleNamespace(SentenceTransformer=constructor))
    embeddings._bge_model.cache_clear()
    try:
        first = embeddings._bge_model("BAAI/bge-m3", "cpu")
        assert embeddings._bge_model("BAAI/bge-m3", "cpu") is first
        assert len(calls) == 1
        assert calls[0][1]["local_files_only"] is True
    finally:
        embeddings._bge_model.cache_clear()


def test_bge_inference_error_does_not_echo_input(bge, monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError("sensitive input was present")

    monkeypatch.setattr(bge, "encode", fail)
    with pytest.raises(RuntimeError) as error:
        embeddings.embed(["sensitive input was present"])
    assert "sensitive input" not in str(error.value)


def test_unknown_provider_is_an_error(monkeypatch):
    monkeypatch.setattr(config, "EMBED_PROVIDER", "typo")
    with pytest.raises(ValueError, match="EMBED_PROVIDER"):
        embeddings.embed(["masked"])


def test_local_backend_remains_available(monkeypatch):
    monkeypatch.setattr(config, "EMBED_PROVIDER", "local")
    monkeypatch.setattr(config, "EMBED_DIM", 1536)
    vector = embeddings.embed(["masked"])[0]
    assert vector.shape == (1536,)
    assert np.isclose(np.linalg.norm(vector), 1)


def test_ingest_and_query_send_only_masked_text_to_bge(bge, monkeypatch, tmp_path):
    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def execute(self, *_):
            pass

        def executemany(self, *_):
            pass

        def fetchone(self):
            return ("present",)

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def cursor(self):
            return Cursor()

    monkeypatch.setattr(config, "HMAC_KEY", "bge-test-key-only")
    pii_pipeline._key.cache_clear()
    monkeypatch.setattr(config, "OUT_DIR", tmp_path)
    monkeypatch.setattr(ingest, "load_docs", lambda *_: [{
        "id": "D1", "text": "고객 김민준 전화 010-1234-5678", "doc_type": "test"
    }])
    monkeypatch.setattr(db, "rag_conn", Connection)
    monkeypatch.setattr(db, "vault_conn", Connection)
    monkeypatch.setattr(sys, "argv", ["ingest", "--data", "synthetic.jsonl"])
    try:
        ingest.main()
        masked_query, tokens = search.mask_query("김민준 전화 내역")
        embeddings.embed([masked_query])
    finally:
        pii_pipeline._key.cache_clear()
    assert len(bge.inputs) == 2
    assert all("김민준" not in text and "010-1234-5678" not in text for text in bge.inputs)
    assert tokens and all("<PERSON_" in text for text in bge.inputs)
