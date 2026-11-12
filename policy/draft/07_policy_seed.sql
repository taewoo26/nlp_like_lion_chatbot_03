-- 자동 생성 파일: 직접 수정하지 마세요. 원본은 policy/access_policy.yaml
-- 생성: python -m policy.seed_sql
-- 초기값 정책 v0.4: doc_type 33종, 팀 10개, 레벨 규칙 10개
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
      ('사내메신저', 1, '내부업무', '직원 간 업무 소통'),
      ('업무메일', 1, '내부업무', '직원 간 업무 소통'),
      ('연체안내', 1, '고객업무', '고객 통화 기록'),
      ('영문혼용메일', 1, '내부업무', '대외 협력 업무 메일'),
      ('용역계약서', 1, '내부업무', '회사 용역 계약, 회사 운영 문서. [계획서와 차이: 계획서는 계약서를 Lv2 예시로 둠]'),
      ('우편반송처리', 1, '고객업무', '반송 처리 기록'),
      ('이상거래탐지', 1, '고객업무', '고객 통화 기록'),
      ('자동이체신청서', 1, '고객업무', '고객 신청서'),
      ('출장보고', 1, '내부업무', '내부 업무 보고'),
      ('카드발급심사', 1, '고객업무', '고객 카드 발급 심사 메모'),
      ('카드분실상담', 1, '고객업무', '고객 상담 기록'),
      ('피해접수', 1, '고객업무', '보이스피싱 피해 접수'),
      ('한도심사결과', 1, '고객업무', '고객 한도 심사 결과'),
      ('해외송금접수', 1, '고객업무', '송금 접수 기록'),
      ('회의록', 1, '내부업무', '직원 간 업무 회의. [계획서와 차이: 계획서는 Lv2 예시]'),
      ('감사지적', 2, '직원개인', '직원 대상 내부 감사 지적'),
      ('법인카드소명', 2, '직원개인', '직원 개인의 비용 소명'),
      ('인사발령', 2, '직원개인', '직원 인사 정보'),
      ('채용메일', 2, '직원개인', '채용 지원자 개인 정보');
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
