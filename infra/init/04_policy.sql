-- 종합: 권한 정책 (policy_state · doc_type_level · teams · team_level · users)
-- 새 DB에서는 03_person_dict.sql 다음에 자동 실행된다.
-- 이미 있는 DB에는 README의 '메타데이터 스키마 적용'과 같은 방식으로 한 번 실행한다. 여러 번 실행해도 안전하다.
-- 정책을 고치는 트랜잭션은 먼저  SET LOCAL app.user_id = '<사번>';  을 실행한다. 없으면 트리거가 거부한다.

CREATE SCHEMA IF NOT EXISTS policy AUTHORIZATION rag_user;

-- updated_at 자동 갱신
CREATE OR REPLACE FUNCTION policy.tg_set_updated_at()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    NEW.updated_at := now();
    RETURN NEW;
END;
$$;

-- updated_at + updated_by(app.user_id) 자동 기록
CREATE OR REPLACE FUNCTION policy.tg_set_updated_by()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    v_user text := nullif(current_setting('app.user_id', true), '');
BEGIN
    IF v_user IS NULL THEN
        RAISE EXCEPTION 'app.user_id가 설정되지 않았습니다 (SET LOCAL app.user_id = ''사번'')'
            USING ERRCODE = '28000';
    END IF;
    NEW.updated_at := now();
    NEW.updated_by := v_user;
    RETURN NEW;
END;
$$;
ALTER FUNCTION policy.tg_set_updated_at() OWNER TO rag_user;
ALTER FUNCTION policy.tg_set_updated_by() OWNER TO rag_user;

-- 정책 버전. 행은 항상 1개 (policy_state_id = 1)
CREATE TABLE IF NOT EXISTS policy.policy_state (
    policy_state_id  SMALLINT    PRIMARY KEY DEFAULT 1,
    version          INT         NOT NULL DEFAULT 0,
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_by       TEXT,
    changed_txid     BIGINT,                                  -- 버전을 올린 트랜잭션 (txid_current)
    CONSTRAINT policy_state_single_row CHECK (policy_state_id = 1),
    CONSTRAINT policy_state_version_chk CHECK (version >= 0)
);
ALTER TABLE policy.policy_state OWNER TO rag_user;

INSERT INTO policy.policy_state (policy_state_id, version)
VALUES (1, 0)
ON CONFLICT (policy_state_id) DO NOTHING;

-- 문서 유형별 최소 열람 레벨. access_level >= min_level 이면 열람
CREATE TABLE IF NOT EXISTS policy.doc_type_level (
    doc_type    TEXT        PRIMARY KEY,
    min_level   SMALLINT    NOT NULL,                       -- 0~2
    category    TEXT        NOT NULL,                       -- 직원개인 / 고객업무 / 내부업무 / 공개
    rationale   TEXT        NOT NULL,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),         -- 트리거가 채움
    updated_by  TEXT        NOT NULL,                       -- 트리거가 채움
    CONSTRAINT doc_type_level_doc_type_trim CHECK (doc_type = btrim(doc_type) AND doc_type <> ''),
    CONSTRAINT doc_type_level_min_level_chk CHECK (min_level BETWEEN 0 AND 2),
    CONSTRAINT doc_type_level_category_chk  CHECK (category IN ('직원개인', '고객업무', '내부업무', '공개'))
);
ALTER TABLE policy.doc_type_level OWNER TO rag_user;

CREATE OR REPLACE TRIGGER doc_type_level_updated_by
    BEFORE INSERT OR UPDATE ON policy.doc_type_level
    FOR EACH ROW EXECUTE FUNCTION policy.tg_set_updated_by();

-- 부서(팀) 마스터
CREATE TABLE IF NOT EXISTS policy.teams (
    team_cd     TEXT        PRIMARY KEY,
    team_name   TEXT        NOT NULL,
    is_active   BOOLEAN     NOT NULL DEFAULT true,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
ALTER TABLE policy.teams OWNER TO rag_user;

CREATE OR REPLACE TRIGGER teams_updated_at
    BEFORE UPDATE ON policy.teams
    FOR EACH ROW EXECUTE FUNCTION policy.tg_set_updated_at();

-- 사용자 레벨 규칙. team_cd·position이 NULL이면 '전체'
CREATE TABLE IF NOT EXISTS policy.team_level (
    team_level_id  BIGINT      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    team_cd        TEXT        REFERENCES policy.teams (team_cd),
    position       TEXT,
    level          SMALLINT    NOT NULL,                    -- 0~2
    rationale      TEXT        NOT NULL,
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),      -- 트리거가 채움
    updated_by     TEXT        NOT NULL,                    -- 트리거가 채움
    CONSTRAINT team_level_level_chk   CHECK (level BETWEEN 0 AND 2),
    CONSTRAINT team_level_position_trim CHECK (position IS NULL OR (position = btrim(position) AND position <> ''))
);
ALTER TABLE policy.team_level OWNER TO rag_user;

CREATE OR REPLACE TRIGGER team_level_updated_by
    BEFORE INSERT OR UPDATE ON policy.team_level
    FOR EACH ROW EXECUTE FUNCTION policy.tg_set_updated_by();

-- 사용자 계정. 비밀번호는 bcrypt 해시만 허용한다 (평문은 DB가 거부)
CREATE TABLE IF NOT EXISTS policy.users (
    user_id        TEXT        PRIMARY KEY,                 -- 사번 = 로그인 ID
    password_hash  TEXT        NOT NULL,
    user_name      TEXT        NOT NULL,
    team_cd        TEXT        NOT NULL REFERENCES policy.teams (team_cd),
    position       TEXT,                                    -- 직책 (team_level.position과 같은 표기)
    grade          TEXT,                                    -- 직급. 표시용
    access_level   SMALLINT    NOT NULL,                    -- 0~2. 권한 판단의 유일한 기준값
    is_admin       BOOLEAN     NOT NULL DEFAULT false,
    is_active      BOOLEAN     NOT NULL DEFAULT true,
    last_login_at  TIMESTAMPTZ,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),      -- 트리거가 채움
    CONSTRAINT users_user_id_trim   CHECK (user_id = btrim(user_id) AND user_id <> ''),
    CONSTRAINT users_user_name_trim CHECK (user_name = btrim(user_name) AND user_name <> ''),
    CONSTRAINT users_position_trim  CHECK (position IS NULL OR (position = btrim(position) AND position <> '')),
    CONSTRAINT users_grade_trim     CHECK (grade IS NULL OR (grade = btrim(grade) AND grade <> '')),
    CONSTRAINT users_password_bcrypt CHECK (password_hash ~ '^\$2[aby]\$[0-9]{2}\$[./A-Za-z0-9]{53}$'),
    CONSTRAINT users_access_level_chk CHECK (access_level BETWEEN 0 AND 2)
);
ALTER TABLE policy.users OWNER TO rag_user;
CREATE INDEX IF NOT EXISTS users_team_cd ON policy.users (team_cd);

CREATE OR REPLACE TRIGGER users_updated_at
    BEFORE UPDATE ON policy.users
    FOR EACH ROW EXECUTE FUNCTION policy.tg_set_updated_at();

-- 설명 (테이블 정의서 기준)
COMMENT ON TABLE policy.policy_state IS '정책 버전 (상태 테이블, 한 행)';
COMMENT ON COLUMN policy.policy_state.policy_state_id IS '상태 행 번호';
COMMENT ON COLUMN policy.policy_state.version IS '정책 버전';
COMMENT ON COLUMN policy.policy_state.updated_at IS '마지막 편집 시각';
COMMENT ON COLUMN policy.policy_state.updated_by IS '마지막 편집자';
COMMENT ON COLUMN policy.policy_state.changed_txid IS '버전을 올린 트랜잭션 번호';
COMMENT ON TABLE policy.doc_type_level IS 'doc_type별 최소 레벨 (권한 마스터)';
COMMENT ON COLUMN policy.doc_type_level.doc_type IS '문서 유형';
COMMENT ON COLUMN policy.doc_type_level.min_level IS '이 유형을 볼 수 있는 최소 레벨';
COMMENT ON COLUMN policy.doc_type_level.category IS '문서 분류';
COMMENT ON COLUMN policy.doc_type_level.rationale IS '판정 근거';
COMMENT ON COLUMN policy.doc_type_level.updated_at IS '편집 시각';
COMMENT ON COLUMN policy.doc_type_level.updated_by IS '편집자';
COMMENT ON TABLE policy.teams IS '부서(팀) 마스터';
COMMENT ON COLUMN policy.teams.team_cd IS '부서 코드';
COMMENT ON COLUMN policy.teams.team_name IS '부서명';
COMMENT ON COLUMN policy.teams.is_active IS '활성 여부';
COMMENT ON COLUMN policy.teams.created_at IS '생성 시각';
COMMENT ON COLUMN policy.teams.updated_at IS '수정 시각';
COMMENT ON TABLE policy.team_level IS '사용자 레벨 규칙 (팀·직책)';
COMMENT ON COLUMN policy.team_level.team_level_id IS '규칙 번호';
COMMENT ON COLUMN policy.team_level.team_cd IS '팀 코드';
COMMENT ON COLUMN policy.team_level.position IS '직책';
COMMENT ON COLUMN policy.team_level.level IS '조건에 맞는 계정의 기본 레벨';
COMMENT ON COLUMN policy.team_level.rationale IS '근거';
COMMENT ON COLUMN policy.team_level.updated_at IS '편집 시각';
COMMENT ON COLUMN policy.team_level.updated_by IS '편집자';
COMMENT ON TABLE policy.users IS '사용자 계정';
COMMENT ON COLUMN policy.users.user_id IS '사번 = 로그인 ID';
COMMENT ON COLUMN policy.users.password_hash IS '비밀번호 해시. 평문은 DB가 거부';
COMMENT ON COLUMN policy.users.user_name IS '이름';
COMMENT ON COLUMN policy.users.team_cd IS '소속 팀 코드';
COMMENT ON COLUMN policy.users.position IS '직책(팀장·팀원)';
COMMENT ON COLUMN policy.users.grade IS '직급(사원·과장 등). 표시용, 레벨 판정에 쓰지 않음';
COMMENT ON COLUMN policy.users.access_level IS '권한 레벨. 권한 판단의 유일한 기준값';
COMMENT ON COLUMN policy.users.is_admin IS '관리자 화면 접근 여부. 레벨과 별개';
COMMENT ON COLUMN policy.users.is_active IS '활성 여부';
COMMENT ON COLUMN policy.users.last_login_at IS '마지막 로그인 시각';
COMMENT ON COLUMN policy.users.created_at IS '생성 시각';
COMMENT ON COLUMN policy.users.updated_at IS '수정 시각';
