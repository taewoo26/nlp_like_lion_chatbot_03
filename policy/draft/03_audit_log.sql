-- [초안 · 팀 확정 전] 감사 로그(audit) 스키마
-- 확정 전까지 policy/draft/ 에 두며 자동 실행되지 않는다. 확정 후 infra/init/ 으로 옮기면
-- 새 DB에서 01_schema.sh, 02_person_roles.sql 다음에 자동 실행된다.
-- 수동 적용(관리자 계정, 여러 번 실행해도 결과가 같다):
--   docker compose -p pii_starter --env-file .env -f infra/compose.yaml exec -T db \
--     psql -U admin -d pii_rag -v ON_ERROR_STOP=1 < policy/draft/03_audit_log.sql
--
-- 설계 결정 (검토안 반영)
--   - 정책은 policy/access_policy.yaml 이 단일 출처. DB에는 정책 테이블을 두지 않고 로그에 policy_version 만 남긴다.
--     (DB 정책 테이블 초안은 policy/draft/optional/ 에 보관: RLS 등 확장 시 사용)
--   - 로그는 검색/복원 계정 경계를 따라 3개 테이블로 분리한다.
--   - 원문 개인정보는 어떤 컬럼에도 저장하지 않는다. 질의는 가명처리된 텍스트만, 개인정보는 토큰만.
--   - user_id 는 로그인 ID 문자열(예: demo_lv1). 다른 테이블과 외래키로 잇지 않는다(계정이 지워져도 로그는 남김).
--
-- 계정별 권한 (최소 권한)
--   rag_user   : audit.query_log, audit.recheck_log 에 INSERT 만
--   vault_user : audit.restore_log 에 INSERT 만
--   읽기·수정·삭제 : 두 계정 모두 불가 (관리자만). Lv2 조회는 관리자 계정으로 실행하는 스크립트로 한다.
--   ※ INSERT만 허용하므로 앱에서 INSERT ... RETURNING 을 쓰면 실패한다(SELECT 권한 필요).
--
-- 검색 권한 필터
--   앱: policy.access_policy.search_filter(level) 결과가 None이 아니면 AND doc_type = ANY(%(allowed)s)
--   Lv2는 None(필터 없음)이라 미등록·NULL doc_type 문서도 포함된다.

BEGIN;

CREATE SCHEMA IF NOT EXISTS audit;

-- 조회 로그: 계획서 3절 "평가·감사용 로그에는 가명처리된 질의, 검색 결과, 응답 시간, 재검사 결과만 기록"
CREATE TABLE IF NOT EXISTS audit.query_log (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ts              TIMESTAMPTZ NOT NULL DEFAULT now(),
    request_id      TEXT NOT NULL,                  -- 한 요청의 query/recheck/restore 로그를 잇는 키
    user_id         TEXT NOT NULL,
    level           SMALLINT NOT NULL CHECK (level BETWEEN 0 AND 2),   -- 요청 시점의 레벨 복사본
    masked_query    TEXT NOT NULL,                  -- 가명처리된 질의만 (원문 금지)
    query_tokens    TEXT[] NOT NULL DEFAULT '{}',
    returned_doc_ids TEXT[] NOT NULL DEFAULT '{}',  -- "권한 밖 문서 노출 0건" 검증용
    state           TEXT CHECK (state IN ('found', 'sorry', 'nollm')),
    latency_ms      INT CHECK (latency_ms >= 0),
    policy_version  TEXT NOT NULL
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
    label           TEXT NOT NULL,                  -- 적발된 개인정보 라벨
    span_count      INT NOT NULL CHECK (span_count > 0),
    action          TEXT NOT NULL CHECK (action IN ('masked', 'blocked')),
    detector        TEXT,                           -- 예: koelectra_post_kiwi_split_v3
    policy_version  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS recheck_log_ts ON audit.recheck_log (ts);

-- 실명 복원 로그: 계획서 "실명 복원은 Lv2에서만 허용하며 복원 이력을 감사 로그로 기록"
-- 실제로 복원을 수행한 경우에만 토큰마다 1행을 기록한다(복원이 일어나지 않는 Lv0·Lv1 요청은 기록하지 않음).
-- Lv0·Lv1의 복원 차단은 query_log의 level + policy_version 으로 재현할 수 있어 별도 기록이 필요 없다.
CREATE TABLE IF NOT EXISTS audit.restore_log (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ts              TIMESTAMPTZ NOT NULL DEFAULT now(),
    request_id      TEXT NOT NULL,
    user_id         TEXT NOT NULL,
    level           SMALLINT NOT NULL CHECK (level BETWEEN 0 AND 2),
    token           TEXT NOT NULL CHECK (token ~ '^<[A-Z_]+_[0-9a-f]{64}>$'),  -- 원문이 아닌 토큰
    label           TEXT NOT NULL,
    mode            TEXT NOT NULL CHECK (mode IN ('full')),             -- 부분 복원(partial) 도입 시 값 추가
    policy_version  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS restore_log_ts      ON audit.restore_log (ts);
CREATE INDEX IF NOT EXISTS restore_log_user_ts ON audit.restore_log (user_id, ts);

-- 검색 권한 필터용 인덱스
CREATE INDEX IF NOT EXISTS chunks_doc_type ON rag.chunks (doc_type);

-- 권한
REVOKE ALL ON SCHEMA audit FROM PUBLIC;
REVOKE ALL ON ALL TABLES IN SCHEMA audit FROM PUBLIC, rag_user, vault_user;
GRANT USAGE ON SCHEMA audit TO rag_user, vault_user;
GRANT INSERT ON audit.query_log, audit.recheck_log TO rag_user;
GRANT INSERT ON audit.restore_log TO vault_user;

COMMIT;
