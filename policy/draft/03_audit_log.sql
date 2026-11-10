-- [초안 · 팀 확정 전] 감사 로그(audit) 스키마
-- 확정 전까지 policy/draft/ 에 두며 자동 실행되지 않는다. 확정 후 infra/init/ 으로 옮기면
-- 새 DB에서 01_schema.sh, 02_metadata.sql 다음에 자동 실행된다.
-- 수동 적용(관리자 계정, 여러 번 실행해도 결과가 같다):
--   docker compose -p pii_starter --env-file .env -f infra/compose.yaml exec -T db \
--     psql -U admin -d pii_rag -v ON_ERROR_STOP=1 < policy/draft/03_audit_log.sql
--
-- 설계 결정
--   - 질문 처리 로그는 2개다. 질문 1건 = query_log 1행(재검사 결과 포함), 실명 복원 = restore_log 토큰마다 1행.
--     (재검사 로그를 따로 두던 recheck_log는 기록 단위가 질문 1건과 같아서 query_log에 합쳤다)
--   - query_log는 미들웨어가 응답 직전에 한 번 쓴다. 어느 경로로 끝나든(결과 없음·LLM 없음·오류) 반드시 1행을 남긴다.
--   - 두 로그는 request_id 로 잇는다. 외래키는 걸지 않는다(원본이 지워져도 로그는 남김).
--   - 원문 개인정보는 어떤 컬럼에도 저장하지 않는다. 질의는 가명처리된 텍스트만, 개인정보는 토큰만.
--   - user_id 는 로그인 ID 문자열(예: demo_lv1). level 은 요청 시점의 값을 복사한다.
--   - policy_version 에는 policy.policy_state.version 값을 문자열로 기록한다(04_policy_tables.sql).
--
-- 계정별 권한 (최소 권한)
--   rag_user   : audit.query_log 에 INSERT 만
--   vault_user : audit.restore_log 에 INSERT 만
--   읽기·수정·삭제 : 두 계정 모두 불가 (관리자만)
--   audit_reader(신규, WBS 3.4.1에서 적용): audit 스키마 SELECT만. 감사 조회 화면·평가 스크립트용
--     (적용 시 아래 권한 부분에 GRANT USAGE ON SCHEMA audit, GRANT SELECT ON ALL TABLES IN SCHEMA audit 추가)
--   vault 접근 기록은 실명 복원만(restore_log). 적재 단계의 vault 쓰기는 기록하지 않는다(정책서 9절 결정 11).
--   ※ INSERT만 허용하므로 앱에서 INSERT ... RETURNING 을 쓰면 실패한다(SELECT 권한 필요).
--
-- 검색 권한 필터
--   앱: policy.access_policy.search_filter(level) 결과가 None이 아니면 AND doc_type = ANY(%(allowed)s)
--   Lv2는 None(필터 없음)이라 미등록·NULL doc_type 문서도 포함된다.

BEGIN;

CREATE SCHEMA IF NOT EXISTS audit;

-- 조회 로그: 계획서 3절 "평가·감사용 로그에는 가명처리된 질의, 검색 결과, 응답 시간, 재검사 결과만 기록"
--          계획서 "출력 재검사 적발 건은 사용자·시각·토큰 단위로 감사 로그에 기록" → recheck_* 컬럼
CREATE TABLE IF NOT EXISTS audit.query_log (
    id               BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ts               TIMESTAMPTZ NOT NULL DEFAULT now(),
    request_id       TEXT NOT NULL,                  -- restore_log 와 잇는 키 (질문마다 미들웨어가 1개 발급)
    user_id          TEXT NOT NULL,
    level            SMALLINT NOT NULL CHECK (level BETWEEN 0 AND 2),   -- 요청 시점의 레벨 복사본
    team             TEXT,                           -- 요청 시점의 소속 팀(선택). 감사 분석·ABAC 전환 근거
    masked_query     TEXT NOT NULL,                  -- 가명처리된 질의만 (원문 금지)
    query_tokens     TEXT[] NOT NULL DEFAULT '{}',   -- search() 가 돌려준 질의 토큰
    returned_doc_ids TEXT[] NOT NULL DEFAULT '{}',   -- "권한 밖 문서 노출 0건" 검증용
    recheck_action   TEXT CHECK (recheck_action IN ('none', 'masked', 'blocked')),
                                                     -- NULL = 재검사 전에 끝남, none = 적발 없음, masked = 가림, blocked = 답변 차단
    recheck_labels   JSONB NOT NULL DEFAULT '{}'     -- 적발된 라벨별 개수, 예: {"phone_number": 1}. 원문 문자열은 넣지 않음
                     CHECK (jsonb_typeof(recheck_labels) = 'object'),
    recheck_detector TEXT,                           -- 재검사에 쓴 탐지기, 예: koelectra_post_kiwi_split_v3
    state            TEXT CHECK (state IN ('found', 'sorry', 'nollm', 'error')),
    latency_ms       INT CHECK (latency_ms >= 0),
    policy_version   TEXT NOT NULL,
    -- 적발이 있으면(masked/blocked) 라벨이 있어야 하고, 적발이 없으면 라벨이 비어 있어야 한다
    CONSTRAINT query_log_recheck_consistent
        CHECK (coalesce(recheck_action IN ('masked', 'blocked'), false) = (recheck_labels <> '{}'::jsonb))
);
CREATE INDEX IF NOT EXISTS query_log_ts      ON audit.query_log (ts);
CREATE INDEX IF NOT EXISTS query_log_user_ts ON audit.query_log (user_id, ts);

-- 실명 복원 로그: 계획서 "실명 복원은 Lv2에서만 허용하며 복원 이력을 감사 로그로 기록"
-- 실제로 복원을 수행한 경우에만 토큰마다 1행을 기록한다(복원이 일어나지 않는 Lv0·Lv1 요청은 기록하지 않음).
-- 이 기록에 실패하면 복원하지 않는다(기록 없는 실명 노출 방지).
-- 복원 규칙(Lv2만 전체 복원)은 코드에 고정이라 정책 버전을 남기지 않는다. 그 질문의 정책 버전은 request_id 로 query_log 에서 본다.
CREATE TABLE IF NOT EXISTS audit.restore_log (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ts              TIMESTAMPTZ NOT NULL DEFAULT now(),
    request_id      TEXT NOT NULL,
    user_id         TEXT NOT NULL,
    level           SMALLINT NOT NULL CHECK (level BETWEEN 0 AND 2),
    token           TEXT NOT NULL CHECK (token ~ '^<[A-Z_]+_[0-9a-f]{64}>$'),  -- 원문이 아닌 토큰
    label           TEXT NOT NULL,
    mode            TEXT NOT NULL CHECK (mode IN ('full'))              -- 부분 복원(partial) 도입 시 값 추가
);
CREATE INDEX IF NOT EXISTS restore_log_ts      ON audit.restore_log (ts);
CREATE INDEX IF NOT EXISTS restore_log_user_ts ON audit.restore_log (user_id, ts);

-- 검색 권한 필터용 인덱스
CREATE INDEX IF NOT EXISTS chunks_doc_type ON rag.chunks (doc_type);

-- 권한
REVOKE ALL ON SCHEMA audit FROM PUBLIC;
REVOKE ALL ON ALL TABLES IN SCHEMA audit FROM PUBLIC, rag_user, vault_user;
GRANT USAGE ON SCHEMA audit TO rag_user, vault_user;
GRANT INSERT ON audit.query_log TO rag_user;
GRANT INSERT ON audit.restore_log TO vault_user;

COMMIT;
