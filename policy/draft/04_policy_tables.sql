-- [초안 · 팀 확정 전] 권한 정책(policy) 스키마: 관리자 화면에서 편집하는 정책 테이블
-- 확정 전까지 policy/draft/ 에 두며 자동 실행되지 않는다. 확정 후 infra/init/ 으로 옮긴다.
-- 실행 순서: 01_schema.sh → 02_metadata.sql → 03_audit_log.sql → 04_policy_tables.sql → 05_policy_seed.sql
-- 수동 적용(관리자 계정, 여러 번 실행해도 결과가 같다):
--   docker compose -p pii_starter --env-file .env -f infra/compose.yaml exec -T db \
--     psql -U admin -d pii_rag -v ON_ERROR_STOP=1 < policy/draft/04_policy_tables.sql
--
-- 설계 결정 (docs/permission_policy_draft.md 9절 결정 3)
--   - 정책의 원본은 이 DB 테이블이다. policy/access_policy.yaml 은 처음 한 번 넣는 초기값(시드)이다.
--   - 관리자 화면에서 편집하는 것: doc_type별 최소 레벨(doc_type_access), 팀별 기본 레벨(team_level)
--     사용자별 레벨은 로그인 계정 테이블에서 편집한다(이 파일 범위 밖).
--   - 편집하지 못하는 것(코드·시드에 고정): 레벨 개수(0/1/2), 미등록 doc_type은 Lv2 전용,
--     실명 복원 규칙(Lv2만 전체 복원). 잘못 바꾸면 개인정보가 바로 노출되는 항목이라 DB에 편집 대상으로 두지 않는다.
--   - doc_type 최소 레벨은 분류 기본 레벨보다 낮출 수 없다(올리기만 가능, CHECK doc_type_access_level_floor).
--   - 관리자 화면 접근은 레벨과 별개인 관리자 표시(로그인 계정 테이블의 is_admin)로 정한다(정책서 9절).
--   - 편집할 때마다 트리거가 (1) 정책 버전을 올리고 (2) 변경 이력(audit.policy_change_log)을 남긴다.
--     편집자(app.actor)를 알려 주지 않으면 편집이 실패한다. 이력 없이 정책을 바꿀 수 없게 하기 위함.
--
-- 편집 방법 (관리자 화면 API)
--   BEGIN;
--   SET LOCAL app.actor = 'admin01';                 -- 로그인한 관리자 ID. 없으면 트리거가 거부
--   UPDATE policy.doc_type_access SET min_level = 2, rationale = '...' WHERE doc_type = '회의록';
--   COMMIT;                                          -- 한 트랜잭션(= 저장 1회)에 버전은 1만 오른다
--
-- 계정별 권한 설계 (이 파일에서는 만들지 않는다. 적용은 WBS 3.4.1 구현 단계, docs/permission_schema.md 6절)
--   rag_user            : policy_state, doc_type_access 읽기 (검색 전 권한 필터 계산용)
--   관리자 화면 계정(신규): 정책 테이블 읽기, doc_type_access·team_level 편집, 변경 이력 읽기
--                         정책 상태(policy_state)·변경 이력은 직접 쓰지 못한다(트리거만 씀)
--   vault_user          : 권한 없음 (복원 규칙은 코드에 고정)
--   audit_reader(신규)   : 감사 로그 읽기 전용 (audit 스키마 SELECT만). 이력 화면·복원 이력 조회·평가 스크립트용
--
-- 앱이 정책을 읽는 방법
--   SELECT version FROM policy.policy_state;          -- 캐시한 버전과 다르면 아래를 다시 읽는다
--   SELECT doc_type, min_level FROM policy.doc_type_access;
--   감사 로그의 policy_version 에는 이 version 값을 문자열로 기록한다.

BEGIN;

CREATE SCHEMA IF NOT EXISTS policy;

-- 테이블 분류: doc_type_access·team_level = 마스터(관리자 편집), policy_state = 상태(버전 1행),
--             audit.policy_change_log = 이력. 레벨(0/1/2) 정의는 편집하지 않으므로 표로 두지 않고
--             CHECK 제약과 코드(policy/access_policy.yaml의 levels)에 고정한다.

-- ---------------------------------------------------------------- [상태] 정책 버전 (한 행)
CREATE TABLE IF NOT EXISTS policy.policy_state (
    id            BOOLEAN PRIMARY KEY DEFAULT true CHECK (id),   -- 항상 한 행
    version       INT NOT NULL DEFAULT 0 CHECK (version >= 0),   -- 편집(트랜잭션)마다 1씩 오름
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_by    TEXT,
    changed_txid  BIGINT                                          -- 같은 트랜잭션에서 버전을 두 번 올리지 않기 위함
);
INSERT INTO policy.policy_state (id) VALUES (true) ON CONFLICT (id) DO NOTHING;

-- ---------------------------------------------------------------- [마스터] doc_type별 최소 레벨 (편집 가능)
-- 행을 지우면 그 doc_type은 '미등록'이 되어 Lv2 전용으로 바뀐다(안전한 쪽).
-- category: 정책서 3절 판정 질문의 결과(어떤 종류의 문서인가). 값만 봐도 뜻이 보이도록 이름으로 저장한다.
--   직원개인(질문 2, Lv2) / 고객업무(질문 3, Lv1) / 내부업무(질문 4, Lv1) / 공개(질문 5, Lv0)
--   질문 1(미등록)은 행이 없는 경우라서 값이 없다.
CREATE TABLE IF NOT EXISTS policy.doc_type_access (
    doc_type    TEXT PRIMARY KEY CHECK (doc_type = btrim(doc_type) AND doc_type <> ''),
    min_level   SMALLINT NOT NULL CHECK (min_level BETWEEN 0 AND 2),
    category    TEXT NOT NULL CHECK (category IN ('직원개인', '고객업무', '내부업무', '공개')),
    rationale   TEXT NOT NULL,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_by  TEXT NOT NULL,
    -- 안전장치: 분류의 기본 레벨이 하한이다. 올리기만 할 수 있고 내릴 수는 없다.
    --   직원개인 ≥ 2(= 항상 Lv2) / 고객업무·내부업무 ≥ 1 / 공개 ≥ 0
    --   (기본 레벨 값은 policy/access_policy.py 의 CATEGORY_BASE_LEVEL 과 같아야 한다)
    CONSTRAINT doc_type_access_level_floor CHECK (
        min_level >= CASE category WHEN '직원개인' THEN 2
                                   WHEN '고객업무' THEN 1
                                   WHEN '내부업무' THEN 1
                                   ELSE 0 END)
);

-- ---------------------------------------------------------------- [마스터] 팀별 기본 레벨 (편집 가능)
-- 계정을 만들 때 기본 레벨을 채우는 데만 쓴다. 검색은 계정의 level만 보므로
-- 이 표를 고쳐도 기존 사용자의 레벨은 바뀌지 않는다. 표에 없는 팀은 Lv0.
CREATE TABLE IF NOT EXISTS policy.team_level (
    team           TEXT PRIMARY KEY CHECK (team = btrim(team) AND team <> ''),
    default_level  SMALLINT NOT NULL CHECK (default_level BETWEEN 0 AND 2),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_by     TEXT NOT NULL
);

-- ---------------------------------------------------------------- [이력] 정책 변경 이력
-- 안전성 확보조치 기준 제5조(접근 권한 변경 기록) 참고. 트리거만 쓰고, 앱 계정은 쓰지 못한다.
CREATE TABLE IF NOT EXISTS audit.policy_change_log (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ts              TIMESTAMPTZ NOT NULL DEFAULT now(),
    actor           TEXT NOT NULL,                   -- 편집한 관리자 ID
    table_name      TEXT NOT NULL,                   -- 예: policy.doc_type_access
    row_key         TEXT NOT NULL,                   -- 예: 회의록, 인사팀
    action          TEXT NOT NULL CHECK (action IN ('insert', 'update', 'delete')),
    old_value       JSONB,                           -- 바뀌기 전 행 (insert면 NULL)
    new_value       JSONB,                           -- 바뀐 뒤 행 (delete면 NULL)
    policy_version  INT                              -- 변경 후 정책 버전 (버전을 올리지 않는 표면 NULL)
);
CREATE INDEX IF NOT EXISTS policy_change_log_ts ON audit.policy_change_log (ts);

-- ---------------------------------------------------------------- 변경 기록 트리거
-- 인자: (1) 행 키 컬럼 이름 (2) 'bump'면 정책 버전을 올림
-- SECURITY DEFINER: 이 함수를 만든 관리자 권한으로 실행되어, 편집 계정에 이력·상태 테이블 쓰기 권한을 주지 않아도 된다.
-- 로그인 계정 테이블의 레벨 변경 기록에도 같은 함수를 붙일 수 있다(예: 키 'user_id', 버전은 올리지 않음).
CREATE OR REPLACE FUNCTION policy.record_change() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
DECLARE
    v_actor TEXT := nullif(current_setting('app.actor', true), '');
    v_key   TEXT := TG_ARGV[0];
    v_bump  BOOLEAN := coalesce(TG_ARGV[1], '') = 'bump';
    v_ver   INT;
    v_row   JSONB;
BEGIN
    IF v_actor IS NULL THEN
        RAISE EXCEPTION '정책 변경에는 편집자 정보가 필요합니다: SET LOCAL app.actor = ''<관리자 ID>'''
            USING ERRCODE = 'insufficient_privilege';
    END IF;

    IF TG_OP <> 'DELETE' THEN
        -- 편집 시각·편집자는 앱이 넘긴 값이 아니라 여기서 채운다
        NEW := jsonb_populate_record(NEW, jsonb_build_object('updated_at', now(), 'updated_by', v_actor));
    END IF;

    IF v_bump THEN
        UPDATE policy.policy_state
           SET version = version + 1, changed_txid = txid_current()
         WHERE changed_txid IS DISTINCT FROM txid_current();
        UPDATE policy.policy_state SET updated_at = now(), updated_by = v_actor;
        SELECT version INTO v_ver FROM policy.policy_state;
    END IF;

    v_row := to_jsonb(CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END);
    INSERT INTO audit.policy_change_log (actor, table_name, row_key, action, old_value, new_value, policy_version)
    VALUES (v_actor, TG_TABLE_SCHEMA || '.' || TG_TABLE_NAME, v_row ->> v_key, lower(TG_OP),
            CASE WHEN TG_OP <> 'INSERT' THEN to_jsonb(OLD) END,
            CASE WHEN TG_OP <> 'DELETE' THEN to_jsonb(NEW) END,
            v_ver);

    RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
END $$;
REVOKE ALL ON FUNCTION policy.record_change() FROM PUBLIC;

DROP TRIGGER IF EXISTS doc_type_access_change ON policy.doc_type_access;
CREATE TRIGGER doc_type_access_change
    BEFORE INSERT OR UPDATE OR DELETE ON policy.doc_type_access
    FOR EACH ROW EXECUTE FUNCTION policy.record_change('doc_type', 'bump');

DROP TRIGGER IF EXISTS team_level_change ON policy.team_level;
CREATE TRIGGER team_level_change
    BEFORE INSERT OR UPDATE OR DELETE ON policy.team_level
    FOR EACH ROW EXECUTE FUNCTION policy.record_change('team', 'bump');

-- 기존 행의 이름(doc_type)과 분류(category)는 바꿀 수 없다. 분류를 바꿔 직원 개인 문서 보호를 피하는 실수를 막는다.
-- 분류가 잘못 정해졌다면 행을 삭제(미등록 = Lv2 전용)한 뒤 올바른 분류로 다시 추가한다. 삭제·추가는 모두 변경 이력에 남는다.
CREATE OR REPLACE FUNCTION policy.guard_doc_type_access() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.doc_type IS DISTINCT FROM OLD.doc_type OR NEW.category IS DISTINCT FROM OLD.category THEN
        RAISE EXCEPTION 'doc_type과 category는 수정할 수 없습니다. 삭제 후 다시 추가하세요: %', OLD.doc_type
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
END $$;

DROP TRIGGER IF EXISTS doc_type_access_guard ON policy.doc_type_access;
CREATE TRIGGER doc_type_access_guard
    BEFORE UPDATE ON policy.doc_type_access
    FOR EACH ROW EXECUTE FUNCTION policy.guard_doc_type_access();

-- 한 번에 전체를 지우는 TRUNCATE는 트리거를 거치지 않으므로 막는다(권한도 주지 않음)
DROP TRIGGER IF EXISTS doc_type_access_no_truncate ON policy.doc_type_access;
DROP TRIGGER IF EXISTS team_level_no_truncate ON policy.team_level;
CREATE OR REPLACE FUNCTION policy.deny_truncate() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION '정책 테이블은 TRUNCATE할 수 없습니다. 행 단위로 편집하세요.';
END $$;
CREATE TRIGGER doc_type_access_no_truncate BEFORE TRUNCATE ON policy.doc_type_access
    FOR EACH STATEMENT EXECUTE FUNCTION policy.deny_truncate();
CREATE TRIGGER team_level_no_truncate BEFORE TRUNCATE ON policy.team_level
    FOR EACH STATEMENT EXECUTE FUNCTION policy.deny_truncate();

COMMIT;
