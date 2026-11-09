-- 종합: 인물 사전 (인물사전 분석 및 설계 5장)
-- 새 DB에서는 01_schema.sh, 02_metadata.sql 다음에 자동 실행된다.
-- 이미 있는 DB에는 한 번 직접 실행한다. 여러 번 실행해도 안전하다.
-- 원칙: 사람 단위 묶음은 vault에만 (검색 계정 접근 금지). rag에는 질의용 색인 person_lookup만.

-- ───────────────────────── vault (vault_user 전용) ─────────────────────────

-- 5.5 인물 한 명당 한 줄. 이름·역할·문서 목록은 넣지 않는다.
CREATE TABLE IF NOT EXISTS vault.person_registry (
    person_id    UUID PRIMARY KEY,                                   -- 랜덤 UUIDv4 (코드에서 생성)
    status       TEXT NOT NULL CHECK (status IN ('confirmed', 'provisional', 'merged')),
    merged_into  UUID REFERENCES vault.person_registry (person_id),   -- merged일 때 대상
    gender       TEXT CHECK (gender IN ('M', 'F')),                  -- 주민번호 7번째 자리에서만
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 5.6 이름 조회 키 → 인물 (원문 이름 없이 HMAC)
CREATE TABLE IF NOT EXISTS vault.person_alias (
    name_hmac   TEXT NOT NULL,                                       -- HMAC("alias:" + 정규화 이름)
    person_id   UUID NOT NULL REFERENCES vault.person_registry (person_id),
    kind        TEXT NOT NULL CHECK (kind IN ('full', 'given')),
    rule_id     TEXT,                                                -- 연결 근거 (R2, R4 …)
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (name_hmac, person_id, kind)
);

-- 5.4 기존 token_map에 열 추가: 확정 인물 토큰일 때만 값이 있다
ALTER TABLE vault.token_map
    ADD COLUMN IF NOT EXISTS person_id UUID REFERENCES vault.person_registry (person_id);
CREATE INDEX IF NOT EXISTS token_map_person_id ON vault.token_map (person_id);

-- 5.7 강한 식별자(주민번호·전화·계좌·이메일 토큰) → 인물
CREATE TABLE IF NOT EXISTS vault.person_identifier (
    id_token      TEXT NOT NULL REFERENCES vault.token_map (token),
    person_id     UUID NOT NULL REFERENCES vault.person_registry (person_id),
    first_doc_id  TEXT NOT NULL,
    linkable      BOOLEAN NOT NULL DEFAULT true,                     -- 공유 식별자면 false
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (id_token, person_id)
);

-- 5.8 병합·분리 이력 (되돌리기용, 기록 보존을 위해 FK 없음)
CREATE TABLE IF NOT EXISTS vault.person_merge_log (
    log_id          BIGSERIAL PRIMARY KEY,
    action          TEXT NOT NULL CHECK (action IN ('merge', 'split', 'promote')),
    from_person_id  UUID,
    to_person_id    UUID,
    from_token      TEXT,
    to_token        TEXT,
    mentions        JSONB NOT NULL DEFAULT '[]',                     -- [{doc_id, mention}]
    rule_id         TEXT,
    actor           TEXT NOT NULL DEFAULT 'auto',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE vault.person_registry   OWNER TO vault_user;
ALTER TABLE vault.person_alias      OWNER TO vault_user;
ALTER TABLE vault.person_identifier OWNER TO vault_user;
ALTER TABLE vault.person_merge_log  OWNER TO vault_user;

-- ───────────────────────── rag (rag_user, 검색용) ─────────────────────────

-- 5.3 질문 속 이름 → 후보 인물 토큰. 원문·식별자·사람 번호 없음
CREATE TABLE IF NOT EXISTS rag.person_lookup (
    name_hmac   TEXT NOT NULL,                                       -- vault.person_alias와 같은 값
    token       TEXT NOT NULL,                                       -- 후보 인물 토큰
    status      TEXT NOT NULL CHECK (status IN ('confirmed', 'provisional')),
    roles       TEXT[] NOT NULL DEFAULT '{}',                        -- unknown 제외
    doc_count   INT NOT NULL DEFAULT 0,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (name_hmac, token)
);
ALTER TABLE rag.person_lookup OWNER TO rag_user;
