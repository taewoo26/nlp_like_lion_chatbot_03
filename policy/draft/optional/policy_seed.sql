-- 자동 생성 파일: 직접 수정하지 마세요. 원본은 policy/access_policy.yaml
-- 생성: python -m policy.seed_sql
-- 정책 버전 v0.3, YAML sha256 b70815b33b3d95f990ad012ceb238320970aab5ae96ef278e75c16a85995b582
BEGIN;

-- 같은 version인데 내용이 다르면 중단 (version을 올려야 함)
DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM policy.policy_version WHERE version = 'v0.3' AND source_sha256 <> 'b70815b33b3d95f990ad012ceb238320970aab5ae96ef278e75c16a85995b582') THEN
    RAISE EXCEPTION '정책 %의 내용이 DB와 다릅니다. access_policy.yaml의 version을 올리세요.', 'v0.3';
  END IF;
END $$;

UPDATE policy.policy_version SET is_current = false WHERE is_current AND version <> 'v0.3';
INSERT INTO policy.policy_version (version, updated, status, unregistered_min_level, audit_log_read_min_level, source_sha256, is_current) VALUES ('v0.3', '2026-11-06'::date, '팀 검토 전', 2, 2, 'b70815b33b3d95f990ad012ceb238320970aab5ae96ef278e75c16a85995b582', true)
ON CONFLICT (version) DO UPDATE SET is_current = true;

INSERT INTO policy.levels (level, name, description) VALUES
  (0, '일반 조회자', '전 직원 공개 문서 열람'),
  (1, '상담원·민원 담당자', 'Lv0 + 고객·업무 처리 문서 열람'),
  (2, '관리자·준법 담당자', '전체 열람, 실명 복원, 감사 로그 열람')
ON CONFLICT (level) DO UPDATE SET name = EXCLUDED.name, description = EXCLUDED.description;

-- 현재 버전 기준으로 전체 교체 (정책에서 빠진 doc_type은 미등록 = Lv2 전용이 됨)
DELETE FROM policy.doc_type_access;
INSERT INTO policy.doc_type_access (doc_type, min_level, rule, rationale, note, version) VALUES
  ('경조사공지', 0, 5, '전사 공지 성격. 직원 가족 정보가 있어도 Lv0 유지로 결정', NULL, 'v0.3'),
  ('공지', 0, 5, '전사 공지, PII 없음', NULL, 'v0.3'),
  ('민원현황', 0, 5, 'PII 없는 내부 현황 집계', NULL, 'v0.3'),
  ('상품안내', 0, 5, '대외 상품 안내, PII 없음', NULL, 'v0.3'),
  ('VOC요약', 1, 3, '고객 의견 요약', NULL, 'v0.3'),
  ('가맹점변경', 1, 3, '가맹점 요청서', NULL, 'v0.3'),
  ('고객정보변경', 1, 3, '고객 정보 처리', NULL, 'v0.3'),
  ('고객편지', 1, 3, '고객 접수 의견', NULL, 'v0.3'),
  ('녹취요약', 1, 3, '고객 통화 요약', NULL, 'v0.3'),
  ('대리인위임확인', 1, 3, '위임 확인서', NULL, 'v0.3'),
  ('대출상담', 1, 3, '고객 상담 기록', NULL, 'v0.3'),
  ('민원처리', 1, 3, '고객 민원 처리', NULL, 'v0.3'),
  ('사내메신저', 1, 4, '직원 간 업무 소통', NULL, 'v0.3'),
  ('업무메일', 1, 4, '직원 간 업무 소통', NULL, 'v0.3'),
  ('연체안내', 1, 3, '고객 통화 기록', NULL, 'v0.3'),
  ('영문혼용메일', 1, 4, '대외 협력 업무 메일', NULL, 'v0.3'),
  ('우편반송처리', 1, 3, '반송 처리 기록', NULL, 'v0.3'),
  ('이상거래탐지', 1, 3, '고객 통화 기록', NULL, 'v0.3'),
  ('자동이체신청서', 1, 3, '고객 신청서', NULL, 'v0.3'),
  ('출장보고', 1, 4, '내부 업무 보고', NULL, 'v0.3'),
  ('카드분실상담', 1, 3, '고객 상담 기록', NULL, 'v0.3'),
  ('피해접수', 1, 3, '보이스피싱 피해 접수', NULL, 'v0.3'),
  ('해외송금접수', 1, 3, '송금 접수 기록', NULL, 'v0.3'),
  ('회의록', 1, 4, '직원 간 업무 회의', '[계획서와 차이: 계획서는 Lv2 예시]', 'v0.3'),
  ('감사지적', 2, 2, '내부 감사', NULL, 'v0.3'),
  ('권한신청', 2, 2, '시스템 접근 권한, 보안 통제', NULL, 'v0.3'),
  ('법인카드소명', 2, 2, '내부통제, 비용 소명', NULL, 'v0.3'),
  ('분쟁조정', 2, 2, '분쟁 조정 검토 의견서, 준법 검토', NULL, 'v0.3'),
  ('용역계약서', 2, 2, '계약 문서', NULL, 'v0.3'),
  ('인사발령', 2, 2, '인사 정보', NULL, 'v0.3'),
  ('채용메일', 2, 2, '채용 지원자 정보, 인사', NULL, 'v0.3'),
  ('카드발급심사', 2, 2, '내부 심사 판단', NULL, 'v0.3'),
  ('한도심사결과', 2, 2, '내부 심사 판단', NULL, 'v0.3');

DELETE FROM policy.restore_rule;
INSERT INTO policy.restore_rule (level, label, mode, version) VALUES
  (0, 'account_number', 'none', 'v0.3'),
  (0, 'address', 'none', 'v0.3'),
  (0, 'card_number', 'none', 'v0.3'),
  (0, 'email', 'none', 'v0.3'),
  (0, 'person', 'none', 'v0.3'),
  (0, 'phone_number', 'none', 'v0.3'),
  (0, 'resident_registration_number', 'none', 'v0.3'),
  (1, 'account_number', 'none', 'v0.3'),
  (1, 'address', 'none', 'v0.3'),
  (1, 'card_number', 'none', 'v0.3'),
  (1, 'email', 'none', 'v0.3'),
  (1, 'person', 'none', 'v0.3'),
  (1, 'phone_number', 'none', 'v0.3'),
  (1, 'resident_registration_number', 'none', 'v0.3'),
  (2, 'account_number', 'full', 'v0.3'),
  (2, 'address', 'full', 'v0.3'),
  (2, 'card_number', 'full', 'v0.3'),
  (2, 'email', 'full', 'v0.3'),
  (2, 'person', 'full', 'v0.3'),
  (2, 'phone_number', 'full', 'v0.3'),
  (2, 'resident_registration_number', 'full', 'v0.3');

COMMIT;
