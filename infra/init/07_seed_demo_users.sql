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

-- BEGIN AUTO-GENERATED: policy seed (python -m policy.seed_sql 이 덮어씀. 직접 수정 금지)
-- 자동 생성 파일: 직접 수정하지 마세요. 원본은 policy/access_policy.yaml
-- 생성: python -m policy.seed_sql
-- 초기값 정책 v0.5: doc_type 33종, 팀 10개, 레벨 규칙 10개
-- 표가 비어 있을 때만 넣는다(관리자가 편집한 값을 덮어쓰지 않음). infra/init/04_policy.sql 적용 후 실행.
BEGIN;
SET LOCAL app.user_id = 'seed';

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM policy.doc_type_level) THEN
    INSERT INTO policy.doc_type_level (doc_type, min_level, category, rationale) VALUES
      ('경조사공지', 0, '공개', '전사 공지 성격. 직원 가족 정보가 있어도 Lv0 유지로 결정'),
      ('공지', 0, '공개', '전사 공지, PII 없음'),
      ('민원현황', 0, '공개', 'PII 없는 내부 현황 집계'),
      ('상품안내', 0, '공개', '대외 상품 안내, PII 없음'),
      ('인사발령', 0, '공개', '전사 공지 문서 (11-26: Lv2→0)'),
      ('VOC요약', 1, '고객업무', '고객 의견 요약'),
      ('가맹점변경', 1, '고객업무', '가맹점 요청서'),
      ('고객정보변경', 1, '고객업무', '고객 정보 처리'),
      ('고객편지', 1, '고객업무', '고객 접수 의견'),
      ('권한신청', 1, '내부업무', '시스템 접근 권한 신청, 회사 운영 문서'),
      ('녹취요약', 1, '고객업무', '고객 통화 요약'),
      ('대리인위임확인', 1, '고객업무', '위임 확인서'),
      ('대출상담', 1, '고객업무', '고객 상담 기록'),
      ('민원처리', 1, '고객업무', '고객 민원 처리'),
      ('분쟁조정', 1, '고객업무', '고객 분쟁 조정 검토 의견서'),
      ('업무메일', 1, '내부업무', '직원 간 업무 소통'),
      ('연체안내', 1, '고객업무', '고객 통화 기록'),
      ('영문혼용메일', 1, '내부업무', '대외 협력 업무 메일'),
      ('용역계약서', 1, '내부업무', '회사 용역 계약, 회사 운영 문서. [계획서와 차이: 계획서는 계약서를 Lv2 예시로 둠]'),
      ('우편반송처리', 1, '고객업무', '반송 처리 기록'),
      ('이상거래탐지', 1, '고객업무', '고객 통화 기록'),
      ('자동이체신청서', 1, '고객업무', '고객 신청서'),
      ('채용메일', 1, '내부업무', '인사팀 채용 담당 업무. 담당 팀은 항상 인사팀 (11-26: Lv2→1, 직원개인→내부업무)'),
      ('출장보고', 1, '내부업무', '내부 업무 보고'),
      ('카드발급심사', 1, '고객업무', '고객 카드 발급 심사 메모'),
      ('카드분실상담', 1, '고객업무', '고객 상담 기록'),
      ('피해접수', 1, '고객업무', '보이스피싱 피해 접수'),
      ('한도심사결과', 1, '고객업무', '고객 한도 심사 결과'),
      ('해외송금접수', 1, '고객업무', '송금 접수 기록'),
      ('회의록', 1, '내부업무', '직원 간 업무 회의. [계획서와 차이: 계획서는 Lv2 예시]'),
      ('감사지적', 2, '직원개인', '직원 대상 내부 감사 지적'),
      ('법인카드소명', 2, '직원개인', '직원 개인의 비용 소명'),
      ('사내메신저', 2, '내부업무', '개인 간 대화, 공유 의도 없음 (11-26: Lv1→2)');
  END IF;
  -- 팀을 먼저 넣는다 (team_level.team_cd → teams.team_cd 외래키)
  IF NOT EXISTS (SELECT 1 FROM policy.teams) THEN
    INSERT INTO policy.teams (team_cd, team_name) VALUES
      ('DEPT_HR', '인사팀'),
      ('DEPT_INFOSEC', '정보보호팀'),
      ('DEPT_CUST_PROT', '고객보호팀'),
      ('DEPT_COLLECTION', '채권관리팀'),
      ('DEPT_CARD_OPS', '카드운영팀'),
      ('DEPT_CREDIT', '여신심사팀'),
      ('DEPT_RISK', '리스크관리팀'),
      ('DEPT_CS_CENTER', '고객센터'),
      ('DEPT_IT_OPS', 'IT운영팀'),
      ('DEPT_DIGITAL', '디지털전략팀');
  END IF;
  IF NOT EXISTS (SELECT 1 FROM policy.team_level) THEN
    INSERT INTO policy.team_level (team_cd, position, level, rationale) VALUES
      ('DEPT_HR', NULL, 2, '인사발령·채용 문서 담당'),
      ('DEPT_INFOSEC', NULL, 2, '개인정보 보호·권한 신청(보안 통제) 담당, 준법 역할 가정'),
      ('DEPT_CUST_PROT', NULL, 1, '고객 민원·상담 업무'),
      ('DEPT_COLLECTION', NULL, 1, '고객 채권 업무'),
      ('DEPT_CARD_OPS', NULL, 1, '카드 고객 업무'),
      ('DEPT_CREDIT', NULL, 1, '고객 심사 업무'),
      ('DEPT_RISK', NULL, 1, '고객 사고(이상거래·피해 접수) 처리'),
      ('DEPT_CS_CENTER', NULL, 1, '상담 조직(대표 연락처 표기와 구분)'),
      ('DEPT_IT_OPS', NULL, 0, '고객 문서가 업무에 필수 아님'),
      ('DEPT_DIGITAL', NULL, 0, '고객 문서가 업무에 필수 아님');
  END IF;
END $$;

COMMIT;
-- END AUTO-GENERATED: policy seed

-- 확인 (init 로그에 출력된다)
SELECT t.team_name, u.user_id, u.user_name, u.position, u.grade, u.access_level, u.is_admin, u.is_active
  FROM policy.users u
  JOIN policy.teams t USING (team_cd)
 ORDER BY u.user_id;
