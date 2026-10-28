-- pgvector 확장 활성화
CREATE EXTENSION IF NOT EXISTS vector;

-- ─────────────────────────────────────────
-- 1. 청크 테이블 (벡터 DB 적재용)
-- ─────────────────────────────────────────
CREATE TABLE IF NOT EXISTS chunks (
    id          SERIAL PRIMARY KEY,
    doc_id      TEXT NOT NULL,           -- 원본 문서 ID (예: D0002)
    doc_type    TEXT,                    -- 문서 유형 (예: 대출상담)
    chunk_text  TEXT NOT NULL,           -- 가명처리된 청크 본문
    tokens      TEXT[],                  -- 청크에 포함된 가명 토큰 목록
    embedding   vector(1536),            -- 임베딩 벡터 (text-embedding-3-small 기준)
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

-- ─────────────────────────────────────────
-- 2. 매핑 테이블 (토큰 ↔ 원문, 권한 분리)
-- ─────────────────────────────────────────
CREATE TABLE IF NOT EXISTS vault (
    token        TEXT PRIMARY KEY,       -- 가명 토큰 (예: <PERSON_03f0d420>)
    original     TEXT NOT NULL,          -- 원문 값 (예: 윤선영)
    label        TEXT NOT NULL,          -- 라벨 (예: person)
    access_level INT  DEFAULT 1,         -- 권한 등급 (1=일반, 2=관리자)
    created_at   TIMESTAMPTZ DEFAULT NOW()
);

-- ─────────────────────────────────────────
-- 3. 인덱스
-- ─────────────────────────────────────────
-- 벡터 유사도 검색 인덱스 (cosine)
CREATE INDEX IF NOT EXISTS chunks_embedding_idx
    ON chunks USING ivfflat (embedding vector_cosine_ops)
    WITH (lists = 100);

-- 토큰 배열 검색 인덱스 (GIN)
CREATE INDEX IF NOT EXISTS chunks_tokens_idx
    ON chunks USING GIN (tokens);

-- doc_id 검색 인덱스
CREATE INDEX IF NOT EXISTS chunks_doc_id_idx
    ON chunks (doc_id);