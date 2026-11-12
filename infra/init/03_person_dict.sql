-- 종합: 인물사전 (person_registry · person_info · look_up)
-- 새 DB에서는 02_metadata.sql 다음에 자동 실행된다.
-- 이미 있는 DB에는 README의 '메타데이터 스키마 적용'과 같은 방식으로 한 번 실행한다. 여러 번 실행해도 안전하다.
-- 01_schema.sh가 만든 rag.chunks, vault.token_map을 참조한다.

-- updated_at 자동 갱신 (vault 표 공용)
CREATE OR REPLACE FUNCTION vault.tg_set_updated_at()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    NEW.updated_at := now();
    RETURN NEW;
END;
$$;
ALTER FUNCTION vault.tg_set_updated_at() OWNER TO vault_user;

-- 확정 인물사전 (fix). reg_id는 적재 전 파이썬에서 생성한다 (reg_ + 식별자, UUID 아님)
CREATE TABLE IF NOT EXISTS vault.person_registry (
    reg_id      TEXT        PRIMARY KEY,
    reason      JSONB       NOT NULL,                    -- [가정] 타입
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT person_registry_reg_id_fmt CHECK (reg_id LIKE 'reg\_%')
);
ALTER TABLE vault.person_registry OWNER TO vault_user;

CREATE OR REPLACE TRIGGER person_registry_updated_at
    BEFORE UPDATE ON vault.person_registry
    FOR EACH ROW EXECUTE FUNCTION vault.tg_set_updated_at();

-- 임시 인물사전 (tmp). person_id는 적재 전 파이썬에서 생성한다 (tmp_ + 식별자, UUID 아님)
CREATE TABLE IF NOT EXISTS vault.person_info (
    person_id          TEXT        PRIMARY KEY,
    reg_id             TEXT,                                  -- 미등록이면 NULL
    token              TEXT        NOT NULL,                  -- PERSON_ + HMAC
    role               TEXT,                                  -- rag.role_master.role_code
    info_label         TEXT[],                                -- 식별 근거 토큰 (phone, email 등)
    evidence           JSONB,                                 -- 판정 근거
    doc_id             TEXT[]      NOT NULL,                  -- 등장 문서 ID
    resolution_status  TEXT        NOT NULL DEFAULT 'pending', -- pending / resolved / unresolved
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT person_info_person_id_fmt CHECK (person_id LIKE 'tmp\_%'),
    CONSTRAINT person_info_token_fmt     CHECK (token ~ '^<PERSON_[0-9a-f]+>$'),
    CONSTRAINT person_info_status_chk    CHECK (resolution_status IN ('pending', 'resolved', 'unresolved')),
    -- 코드값 시트: resolved = reg_id에 연결됨
    CONSTRAINT person_info_resolved_has_reg CHECK ((resolution_status = 'resolved') = (reg_id IS NOT NULL))
);
ALTER TABLE vault.person_info OWNER TO vault_user;
CREATE INDEX IF NOT EXISTS person_info_token  ON vault.person_info (token);
CREATE INDEX IF NOT EXISTS person_info_reg_id ON vault.person_info (reg_id);

CREATE OR REPLACE TRIGGER person_info_updated_at
    BEFORE UPDATE ON vault.person_info
    FOR EACH ROW EXECUTE FUNCTION vault.tg_set_updated_at();

-- 인물사전 검색 인덱스. 청크에 등장한 후보 1명당 1행, 후보당 역할 1개
CREATE TABLE IF NOT EXISTS rag.look_up (
    person_id  TEXT NOT NULL REFERENCES vault.person_info (person_id) ON DELETE CASCADE,
    chunk_id   TEXT NOT NULL REFERENCES rag.chunks (chunk_id) ON DELETE CASCADE,
    token      TEXT NOT NULL REFERENCES vault.token_map (token),
    reg_id     TEXT          REFERENCES vault.person_registry (reg_id) ON DELETE SET NULL,
    role       TEXT,                                          -- rag.role_master.role_code
    PRIMARY KEY (chunk_id, person_id)
);
ALTER TABLE rag.look_up OWNER TO rag_user;
CREATE INDEX IF NOT EXISTS look_up_person_id ON rag.look_up (person_id);
CREATE INDEX IF NOT EXISTS look_up_token     ON rag.look_up (token);
CREATE INDEX IF NOT EXISTS look_up_reg_id    ON rag.look_up (reg_id);

-- 설명 (테이블 정의서 기준)
COMMENT ON TABLE vault.person_registry IS '동명이인이 구분된 확정 인물사전 (fix)';
COMMENT ON COLUMN vault.person_registry.reg_id IS '확정 인물 ID';
COMMENT ON COLUMN vault.person_registry.reason IS '등록 사유';
COMMENT ON COLUMN vault.person_registry.created_at IS '등록 일시';
COMMENT ON COLUMN vault.person_registry.updated_at IS '마지막 수정 시점';
COMMENT ON TABLE vault.person_info IS '동일인을 찾아가기 위한 임시 인물사전 (tmp)';
COMMENT ON COLUMN vault.person_info.person_id IS '인물 후보 ID';
COMMENT ON COLUMN vault.person_info.reg_id IS '확정 인물 ID';
COMMENT ON COLUMN vault.person_info.token IS '이름 토큰 (HMAC)';
COMMENT ON COLUMN vault.person_info.role IS '인물 역할';
COMMENT ON COLUMN vault.person_info.info_label IS '식별 근거 토큰 배열 (phone, email 등)';
COMMENT ON COLUMN vault.person_info.evidence IS '판정 근거 (단서 위치·소유관계·판정사유)';
COMMENT ON COLUMN vault.person_info.doc_id IS '등장 문서 ID 배열';
COMMENT ON COLUMN vault.person_info.resolution_status IS '판정 상태';
COMMENT ON COLUMN vault.person_info.created_at IS '생성 시점';
COMMENT ON COLUMN vault.person_info.updated_at IS '편집 시각';
COMMENT ON TABLE rag.look_up IS '인물사전을 찾기 위한 인덱스 (fix + tmp)';
COMMENT ON COLUMN rag.look_up.person_id IS '문서별 인물 후보 ID';
COMMENT ON COLUMN rag.look_up.chunk_id IS '후보가 실제 등장한 청크';
COMMENT ON COLUMN rag.look_up.token IS '이름 토큰 (HMAC)';
COMMENT ON COLUMN rag.look_up.reg_id IS '확정 인물 ID. 미확정이면 NULL';
COMMENT ON COLUMN rag.look_up.role IS '인물 역할';
