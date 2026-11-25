#!/usr/bin/env bash
# 종합: 관리자 화면 전용 DB 계정 app_admin (06_grants.sh 다음에 실행. 새 DB에서는 자동 실행된다)
# 이미 있는 DB에는 한 번 실행한다. 여러 번 실행해도 안전하다 (실행할 때마다 아래 권한 목록대로 다시 맞춘다).
#   docker compose -p pii_starter --env-file .env -f infra/compose.yaml exec -T db bash /docker-entrypoint-initdb.d/08_admin_grants.sh
#
# 04~07 파일은 고치지 않고 이 파일만 추가했다 (2026-11-12 "develop 테이블 스키마는 수정하지 않는다" 결정).
# service/app.py 의 /api/admin/* 가 실제로 쓰는 것만 준다.
#   조회    rag.chunks, vault.token_map(token·label·created_at 열만), vault.person_info(검토 화면 열만),
#           audit.query_log·restore_log·policy_change_log, policy.users·teams
#   수정    vault.person_info.resolution_status (인물 검토), policy.users.access_level·is_active (사용자 관리)
#   못 하는 것  vault.token_map.originals(토큰 원문), person_info.evidence 등 판정 근거, 감사 로그 수정·삭제, 정책 표 수정
#
# 비밀번호는 compose의 env_file(.env)에서 받는다: APP_ADMIN_PASSWORD (없으면 로그인 없이 만든다)
set -eo pipefail

psql -v ON_ERROR_STOP=1 --quiet \
     --username "$POSTGRES_USER" \
     --dbname "$POSTGRES_DB" \
     -v pw_admin="${APP_ADMIN_PASSWORD:-}" <<'EOSQL'
BEGIN;
SELECT set_config('init.pw_admin', :'pw_admin', true) \gset

DO $$
DECLARE
    pw text := nullif(current_setting('init.pw_admin', true), '');
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'app_admin') THEN
        CREATE ROLE app_admin NOLOGIN;
    END IF;
    IF pw IS NULL THEN
        ALTER ROLE app_admin NOLOGIN PASSWORD NULL;
        RAISE NOTICE 'app_admin : 비밀번호 환경변수(APP_ADMIN_PASSWORD)가 없어 로그인 없이 만들었습니다';
    ELSE
        EXECUTE format('ALTER ROLE app_admin LOGIN PASSWORD %L', pw);
    END IF;
    EXECUTE format('GRANT CONNECT ON DATABASE %I TO app_admin', current_database());
END
$$;

-- 예전 06_grants.sh(c1352f1)는 app_admin 을 pii_admin 역할의 구성원으로 만들었다. 이미 만든 DB 에는 그 상속이 남아 있어
-- (예: pii_admin 의 token_map 전체 SELECT) 아래 GRANT 로 제한해도 풀린다. 다른 역할의 구성원 자격을 모두 뺀다.
DO $$
DECLARE
    r record;
BEGIN
    FOR r IN SELECT g.rolname
               FROM pg_auth_members m
               JOIN pg_roles g ON g.oid = m.roleid
              WHERE m.member = (SELECT oid FROM pg_roles WHERE rolname = 'app_admin')
    LOOP
        EXECUTE format('REVOKE %I FROM app_admin', r.rolname);
        RAISE NOTICE 'app_admin : 상속받던 역할 % 의 구성원 자격을 뺐습니다', r.rolname;
    END LOOP;
END
$$;

-- 다시 실행해도 아래 목록과 같아지도록 먼저 회수한다
REVOKE ALL ON SCHEMA rag, vault, policy, audit FROM app_admin;
REVOKE ALL ON ALL TABLES IN SCHEMA rag, vault, policy, audit FROM app_admin;

GRANT USAGE ON SCHEMA rag, vault, policy, audit TO app_admin;

GRANT SELECT ON rag.chunks TO app_admin;                                              -- 문서 목록·상세, 통계
GRANT SELECT (token, label, created_at) ON vault.token_map TO app_admin;              -- 원문(originals)은 주지 않는다
GRANT SELECT (person_id, token, role, doc_id, resolution_status) ON vault.person_info TO app_admin;
GRANT UPDATE (resolution_status)                                  ON vault.person_info TO app_admin;
GRANT SELECT ON audit.query_log, audit.restore_log, audit.policy_change_log TO app_admin;
GRANT SELECT ON policy.users, policy.teams TO app_admin;
GRANT UPDATE (access_level, is_active) ON policy.users TO app_admin;

COMMIT;
EOSQL
