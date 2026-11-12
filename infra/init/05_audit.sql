-- 종합: 감사 로그 (query_log · restore_log · policy_change_log) + 정책 변경 기록 + 실명 복원 함수
-- 새 DB에서는 04_policy.sql 다음에 자동 실행된다.
-- 이미 있는 DB에는 README의 '메타데이터 스키마 적용'과 같은 방식으로 한 번 실행한다. 여러 번 실행해도 안전하다.
-- 감사 로그는 추가만 한다. 수정·삭제 권한은 06_grants.sh에서 막는다.

CREATE SCHEMA IF NOT EXISTS audit AUTHORIZATION rag_user;

-- 챗봇 질의·응답 로그. 응답 직전에 질문마다 1행
CREATE TABLE IF NOT EXISTS audit.query_log (
    query_log_id      BIGINT      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    request_id        TEXT        NOT NULL UNIQUE,          -- restore_log와 잇는 키
    user_id           TEXT        NOT NULL,
    level             SMALLINT    NOT NULL,                 -- 요청 시점 레벨 0~2
    team_cd           TEXT,                                 -- 요청 시점 팀
    position          TEXT,                                 -- 요청 시점 직책
    masked_query      TEXT        NOT NULL,                 -- 원문 금지
    query_tokens      TEXT[]      NOT NULL DEFAULT '{}',
    returned_doc_ids  TEXT[]      NOT NULL DEFAULT '{}',
    recheck_action    TEXT,                                 -- none / masked / blocked
    recheck_labels    JSONB       NOT NULL DEFAULT '{}',
    recheck_detector  TEXT,
    state             TEXT        NOT NULL,                 -- 코드값 시트 6개 값
    latency_ms        INT,
    policy_version    INT         NOT NULL,
    CONSTRAINT query_log_level_chk CHECK (level BETWEEN 0 AND 2),
    CONSTRAINT query_log_recheck_action_chk CHECK (recheck_action IS NULL OR recheck_action IN ('none', 'masked', 'blocked')),
    CONSTRAINT query_log_state_chk CHECK (state IN ('error', 'clarify', 'no_result', 'search_only', 'withheld', 'answered')),
    CONSTRAINT query_log_latency_chk CHECK (latency_ms IS NULL OR latency_ms >= 0)
);
ALTER TABLE audit.query_log OWNER TO rag_user;
CREATE INDEX IF NOT EXISTS query_log_user_created ON audit.query_log (user_id, created_at);

-- 실명 복원 로그. vault.restore_token()이 기록한다
CREATE TABLE IF NOT EXISTS audit.restore_log (
    restore_log_id  BIGINT      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    request_id      TEXT        NOT NULL,                   -- query_log.request_id
    user_id         TEXT        NOT NULL,
    level           SMALLINT    NOT NULL,
    token           TEXT        NOT NULL,
    label           TEXT        NOT NULL,
    mode            TEXT        NOT NULL,                   -- 현재 full만
    CONSTRAINT restore_log_level_chk CHECK (level BETWEEN 0 AND 2),
    CONSTRAINT restore_log_token_fmt CHECK (token ~ '^<[A-Z_]+_[0-9a-f]+>$'),
    CONSTRAINT restore_log_mode_chk  CHECK (mode IN ('full'))
);
ALTER TABLE audit.restore_log OWNER TO rag_user;
CREATE INDEX IF NOT EXISTS restore_log_request_id   ON audit.restore_log (request_id);
CREATE INDEX IF NOT EXISTS restore_log_user_created ON audit.restore_log (user_id, created_at);

-- 정책 변경 이력. 아래 트리거가 기록한다
CREATE TABLE IF NOT EXISTS audit.policy_change_log (
    policy_change_log_id  BIGINT      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    user_id               TEXT        NOT NULL,             -- app.user_id
    entity                TEXT        NOT NULL,             -- 예: policy.doc_type_level
    action                TEXT        NOT NULL,             -- insert / update / delete
    old_value             JSONB,                            -- insert면 NULL
    new_value             JSONB,                            -- delete면 NULL
    policy_version        INT,
    CONSTRAINT policy_change_log_action_chk CHECK (action IN ('insert', 'update', 'delete')),
    CONSTRAINT policy_change_log_values_chk CHECK (
        (action = 'insert' AND old_value IS NULL     AND new_value IS NOT NULL) OR
        (action = 'update' AND old_value IS NOT NULL AND new_value IS NOT NULL) OR
        (action = 'delete' AND old_value IS NOT NULL AND new_value IS NULL))
);
ALTER TABLE audit.policy_change_log OWNER TO rag_user;

-- 정책 표(doc_type_level, team_level)가 바뀌면 버전을 올리고 이력을 남긴다.
-- 한 트랜잭션 안의 여러 변경은 버전을 한 번만 올린다 (changed_txid로 판단)
CREATE OR REPLACE FUNCTION policy.tg_policy_change()
RETURNS trigger LANGUAGE plpgsql
SECURITY DEFINER SET search_path = pg_catalog, pg_temp
AS $$
DECLARE
    v_user text := nullif(current_setting('app.user_id', true), '');
    v_ver  int;
BEGIN
    IF v_user IS NULL THEN
        RAISE EXCEPTION 'app.user_id가 설정되지 않았습니다' USING ERRCODE = '28000';
    END IF;

    UPDATE policy.policy_state
       SET version = version + 1, changed_txid = txid_current(),
           updated_at = now(), updated_by = v_user
     WHERE policy_state_id = 1
       AND changed_txid IS DISTINCT FROM txid_current();

    SELECT version INTO v_ver FROM policy.policy_state WHERE policy_state_id = 1;

    INSERT INTO audit.policy_change_log (user_id, entity, action, old_value, new_value, policy_version)
    VALUES (v_user, TG_TABLE_SCHEMA || '.' || TG_TABLE_NAME, lower(TG_OP),
            CASE WHEN TG_OP IN ('UPDATE', 'DELETE') THEN to_jsonb(OLD) END,
            CASE WHEN TG_OP IN ('INSERT', 'UPDATE') THEN to_jsonb(NEW) END,
            v_ver);
    RETURN NULL;
END;
$$;
ALTER FUNCTION policy.tg_policy_change() OWNER TO rag_user;

CREATE OR REPLACE TRIGGER doc_type_level_change_log
    AFTER INSERT OR UPDATE OR DELETE ON policy.doc_type_level
    FOR EACH ROW EXECUTE FUNCTION policy.tg_policy_change();

CREATE OR REPLACE TRIGGER team_level_change_log
    AFTER INSERT OR UPDATE OR DELETE ON policy.team_level
    FOR EACH ROW EXECUTE FUNCTION policy.tg_policy_change();

-- 실명 복원: 레벨 2 확인 → restore_log 기록 → 원문 표기 배열 반환.
-- vault 표에 권한이 없는 검색 계정도 이 함수로만 복원하므로 기록 없는 복원이 생기지 않는다
CREATE OR REPLACE FUNCTION vault.restore_token(p_request_id text, p_user_id text, p_token text)
RETURNS text[] LANGUAGE plpgsql
SECURITY DEFINER SET search_path = pg_catalog, pg_temp
AS $$
DECLARE
    v_level smallint;
    v_label text;
    v_orig  text[];
BEGIN
    SELECT access_level INTO v_level
      FROM policy.users
     WHERE user_id = p_user_id AND is_active;

    IF v_level IS DISTINCT FROM 2 THEN
        RAISE EXCEPTION '복원 권한이 없습니다 (user_id=%)', p_user_id USING ERRCODE = '42501';
    END IF;

    SELECT label, originals INTO v_label, v_orig
      FROM vault.token_map
     WHERE token = p_token;
    IF NOT FOUND THEN
        RETURN NULL;
    END IF;

    INSERT INTO audit.restore_log (request_id, user_id, level, token, label, mode)
    VALUES (p_request_id, p_user_id, v_level, p_token, v_label, 'full');
    RETURN v_orig;
END;
$$;
ALTER FUNCTION vault.restore_token(text, text, text) OWNER TO vault_user;
REVOKE ALL ON FUNCTION vault.restore_token(text, text, text) FROM PUBLIC;

-- 설명 (테이블 정의서 기준)
COMMENT ON TABLE audit.query_log IS '챗봇 질의·응답 대화 로그';
COMMENT ON COLUMN audit.query_log.query_log_id IS '행 번호';
COMMENT ON COLUMN audit.query_log.created_at IS '기록 시각 (응답 직전)';
COMMENT ON COLUMN audit.query_log.request_id IS 'restore_log와 잇는 키';
COMMENT ON COLUMN audit.query_log.user_id IS '로그인 ID';
COMMENT ON COLUMN audit.query_log.level IS '요청 시점의 권한 레벨';
COMMENT ON COLUMN audit.query_log.team_cd IS '요청 시점의 소속 팀 코드';
COMMENT ON COLUMN audit.query_log.position IS '요청 시점의 직책';
COMMENT ON COLUMN audit.query_log.masked_query IS '가명처리된 질의';
COMMENT ON COLUMN audit.query_log.query_tokens IS '질의 토큰 목록';
COMMENT ON COLUMN audit.query_log.returned_doc_ids IS '반환한 문서 ID';
COMMENT ON COLUMN audit.query_log.recheck_action IS '출력 재검사 조치';
COMMENT ON COLUMN audit.query_log.recheck_labels IS '적발된 라벨별 개수';
COMMENT ON COLUMN audit.query_log.recheck_detector IS '재검사에 쓴 탐지기';
COMMENT ON COLUMN audit.query_log.state IS '최종 응답 상태';
COMMENT ON COLUMN audit.query_log.latency_ms IS '응답 시간(ms)';
COMMENT ON COLUMN audit.query_log.policy_version IS '판단에 사용한 정책 버전';
COMMENT ON TABLE audit.restore_log IS '실명 복원 로그';
COMMENT ON COLUMN audit.restore_log.restore_log_id IS '행 번호';
COMMENT ON COLUMN audit.restore_log.created_at IS '기록 시각';
COMMENT ON COLUMN audit.restore_log.request_id IS '요청 묶음 키';
COMMENT ON COLUMN audit.restore_log.user_id IS '로그인 ID';
COMMENT ON COLUMN audit.restore_log.level IS '요청 시점의 권한 레벨';
COMMENT ON COLUMN audit.restore_log.token IS '복원 대상 토큰';
COMMENT ON COLUMN audit.restore_log.label IS '토큰의 라벨';
COMMENT ON COLUMN audit.restore_log.mode IS '적용된 복원 모드';
COMMENT ON TABLE audit.policy_change_log IS '정책 변경 이력';
COMMENT ON COLUMN audit.policy_change_log.policy_change_log_id IS '행 번호';
COMMENT ON COLUMN audit.policy_change_log.created_at IS '기록 시각';
COMMENT ON COLUMN audit.policy_change_log.user_id IS '편집한 관리자 ID';
COMMENT ON COLUMN audit.policy_change_log.entity IS '변경한 영역(표)';
COMMENT ON COLUMN audit.policy_change_log.action IS '변경 종류';
COMMENT ON COLUMN audit.policy_change_log.old_value IS '변경 전 행';
COMMENT ON COLUMN audit.policy_change_log.new_value IS '변경 후 행';
COMMENT ON COLUMN audit.policy_change_log.policy_version IS '변경 후 정책 버전';
