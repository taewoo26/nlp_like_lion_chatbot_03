-- [대체됨 2026-11-09] 이 보관안은 policy/draft/04_policy_tables.sql(관리자 화면 편집용 정책 테이블)로 대체되었다. 참고용으로만 남긴다.
-- [보관용 · 현재 채택하지 않음] 정책 테이블을 DB에도 두는 안(RLS 등 확장 시 사용). 채택안은 policy/draft/03_audit_log.sql
-- 확정 전까지 policy/draft/ 에 두며 자동 실행되지 않는다. 확정 후 infra/init/ 으로 옮기면
-- 새 DB에서 01_schema.sh, 02_person_roles.sql 다음에 자동 실행된다.
-- 수동 적용(관리자 계정, 여러 번 실행해도 결과가 같다):
--   docker compose -p pii_starter --env-file .env -f infra/compose.yaml exec -T db \
--     psql -U admin -d pii_rag -v ON_ERROR_STOP=1 < policy/draft/optional/full_with_policy_tables.sql
--
-- 정책의 단일 출처는 policy/access_policy.yaml 이다. 이 스키마의 policy.* 테이블은
-- YAML에서 생성한 04_policy_seed.sql 로 채우는 사본이며, 직접 수정하지 않는다.
--
-- 계정별 권한 (최소 권한)
--   rag_user   : policy.levels, policy.doc_type_access 읽기 / audit.query_log, audit.recheck_log 쓰기(INSERT만)
--   vault_user : policy.restore_rule 읽기 / audit.restore_log 쓰기(INSERT만)
--   감사 로그 읽기·수정·삭제 : 두 계정 모두 불가 (관리자만)
--   ※ 감사 로그는 INSERT만 허용하므로 앱에서 INSERT ... RETURNING 을 쓰면 실패한다(SELECT 권한 필요).
--
-- 검색 권한 필터
--   앱: policy.access_policy.search_filter(level) 결과가 None이 아니면 AND doc_type = ANY(%(allowed)s)
--   DB 안에서 판단할 때(미등록·NULL doc_type은 기본값 Lv2로 처리):
--     WHERE COALESCE((SELECT p.min_level FROM policy.doc_type_access p WHERE p.doc_type = c.doc_type),
--                    (SELECT v.unregistered_min_level FROM policy.policy_version v WHERE v.is_current)) <= :level
--   doc_type = ANY(허용 목록)만 쓰면 Lv2에서도 미등록 문서가 빠지므로 주의.

BEGIN;

-- ---------------------------------------------------------------- policy
CREATE SCHEMA IF NOT EXISTS policy;

CREATE TABLE IF NOT EXISTS policy.policy_version (
    version                  TEXT PRIMARY KEY,                -- 예: v0.3
    updated                  DATE,                            -- YAML의 updated
    status                   TEXT,                            -- 예: 팀 검토 전
    unregistered_min_level   SMALLINT NOT NULL CHECK (unregistered_min_level BETWEEN 0 AND 2),
    audit_log_read_min_level SMALLINT NOT NULL CHECK (audit_log_read_min_level BETWEEN 0 AND 2),
    source_sha256            TEXT NOT NULL,                   -- 적재한 YAML 파일의 해시
    is_current               BOOLEAN NOT NULL DEFAULT false,
    loaded_at                TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- 현재 버전은 하나만
CREATE UNIQUE INDEX IF NOT EXISTS policy_version_one_current
    ON policy.policy_version (is_current) WHERE is_current;

CREATE TABLE IF NOT EXISTS policy.levels (
    level        SMALLINT PRIMARY KEY CHECK (level BETWEEN 0 AND 2),
    name         TEXT NOT NULL,
    description  TEXT
);

CREATE TABLE IF NOT EXISTS policy.doc_type_access (
    doc_type   TEXT PRIMARY KEY,
    min_level  SMALLINT NOT NULL REFERENCES policy.levels(level),
    rule       SMALLINT NOT NULL CHECK (rule BETWEEN 1 AND 5),   -- 정책서 3절 판정 규칙 번호
    rationale  TEXT NOT NULL,
    note       TEXT,
    version    TEXT NOT NULL REFERENCES policy.policy_version(version)
);

CREATE TABLE IF NOT EXISTS policy.restore_rule (
    level    SMALLINT NOT NULL REFERENCES policy.levels(level),
    label    TEXT NOT NULL CHECK (label IN ('person', 'phone_number', 'email', 'address',
                                            'resident_registration_number', 'account_number', 'card_number')),
    mode     TEXT NOT NULL CHECK (mode IN ('none', 'full')),     -- partial은 추후 논의 후 추가
    version  TEXT NOT NULL REFERENCES policy.policy_version(version),
    PRIMARY KEY (level, label)
);

-- ---------------------------------------------------------------- audit
-- 원문 개인정보는 어떤 컬럼에도 저장하지 않는다. 질의는 가명처리된 텍스트만, 개인정보는 토큰만 기록한다.
CREATE SCHEMA IF NOT EXISTS audit;

-- 조회 로그: 계획서 3절 "평가·감사용 로그에는 가명처리된 질의, 검색 결과, 응답 시간, 재검사 결과만 기록"
CREATE TABLE IF NOT EXISTS audit.query_log (
    id                  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ts                  TIMESTAMPTZ NOT NULL DEFAULT now(),
    request_id          TEXT NOT NULL,                  -- 한 요청의 query/recheck/restore 로그를 잇는 키
    user_id             TEXT NOT NULL,
    level               SMALLINT NOT NULL CHECK (level BETWEEN 0 AND 2),
    masked_query        TEXT NOT NULL,                  -- 가명처리된 질의만 (원문 금지)
    query_tokens        TEXT[] NOT NULL DEFAULT '{}',
    allowed_doc_types   TEXT[] NOT NULL,                -- 이번 요청에 적용한 허용 목록
    returned_doc_ids    TEXT[] NOT NULL DEFAULT '{}',
    filtered_out_count  INT CHECK (filtered_out_count >= 0),   -- 권한 필터로 제외된 후보 수(계산 가능할 때만)
    state               TEXT CHECK (state IN ('found', 'sorry', 'nollm')),
    latency_ms          INT CHECK (latency_ms >= 0),
    policy_version      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS query_log_ts      ON audit.query_log (ts);
CREATE INDEX IF NOT EXISTS query_log_user_ts ON audit.query_log (user_id, ts);

-- 출력 재검사 적발 로그: 계획서 "복원 요청과 출력 재검사 적발 건은 사용자·시각·토큰 단위로 감사 로그에 기록"
CREATE TABLE IF NOT EXISTS audit.recheck_log (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ts              TIMESTAMPTZ NOT NULL DEFAULT now(),
    request_id      TEXT NOT NULL,
    user_id         TEXT NOT NULL,
    level           SMALLINT NOT NULL CHECK (level BETWEEN 0 AND 2),
    label           TEXT NOT NULL,                      -- 적발된 개인정보 라벨
    span_count      INT NOT NULL CHECK (span_count > 0),
    action          TEXT NOT NULL CHECK (action IN ('masked', 'blocked')),
    detector        TEXT,                               -- 예: koelectra_post_kiwi_split_v3
    policy_version  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS recheck_log_ts ON audit.recheck_log (ts);

-- 실명 복원 로그: 계획서 "실명 복원은 Lv2에서만 허용하며 복원 이력을 감사 로그로 기록"
CREATE TABLE IF NOT EXISTS audit.restore_log (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ts              TIMESTAMPTZ NOT NULL DEFAULT now(),
    request_id      TEXT NOT NULL,
    user_id         TEXT NOT NULL,
    level           SMALLINT NOT NULL CHECK (level BETWEEN 0 AND 2),
    token           TEXT NOT NULL CHECK (token ~ '^<[A-Z_]+_[0-9a-f]{64}>$'),  -- 원문이 아닌 토큰
    label           TEXT NOT NULL,
    mode            TEXT NOT NULL CHECK (mode IN ('none', 'full')),     -- none = 복원 거부도 기록
    policy_version  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS restore_log_ts      ON audit.restore_log (ts);
CREATE INDEX IF NOT EXISTS restore_log_user_ts ON audit.restore_log (user_id, ts);

-- ---------------------------------------------------------------- 검색 필터용 인덱스
CREATE INDEX IF NOT EXISTS chunks_doc_type ON rag.chunks (doc_type);

-- ---------------------------------------------------------------- 권한
REVOKE ALL ON SCHEMA policy, audit FROM PUBLIC;
REVOKE ALL ON ALL TABLES IN SCHEMA policy, audit FROM PUBLIC, rag_user, vault_user;

GRANT USAGE ON SCHEMA policy TO rag_user, vault_user;
GRANT SELECT ON policy.policy_version TO rag_user, vault_user;
GRANT SELECT ON policy.levels, policy.doc_type_access TO rag_user;
GRANT SELECT ON policy.restore_rule TO vault_user;

GRANT USAGE ON SCHEMA audit TO rag_user, vault_user;
GRANT INSERT ON audit.query_log, audit.recheck_log TO rag_user;    -- 쓰기만 (읽기·수정·삭제 불가)
GRANT INSERT ON audit.restore_log TO vault_user;

COMMIT;
