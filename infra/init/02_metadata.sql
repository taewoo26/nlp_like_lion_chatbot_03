-- 종합: 역할·지역 메타데이터 (규칙 정의서 ROLE-07, ROLE-30, REG-20)
-- 새 DB에서는 01_schema.sh 다음에 자동 실행된다.
-- 이미 있는 DB에는 README의 '메타데이터 스키마 적용' 명령으로 한 번 실행한다. 여러 번 실행해도 안전하다.

DROP TABLE IF EXISTS rag.person_roles;   -- 실전 2 시험용 테이블. persons JSONB로 대체

ALTER TABLE rag.chunks ADD COLUMN IF NOT EXISTS persons JSONB  NOT NULL DEFAULT '[]';
ALTER TABLE rag.chunks ADD COLUMN IF NOT EXISTS regions TEXT[] NOT NULL DEFAULT '{}';
CREATE INDEX IF NOT EXISTS chunks_persons_gin ON rag.chunks USING GIN (persons jsonb_path_ops);
CREATE INDEX IF NOT EXISTS chunks_regions_gin ON rag.chunks USING GIN (regions);

-- 역할 기준표. 단서를 바꿀 때는 이 표와 starter/roles.py 의 DEFAULT_ROWS 를 함께 고친다.
CREATE TABLE IF NOT EXISTS rag.role_master (
    role_code    TEXT PRIMARY KEY,
    role_name_ko TEXT NOT NULL,
    role_group   TEXT NOT NULL,                  -- customer / staff / related / unknown
    query_cues   TEXT[] NOT NULL DEFAULT '{}',   -- 질문에서 이 역할을 뜻하는 단어
    doc_cues     TEXT[] NOT NULL DEFAULT '{}',   -- 문서에서 이름 앞뒤에 붙는 호칭·직급
    field_labels TEXT[] NOT NULL DEFAULT '{}',   -- '항목명: 이름' 형태의 항목명
    active       BOOLEAN NOT NULL DEFAULT true
);
ALTER TABLE rag.role_master OWNER TO rag_user;

INSERT INTO rag.role_master (role_code, role_name_ko, role_group, query_cues, doc_cues, field_labels) VALUES
 ('customer', '고객', 'customer', '{고객,회원,민원인}', '{고객,고객님,회원,회원님,민원인,가입자}',
  '{신청인,고객명,가입자,민원인,접수자,예금주,명의자,피해 고객,대상 고객}'),
 ('employee', '직원', 'staff', '{직원,행원,임직원}',
  '{사원,주임,대리,과장,차장,부장,팀장,실장,지점장,심사역}', '{}'),
 ('agent', '상담원', 'staff', '{상담사,상담원}', '{상담사,상담원}', '{}'),
 ('staff_contact', '담당자', 'staff', '{담당자}', '{}', '{담당자,문의처,처리 담당,연락 담당자,작성자}'),
 ('guardian', '대리인·보호자', 'related', '{대리인,보호자}', '{대리인,보호자}', '{대리인,보호자,수임인,법정대리인}'),
 ('unknown', '미상', 'unknown', '{}', '{}', '{}')
ON CONFLICT (role_code) DO NOTHING;
