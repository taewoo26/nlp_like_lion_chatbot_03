#!/usr/bin/env bash
# 종합: 권한별 DB 유저 분리 (read / search / admin + 적재)
# 새 DB에서는 05_audit.sql 다음에 자동 실행된다.
# 이미 있는 DB에는 한 번 실행한다. 여러 번 실행해도 안전하다.
#   docker compose exec -T <db 서비스> bash /docker-entrypoint-initdb.d/06_grants.sh
#
# 표의 소유자는 지금처럼 rag_user로 둔다. 기존 코드는 그대로 동작한다.
# 아래 계정은 앱이 접속 계정을 바꿀 때부터 효과가 있다.
#   app_ingest  적재 파이프라인   : rag·vault 쓰기 (토큰 삭제 불가)
#   app_reset   전체 재적재      : 적재 권한 + 명시된 5개 테이블만 TRUNCATE
#   app_search  챗봇 검색 서비스  : rag·정책 읽기, query_log 추가, 복원은 vault.restore_token()으로만
# 관리자(admin)는 슈퍼유저를 그대로 쓴다. 감사 로그(audit.*)는 어느 계정에도 UPDATE·DELETE를 주지 않는다.
#
# 비밀번호는 compose의 env_file(.env)에서 받는다. 값이 없는 계정은 로그인 없이 만든다.
#   APP_INGEST_PASSWORD, APP_RESET_PASSWORD, APP_SEARCH_PASSWORD
set -eo pipefail   # -u는 쓰지 않음: 실행 권한 없이 source될 때 엔트리포인트까지 영향을 줌

psql -v ON_ERROR_STOP=1 --quiet \
     --username "$POSTGRES_USER" \
     --dbname "$POSTGRES_DB" \
     -v pw_ingest="${APP_INGEST_PASSWORD:-}" \
     -v pw_search="${APP_SEARCH_PASSWORD:-}" \
     -v pw_reset="${APP_RESET_PASSWORD:-}" <<'EOSQL'

-- 비밀번호를 세션 설정으로 넘겨 DO 블록에서 쓴다 (트랜잭션이 끝나면 사라짐)
BEGIN;
SELECT set_config('init.pw_ingest', :'pw_ingest', true),
       set_config('init.pw_reset',  :'pw_reset', true),
       set_config('init.pw_search', :'pw_search', true) \gset

-- 1. 역할: 권한 묶음(pii_*, 로그인 불가) + 로그인 계정(app_*)
DO $$
DECLARE
    r  record;
    pw text;
BEGIN
    FOR r IN SELECT * FROM (VALUES
        ('pii_ingest', NULL,          NULL),
        ('pii_reset',  NULL,          NULL),
        ('pii_search', NULL,          NULL),
        ('app_ingest', 'pii_ingest',  'init.pw_ingest'),
        ('app_reset',  'pii_reset',   'init.pw_reset'),
        ('app_search', 'pii_search',  'init.pw_search')
    ) AS t(name, grp, pw_key)
    LOOP
        IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = r.name) THEN
            EXECUTE format('CREATE ROLE %I NOLOGIN', r.name);
        END IF;

        IF r.grp IS NOT NULL THEN
            EXECUTE format('GRANT %I TO %I', r.grp, r.name);
            pw := nullif(current_setting(r.pw_key, true), '');
            IF pw IS NULL THEN
                EXECUTE format('ALTER ROLE %I NOLOGIN PASSWORD NULL', r.name);
                RAISE NOTICE '% : 비밀번호 환경변수가 없어 로그인 없이 만들었습니다', r.name;
            ELSE
                EXECUTE format('ALTER ROLE %I LOGIN PASSWORD %L', r.name, pw);
            END IF;
        END IF;
    END LOOP;

    EXECUTE format('GRANT CONNECT ON DATABASE %I TO pii_ingest, pii_reset, pii_search',
                   current_database());
END
$$;

-- 2. 기본 권한 회수 (rag_user는 소유자라 영향 없음)
REVOKE ALL ON SCHEMA rag, vault, policy, audit FROM PUBLIC;
REVOKE ALL ON ALL TABLES    IN SCHEMA rag, vault, policy, audit FROM PUBLIC;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA rag, vault, policy, audit FROM PUBLIC;
ALTER DEFAULT PRIVILEGES FOR ROLE rag_user IN SCHEMA rag, vault, policy, audit
    REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC;

-- vault_user: 브릿지 함수(restore_token)용 최소 권한
GRANT USAGE ON SCHEMA rag, policy, audit TO vault_user;
GRANT SELECT ON policy.users TO vault_user;
GRANT INSERT ON audit.restore_log TO vault_user;
GRANT SELECT ON audit.query_log, rag.chunks TO vault_user;   -- partial_token: 본인 질문·답변에 나온 토큰 확인

-- 3. 적재: 탐지 → 가명처리 → 적재 → 인물사전
GRANT USAGE ON SCHEMA rag, vault TO pii_ingest;
GRANT SELECT, INSERT, UPDATE, DELETE ON rag.chunks, rag.look_up TO pii_ingest;
GRANT SELECT                         ON rag.role_master        TO pii_ingest;
GRANT SELECT, INSERT                 ON vault.token_map         TO pii_ingest;   -- 토큰은 지우지 않음
GRANT UPDATE (originals)             ON vault.token_map         TO pii_ingest;   -- 새 표기 추가만
GRANT SELECT, INSERT, UPDATE, DELETE ON vault.person_info, vault.person_registry TO pii_ingest;

-- 전체 초기화 전용. 재적재도 같은 트랜잭션에서 수행하기 위해 적재 권한을 상속한다.
-- 일반 적재·검색 계정은 이 역할을 상속하지 않는다. 감사·정책 표는 초기화 대상이 아니다.
GRANT pii_ingest TO pii_reset;
GRANT TRUNCATE ON rag.chunks, rag.look_up, vault.token_map,
                  vault.person_info, vault.person_registry TO pii_reset;

-- 4. 검색 서비스: vault 표는 직접 못 읽음
GRANT USAGE ON SCHEMA rag, policy, audit, vault TO pii_search;
GRANT SELECT ON rag.chunks, rag.look_up, rag.role_master TO pii_search;
GRANT SELECT ON policy.doc_type_level, policy.team_level, policy.teams, policy.policy_state TO pii_search;
GRANT SELECT                 ON policy.users TO pii_search;                     -- 로그인 확인
GRANT UPDATE (last_login_at) ON policy.users TO pii_search;
GRANT INSERT ON audit.query_log TO pii_search;
GRANT EXECUTE ON FUNCTION vault.restore_token(text, text, text) TO pii_search;
GRANT EXECUTE ON FUNCTION vault.partial_token(text, text, text) TO pii_search;   -- Lv1 이름 부분 공개

-- 5. 관리자(admin)는 슈퍼유저이므로 별도 GRANT 불필요

COMMIT;
EOSQL
