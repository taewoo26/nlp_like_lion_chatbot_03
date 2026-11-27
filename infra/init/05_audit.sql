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

-- 재질문(CLR): state 는 그대로 두고 원인과 대화 흐름을 따로 남긴다. 이미 있는 DB 에도 다시 실행하면 적용된다
--   clarify_code : 재질문 원인 (starter/clarify.py). 재질문이 아니면 NULL
--   follow_up_of : 재질문 바로 다음 질문이면 그 재질문의 request_id (같은 사용자 질문만, app.py 가 확인)
ALTER TABLE audit.query_log ADD COLUMN IF NOT EXISTS clarify_code TEXT;
ALTER TABLE audit.query_log ADD COLUMN IF NOT EXISTS follow_up_of TEXT;
ALTER TABLE audit.query_log DROP CONSTRAINT IF EXISTS query_log_clarify_code_chk;
ALTER TABLE audit.query_log ADD CONSTRAINT query_log_clarify_code_chk CHECK (clarify_code IS NULL OR clarify_code IN (
    'too_vague', 'ask_region', 'ask_doc_type', 'ask_person',
    'person', 'role', 'region', 'token', 'unknown', 'keyword', 'withheld'));
CREATE INDEX IF NOT EXISTS query_log_follow_up_of ON audit.query_log (follow_up_of);
COMMENT ON COLUMN audit.query_log.clarify_code IS '재질문 원인 코드 (CLR-01~10, starter/clarify.py)';
COMMENT ON COLUMN audit.query_log.follow_up_of IS '이 질문이 이어받은 재질문의 request_id';

-- 실명 복원 로그. vault.restore_token()이 기록한다
CREATE TABLE IF NOT EXISTS audit.restore_log (
    restore_log_id  BIGINT      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    request_id      TEXT        NOT NULL,                   -- query_log.request_id
    user_id         TEXT        NOT NULL,
    level           SMALLINT    NOT NULL,
    token           TEXT        NOT NULL,
    label           TEXT        NOT NULL,
    mode            TEXT        NOT NULL,                   -- full(Lv2 실명 복원) / partial(Lv1 이름 부분 공개)
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

-- 실명 복원: 레벨 2 확인 → 본인 질문 확인 → 그 질문 결과의 토큰인지 확인 → restore_log 기록 → 원문 표기 배열 반환.
-- vault 표에 권한이 없는 검색 계정도 이 함수로만 복원하므로 기록 없는 복원이 생기지 않는다
-- 2026-11-26: partial_token()과 같은 확인(본인 질문, 결과에 나온 토큰)을 추가했다. 검색 전 권한 필터가 반환 문서를
--   열람 가능한 문서로 제한하므로, 이 확인으로 "열람 가능한 문서의 토큰만 복원"이 성립한다.
--   요청 단위 확인이 틀리면 42501 로 거절(기록 없음), 토큰 단위 확인이 틀리면 그 토큰만 NULL(기록 없음).
CREATE OR REPLACE FUNCTION vault.restore_token(p_request_id text, p_user_id text, p_token text)
RETURNS text[] LANGUAGE plpgsql
SECURITY DEFINER SET search_path = pg_catalog, pg_temp
AS $$
DECLARE
    v_level   smallint;
    v_label   text;
    v_orig    text[];
    v_qtokens text[];
    v_docs    text[];
BEGIN
    SELECT access_level INTO v_level
      FROM policy.users
     WHERE user_id = p_user_id AND is_active;

    IF v_level IS DISTINCT FROM 2 THEN
        RAISE EXCEPTION '복원 권한이 없습니다 (user_id=%)', p_user_id USING ERRCODE = '42501';
    END IF;

    SELECT query_tokens, returned_doc_ids INTO v_qtokens, v_docs
      FROM audit.query_log
     WHERE request_id = p_request_id AND user_id = p_user_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION '본인 질문이 아니거나 없는 요청입니다 (request_id=%)', p_request_id USING ERRCODE = '42501';
    END IF;

    SELECT label, originals INTO v_label, v_orig
      FROM vault.token_map
     WHERE token = p_token;
    IF NOT FOUND THEN
        RETURN NULL;
    END IF;

    IF NOT (p_token = ANY(coalesce(v_qtokens, '{}')) OR EXISTS (
            SELECT 1 FROM rag.chunks
             WHERE doc_id = ANY(coalesce(v_docs, '{}')) AND p_token = ANY(tokens))) THEN
        RETURN NULL;
    END IF;

    INSERT INTO audit.restore_log (request_id, user_id, level, token, label, mode)
    VALUES (p_request_id, p_user_id, v_level, p_token, v_label, 'full');
    RETURN v_orig;
END;
$$;
ALTER FUNCTION vault.restore_token(text, text, text) OWNER TO vault_user;
REVOKE ALL ON FUNCTION vault.restore_token(text, text, text) FROM PUBLIC;

-- 부분 공개(Lv1): restore_log.mode 에 'partial' 을 더한다. 이미 있는 DB 에도 다시 실행하면 적용된다
ALTER TABLE audit.restore_log DROP CONSTRAINT IF EXISTS restore_log_mode_chk;
ALTER TABLE audit.restore_log ADD CONSTRAINT restore_log_mode_chk CHECK (mode IN ('full', 'partial'));

-- 이름 마스킹: 공백을 빼고 첫 글자·끝 글자만 남긴다. 윤선영 → 윤*영, 남궁민수 → 남**수, 김민 → 김*
CREATE OR REPLACE FUNCTION vault.mask_name(p_name text)
RETURNS text LANGUAGE sql IMMUTABLE
SET search_path = pg_catalog, pg_temp
AS $$
    SELECT CASE
        WHEN n IS NULL OR length(n) = 0 THEN NULL
        WHEN length(n) = 1 THEN '*'
        WHEN length(n) = 2 THEN left(n, 1) || '*'
        ELSE left(n, 1) || repeat('*', length(n) - 2) || right(n, 1)
    END
    FROM (SELECT regexp_replace(p_name, '\s+', '', 'g') AS n) s
$$;
ALTER FUNCTION vault.mask_name(text) OWNER TO vault_user;
REVOKE ALL ON FUNCTION vault.mask_name(text) FROM PUBLIC;

-- 이름 부분 공개 (Lv1 전용): 원문은 함수 밖으로 내보내지 않고 마스킹한 값만 돌려준다.
--   요청 단위 확인 (하나라도 틀리면 42501 로 거절, 기록 없음)
--     1) 활성 Lv1 계정인가
--     2) request_id 가 이 사용자의 질문인가 (audit.query_log 의 request_id·user_id)
--   토큰 단위 확인 (틀리면 그 토큰만 NULL, 기록 없음 → 화면에는 칩 그대로)
--     3) vault.token_map 에 있는 이름(person) 토큰인가
--     4) 그 질문의 결과에 나온 토큰인가 (query_tokens, 또는 returned_doc_ids 문서의 rag.chunks.tokens)
--   모두 맞으면 audit.restore_log 에 mode='partial' 로 기록하고 마스킹 값을 돌려준다.
CREATE OR REPLACE FUNCTION vault.partial_token(p_request_id text, p_user_id text, p_token text)
RETURNS text LANGUAGE plpgsql
SECURITY DEFINER SET search_path = pg_catalog, pg_temp
AS $$
DECLARE
    v_level   smallint;
    v_label   text;
    v_orig    text[];
    v_qtokens text[];
    v_docs    text[];
BEGIN
    SELECT access_level INTO v_level
      FROM policy.users
     WHERE user_id = p_user_id AND is_active;

    IF v_level IS DISTINCT FROM 1 THEN          -- Lv2 도 쓰게 하려면: v_level IS NULL OR v_level < 1
        RAISE EXCEPTION '부분 공개 권한이 없습니다 (user_id=%)', p_user_id USING ERRCODE = '42501';
    END IF;

    SELECT query_tokens, returned_doc_ids INTO v_qtokens, v_docs
      FROM audit.query_log
     WHERE request_id = p_request_id AND user_id = p_user_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION '본인 질문이 아니거나 없는 요청입니다 (request_id=%)', p_request_id USING ERRCODE = '42501';
    END IF;

    SELECT label, originals INTO v_label, v_orig
      FROM vault.token_map
     WHERE token = p_token;
    IF NOT FOUND OR lower(v_label) <> 'person' OR coalesce(cardinality(v_orig), 0) = 0 THEN
        RETURN NULL;
    END IF;

    IF NOT (p_token = ANY(v_qtokens) OR EXISTS (
            SELECT 1 FROM rag.chunks
             WHERE doc_id = ANY(v_docs) AND p_token = ANY(tokens))) THEN
        RETURN NULL;
    END IF;

    INSERT INTO audit.restore_log (request_id, user_id, level, token, label, mode)
    VALUES (p_request_id, p_user_id, v_level, p_token, v_label, 'partial');
    RETURN vault.mask_name(v_orig[1]);
END;
$$;
ALTER FUNCTION vault.partial_token(text, text, text) OWNER TO vault_user;
REVOKE ALL ON FUNCTION vault.partial_token(text, text, text) FROM PUBLIC;

-- 탐지 검수: 문서 토큰(NER 탐지 결과)이 올바른지 확인하는 기록. 문서 단위로 토큰마다 1행
CREATE TABLE IF NOT EXISTS audit.detection_review (
    detection_review_id  BIGINT      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    doc_id               TEXT        NOT NULL,
    token                TEXT        NOT NULL,
    verdict              TEXT        NOT NULL,
    new_label            TEXT,
    reviewer             TEXT        NOT NULL,
    note                 TEXT,
    CONSTRAINT detection_review_verdict_chk CHECK (verdict IN ('confirmed', 'rejected', 'relabeled')),
    CONSTRAINT detection_review_token_fmt CHECK (token ~ '^<[A-Z_]+_[0-9A-Za-z]+>$'),
    CONSTRAINT detection_review_relabel_chk CHECK (
        (verdict = 'relabeled' AND new_label IS NOT NULL) OR
        (verdict != 'relabeled' AND new_label IS NULL)),
    CONSTRAINT detection_review_unique UNIQUE (doc_id, token)
);
ALTER TABLE audit.detection_review OWNER TO rag_user;
CREATE INDEX IF NOT EXISTS detection_review_doc ON audit.detection_review (doc_id);
CREATE INDEX IF NOT EXISTS detection_review_reviewer ON audit.detection_review (reviewer, created_at);

COMMENT ON TABLE audit.detection_review IS '탐지 검수 기록 (문서 토큰별)';
COMMENT ON COLUMN audit.detection_review.detection_review_id IS '행 번호';
COMMENT ON COLUMN audit.detection_review.created_at IS '검수 시각';
COMMENT ON COLUMN audit.detection_review.doc_id IS '문서 ID';
COMMENT ON COLUMN audit.detection_review.token IS '검수 대상 토큰';
COMMENT ON COLUMN audit.detection_review.verdict IS '판정: confirmed(정탐) / rejected(오탐) / relabeled(라벨 수정)';
COMMENT ON COLUMN audit.detection_review.new_label IS '수정된 라벨 (relabeled인 경우만)';
COMMENT ON COLUMN audit.detection_review.reviewer IS '검수자 사번';
COMMENT ON COLUMN audit.detection_review.note IS '검수 메모';

-- 문서 업로드 로그: 관리자가 업로드한 문서의 감사 기록. 메모리가 아닌 DB에 영구 보관
CREATE TABLE IF NOT EXISTS audit.upload_log (
    upload_log_id  BIGINT      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    user_id        TEXT        NOT NULL,
    job_id         TEXT        NOT NULL UNIQUE,
    doc_ids        TEXT[]      NOT NULL DEFAULT '{}',
    doc_count      INT         NOT NULL,
    data_file      TEXT        NOT NULL,
    status         TEXT        NOT NULL DEFAULT 'processing',
    finished_at    TIMESTAMPTZ,
    error_summary  TEXT,
    CONSTRAINT upload_log_status_chk CHECK (status IN ('processing', 'done', 'failed')),
    CONSTRAINT upload_log_doc_count_chk CHECK (doc_count >= 0)
);
ALTER TABLE audit.upload_log OWNER TO rag_user;
CREATE INDEX IF NOT EXISTS upload_log_user_created ON audit.upload_log (user_id, created_at);

COMMENT ON TABLE audit.upload_log IS '문서 업로드 감사 로그';
COMMENT ON COLUMN audit.upload_log.upload_log_id IS '행 번호';
COMMENT ON COLUMN audit.upload_log.created_at IS '업로드 시작 시각';
COMMENT ON COLUMN audit.upload_log.user_id IS '업로더 사번';
COMMENT ON COLUMN audit.upload_log.job_id IS '업로드 작업 ID';
COMMENT ON COLUMN audit.upload_log.doc_ids IS '업로드한 문서 ID 목록';
COMMENT ON COLUMN audit.upload_log.doc_count IS '문서 수';
COMMENT ON COLUMN audit.upload_log.data_file IS '저장된 JSONL 파일명';
COMMENT ON COLUMN audit.upload_log.status IS '처리 상태: processing → done / failed';
COMMENT ON COLUMN audit.upload_log.finished_at IS '완료 시각';
COMMENT ON COLUMN audit.upload_log.error_summary IS '오류 요약';

-- ── 인물 병합 이력 ──
CREATE TABLE IF NOT EXISTS audit.person_merge_log (
    merge_id    BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    reviewer        TEXT NOT NULL,
    target_reg_id   TEXT NOT NULL,
    target_person_id TEXT NOT NULL,
    source_person_ids TEXT[] NOT NULL,
    note            TEXT,
    CONSTRAINT person_merge_log_target_reg_fmt CHECK (target_reg_id LIKE 'reg\_%')
);
ALTER TABLE audit.person_merge_log OWNER TO rag_user;
COMMENT ON TABLE  audit.person_merge_log IS '인물 병합 감사 로그';
COMMENT ON COLUMN audit.person_merge_log.merge_id IS '행 번호';
COMMENT ON COLUMN audit.person_merge_log.reviewer IS '병합 수행자 ID';
COMMENT ON COLUMN audit.person_merge_log.target_reg_id IS '병합 대상 확정 인물 reg_id';
COMMENT ON COLUMN audit.person_merge_log.target_person_id IS '병합 대상 person_id';
COMMENT ON COLUMN audit.person_merge_log.source_person_ids IS '병합된 출처 person_id 목록';
COMMENT ON COLUMN audit.person_merge_log.note IS '메모';

-- ── 인물 분리 이력 ──
CREATE TABLE IF NOT EXISTS audit.person_split_log (
    split_id    BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    reviewer          TEXT NOT NULL,
    source_reg_id     TEXT NOT NULL,
    detach_person_ids TEXT[] NOT NULL,
    note              TEXT
);
ALTER TABLE audit.person_split_log OWNER TO rag_user;
COMMENT ON TABLE  audit.person_split_log IS '인물 분리 감사 로그';
COMMENT ON COLUMN audit.person_split_log.split_id IS '행 번호';
COMMENT ON COLUMN audit.person_split_log.reviewer IS '분리 수행자 ID';
COMMENT ON COLUMN audit.person_split_log.source_reg_id IS '분리 전 소속 reg_id';
COMMENT ON COLUMN audit.person_split_log.detach_person_ids IS '분리된 person_id 목록';
COMMENT ON COLUMN audit.person_split_log.note IS '메모';

-- ── 챗봇 피드백 ──
CREATE TABLE IF NOT EXISTS audit.feedback (
    feedback_id  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    request_id   TEXT NOT NULL,
    user_id      TEXT NOT NULL,
    kind         TEXT NOT NULL,
    comment      TEXT,
    CONSTRAINT feedback_kind_chk CHECK (kind IN ('good', 'bad'))
);
ALTER TABLE audit.feedback OWNER TO rag_user;
CREATE INDEX IF NOT EXISTS feedback_request_id ON audit.feedback (request_id);
COMMENT ON TABLE  audit.feedback IS '챗봇 답변 피드백';
COMMENT ON COLUMN audit.feedback.request_id IS '질의 로그 request_id';
COMMENT ON COLUMN audit.feedback.user_id IS '피드백 제출자';
COMMENT ON COLUMN audit.feedback.kind IS 'good(도움 됨) / bad(틀린 답 신고)';
COMMENT ON COLUMN audit.feedback.comment IS '추가 의견 (선택)';

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
