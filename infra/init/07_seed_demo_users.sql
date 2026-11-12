-- [시연용 로그인 계정] 팀 10개, 계정 10개. 여러 번 실행해도 결과가 같다.
-- 새 DB에서는 06_grants.sh 다음에 자동 실행된다.
-- 이미 있는 DB에는 README의 '메타데이터 스키마 적용'과 같은 방식으로 한 번 실행한다.
-- 선행 조건: 04_policy.sql (policy.teams·users 테이블)
-- 로그인: 사번 SB260001~SB260010 / 비밀번호 qwer0001~qwer0010 (각 줄 주석). DB에는 bcrypt 해시만 저장
-- 계정은 dev/test/holdout 문서에 직원으로 나오는 가상 인물(정답 파일 미사용). '임의' = 문서에 없어 임의로 채운 값
-- SB260010 은 비활성 계정(로그인 거부 확인용)
-- 운영 DB에는 넣지 않는다. 시연·개발용이다.

BEGIN;
SET LOCAL app.user_id = 'seed';   -- 변경 이력 트리거가 있으면 필요, 없으면 영향 없음

INSERT INTO policy.teams (team_cd, team_name)
SELECT * FROM (VALUES
    ('DEPT_HR',         '인사팀'),
    ('DEPT_INFOSEC',    '정보보호팀'),
    ('DEPT_CUST_PROT',  '고객보호팀'),
    ('DEPT_COLLECTION', '채권관리팀'),
    ('DEPT_CARD_OPS',   '카드운영팀'),
    ('DEPT_CREDIT',     '여신심사팀'),
    ('DEPT_RISK',       '리스크관리팀'),
    ('DEPT_CS_CENTER',  '고객센터'),
    ('DEPT_IT_OPS',     'IT운영팀'),
    ('DEPT_DIGITAL',    '디지털전략팀')
) AS v (team_cd, team_name)
WHERE NOT EXISTS (SELECT 1 FROM policy.teams t WHERE t.team_cd = v.team_cd);

INSERT INTO policy.users (user_id, password_hash, user_name, team_cd, position, grade, access_level, is_admin, is_active)
SELECT * FROM (VALUES
    ('SB260001', '$2b$12$PyuVnaZZvI6DLGX8IchnkuTaWg0xKRPUbVfzaN7z0pUuUlz7lUvRK', '전경희', 'DEPT_HR',         '팀장', '부장', 2, false, true),   -- qwer0001 / 직급 임의
    ('SB260002', '$2b$12$o607u7awtJaYFjVeD6B9auL2JQCiBLp7wWzg1ICCXS.a9ShRzFdbq', '서혜진', 'DEPT_INFOSEC',    '팀장', '차장', 2, true,  true),   -- qwer0002 / 직급 임의, 관리자
    ('SB260003', '$2b$12$NyP8EFm1koMvcmAEKVevVujD8nMryWm/66K7gUjvweUkSHBWKRICm', '최지영', 'DEPT_CUST_PROT',  '팀장', '차장', 1, false, true),   -- qwer0003 / 직급 임의
    ('SB260004', '$2b$12$oZSzo6usd3zG1dx07SBYFuYqLoy6WDEkJVQSE5u8Aa93N6LcTyhAa', '이상훈', 'DEPT_COLLECTION', '팀원', '과장', 1, false, true),   -- qwer0004 / 직책·직급 임의
    ('SB260005', '$2b$12$7sGlqMUxhMP5Meut7AZwtOHuvg9Zyy29eD4/NbK0V8N6OdXw4hMOG', '김주원', 'DEPT_CARD_OPS',   '팀원', '대리', 1, false, true),   -- qwer0005
    ('SB260006', '$2b$12$tQFRw2ISB1OkwEy88av.r.Z.GIgdp8ufV36f3o1al3sCR9GmjnoAC', '김은영', 'DEPT_CREDIT',     '팀원', '대리', 1, false, true),   -- qwer0006
    ('SB260007', '$2b$12$HgaZ5WPXr5C.a7lgoRcQ/eInVQpM/m6X7dgX/2VIco1UUVWain/OW', '허수아', 'DEPT_RISK',       '팀원', '부장', 1, false, true),   -- qwer0007 / 직책 임의
    ('SB260008', '$2b$12$ksJd4CRVnxaRDnYSKf8UceIY6BIm9OknZtYh5OE2oRwb6V4N67rca', '김서준', 'DEPT_CS_CENTER',  NULL,   '대리', 1, false, true),   -- qwer0008 / 직책 비움
    ('SB260009', '$2b$12$VlmH/vE5abKFqmyPii41T./tkHmOelIhaUofQK8GZYfwNx9gv0Vza', '박도윤', 'DEPT_IT_OPS',     '팀원', '대리', 0, false, true),   -- qwer0009
    ('SB260010', '$2b$12$gK6AQuO0C8J0kRolA2vK6u2dDsBh6.3GDfvtpwf2mJqN67Cym2nRO', '김미영', 'DEPT_DIGITAL',    '팀원', '사원', 0, false, false)   -- qwer0010 / 비활성
) AS v (user_id, password_hash, user_name, team_cd, position, grade, access_level, is_admin, is_active)
WHERE NOT EXISTS (SELECT 1 FROM policy.users u WHERE u.user_id = v.user_id);

COMMIT;

-- 확인 (init 로그에 출력된다)
SELECT t.team_name, u.user_id, u.user_name, u.position, u.grade, u.access_level, u.is_admin, u.is_active
  FROM policy.users u
  JOIN policy.teams t USING (team_cd)
 ORDER BY u.user_id;
