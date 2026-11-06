"""ingest.main 이 persons·regions 를 INSERT 하는지 (가짜 DB 연결, 실제 DB 미사용)."""
import json
import sys
from unittest.mock import MagicMock

import pytest

from starter import ingest, pii_pipeline


DOC = {"id": "M1", "doc_type": "민원",
       "text": "신청인: 홍길동\n주소: 대구 수성구 한빛로 12, 302호\n상담사 김하늘: 확인했습니다"}


def fake_detect(text):
    out = []
    for label, part in (("person", "홍길동"), ("address", "대구 수성구 한빛로 12, 302호"), ("person", "김하늘")):
        if part in text:
            s = text.index(part)
            out.append({"label": label, "start": s, "end": s + len(part)})
    return out


@pytest.fixture
def run(tmp_path, monkeypatch):
    src = tmp_path / "docs.jsonl"
    src.write_text(json.dumps(DOC, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(pii_pipeline.config, "HMAC_KEY", "ingest-meta-test-key")
    pii_pipeline._key.cache_clear()
    monkeypatch.setenv("ROLE_MASTER", "file")
    monkeypatch.delenv("CHUNK_METADATA", raising=False)
    monkeypatch.delenv("ADDRESS_MODE", raising=False)
    monkeypatch.setattr(ingest.config, "OUT_DIR", tmp_path / "out")
    monkeypatch.setattr(pii_pipeline, "detect", fake_detect)
    monkeypatch.setattr(ingest, "embed", lambda texts: [[0.0]] * len(texts))
    rag, vault = MagicMock(), MagicMock()
    monkeypatch.setattr(ingest.db, "rag_conn", lambda: rag)
    monkeypatch.setattr(ingest.db, "vault_conn", lambda: vault)

    def go(*extra):
        monkeypatch.setattr(sys, "argv", ["ingest", "--data", str(src), *extra])
        ingest.main()
        conn = rag.__enter__.return_value
        cur = conn.cursor.return_value.__enter__.return_value
        sql, params = cur.executemany.call_args.args
        return sql, params, conn, vault.__enter__.return_value, tmp_path / "out"
    yield go
    pii_pipeline._key.cache_clear()


def test_persons_and_regions_are_inserted(run):
    sql, params, *_ = run()
    assert "persons" in sql and "regions" in sql and "persons = EXCLUDED.persons" in sql
    row = params[0]
    assert row["regions"] == ["대구광역시 수성구"]
    persons = row["persons"].obj                                  # psycopg Jsonb
    assert [(p["role"], p["rule_id"], p["mention"]) for p in persons] == [
        ("customer", "ROLE-11", 0), ("agent", "ROLE-10", 1)]
    assert all(p["token"].startswith("<PERSON_") for p in persons)
    assert not any(k.startswith("_") for k in row)                # 요약용 키는 DB로 가지 않음
    assert "홍길동" not in row["masked_text"] and "대구" not in row["masked_text"]   # 기존 가명처리 유지


def test_metadata_can_be_turned_off(run, monkeypatch):
    monkeypatch.setenv("CHUNK_METADATA", "off")
    sql, params, *_ = run()
    assert "persons" not in sql and "persons" not in params[0]


def test_dry_run_rolls_back_and_writes_no_pseudo_file(run):
    _, _, conn, vault, out = run("--dry-run")
    conn.rollback.assert_called_once()
    vault.rollback.assert_called_once()
    assert not (out / "pseudo_docs.jsonl").exists()


def test_dry_run_with_reset_is_refused(run):
    with pytest.raises(SystemExit):
        run("--dry-run", "--reset")
