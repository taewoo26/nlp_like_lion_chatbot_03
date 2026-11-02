"""EMBED_PROVIDER=e5: 로컬 sentence-transformers 모델, query/passage 접두어."""
import sys
import types

import numpy as np

from starter import config, embed


class _FakeModel:
    def __init__(self, path, device=None):
        self.seen = []

    def encode(self, texts, **kw):
        self.seen.extend(texts)
        return np.ones((len(texts), 384), dtype=np.float32) / np.sqrt(384)


def _setup(monkeypatch, tmp_path):
    (tmp_path / "models" / "e5").mkdir(parents=True)
    monkeypatch.setitem(sys.modules, "sentence_transformers", types.SimpleNamespace(SentenceTransformer=_FakeModel))
    monkeypatch.setattr(config, "ROOT", tmp_path)
    monkeypatch.setattr(config, "EMBED_PROVIDER", "e5")
    monkeypatch.setattr(config, "EMBED_DIM", 384)
    monkeypatch.setenv("EMBED_MODEL_PATH", "models/e5")
    monkeypatch.setattr(embed, "_st_model", None)


def test_e5_adds_query_and_passage_prefix(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    doc = embed.embed(["<PERSON> 고객 VOC"])
    q = embed.embed(["<PERSON> VOC 찾아줘"], kind="query")
    assert embed._st_model.seen == ["passage: <PERSON> 고객 VOC", "query: <PERSON> VOC 찾아줘"]
    assert doc[0].shape == (384,) and q[0].dtype == np.float32


def test_e5_dim_mismatch_raises(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    monkeypatch.setattr(config, "EMBED_DIM", 1024)
    try:
        embed.embed(["x"])
    except RuntimeError as e:
        assert "EMBED_DIM" in str(e)
    else:
        raise AssertionError("차원 불일치인데 통과함")
