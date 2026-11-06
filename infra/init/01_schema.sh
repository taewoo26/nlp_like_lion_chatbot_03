#!/bin/bash
# 컨테이너 최초 실행 시 한 번만 실행됩니다. (다시 만들려면: docker compose down -v 후 up)
# rag   : 마스킹된 청크 + 토큰 목록 + 임베딩  (rag_user 만 접근)
# vault : 토큰 ↔ 원문 매핑 테이블            (vault_user 만 접근)
set -euo pipefail

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<SQL
CREATE EXTENSION IF NOT EXISTS vector;

CREATE ROLE rag_user   LOGIN PASSWORD '${RAG_PASSWORD}';
CREATE ROLE vault_user LOGIN PASSWORD '${VAULT_PASSWORD}';
GRANT CONNECT ON DATABASE ${POSTGRES_DB} TO rag_user, vault_user;

-- 검색용 스키마: 가명처리된 텍스트만 저장
CREATE SCHEMA rag AUTHORIZATION rag_user;
CREATE TABLE rag.chunks (
    chunk_id     TEXT PRIMARY KEY,               -- 예: H0001#0
    doc_id       TEXT NOT NULL,
    chunk_idx    INT  NOT NULL,
    doc_type     TEXT,
    masked_text  TEXT NOT NULL,                  -- 원문 저장 금지
    tokens       TEXT[] NOT NULL DEFAULT '{}',   -- 청크에 들어 있는 토큰 목록
    embedding    vector(${EMBED_DIM}) NOT NULL,
    created_at   TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX chunks_tokens_gin     ON rag.chunks USING GIN (tokens);
CREATE INDEX chunks_embedding_hnsw ON rag.chunks USING hnsw (embedding vector_cosine_ops);
CREATE INDEX chunks_doc_id         ON rag.chunks (doc_id);
ALTER TABLE rag.chunks OWNER TO rag_user;

-- 매핑용 스키마: 토큰 ↔ 원문
CREATE SCHEMA vault AUTHORIZATION vault_user;
CREATE TABLE vault.token_map (
    token       TEXT PRIMARY KEY,                -- 예: <PERSON_03f0d420>
    label       TEXT NOT NULL,
    normalized  TEXT NOT NULL,                   -- 토큰을 만든 정규화 값
    originals   TEXT[] NOT NULL DEFAULT '{}',    -- 실제로 나온 표기들 (예: 윤 선영, 윤선영)
    created_at  TIMESTAMPTZ DEFAULT now()
);
ALTER TABLE vault.token_map OWNER TO vault_user;

-- 서로의 스키마에는 접근 불가
REVOKE ALL ON SCHEMA vault FROM PUBLIC, rag_user;
REVOKE ALL ON SCHEMA rag   FROM PUBLIC, vault_user;
SQL
