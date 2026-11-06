-- 마이그레이션 001: 메타데이터 열과 역할 마스터 추가 (메타데이터 규칙 정의서 v0.2)
-- 대상: 이미 만들어져 데이터가 있는 DB. (새 DB는 infra/init/ 스크립트가 같은 구조를 만든다)
-- 실행 계정: POSTGRES_USER(admin)
--
-- 지키는 것
--   - 기존 열·데이터·인덱스는 바꾸지 않는다. DROP·TRUNCATE·데이터 UPDATE 없음.
--   - rag.person_roles는 건드리지 않는다. (정리는 팀 결정 후 별도 마이그레이션)
--   - 다시 실행해도 오류 없이 같은 결과 (IF NOT EXISTS, ON CONFLICT DO NOTHING).
--   - 한 트랜잭션: 중간에 실패하면 아무것도 바뀌지 않는다.
--
-- 참고: PostgreSQL 11+는 상수 기본값이 있는 열 추가 시 테이블을 다시 쓰지 않는다.
--       기존 행은 persons = '[]', regions = '{}' 로 읽힌다.

BEGIN;

-- 1. rag.chunks 메타데이터 열 (ROLE-30·31·32, REG-20·21)
ALTER TABLE rag.chunks ADD COLUMN IF NOT EXISTS persons JSONB  NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE rag.chunks ADD COLUMN IF NOT EXISTS regions TEXT[] NOT NULL DEFAULT '{}';

-- persons는 배열만 허용 (init 스크립트와 같은 제약 이름)
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'chunks_persons_check' AND conrelid = 'rag.chunks'::regclass
    ) THEN
        ALTER TABLE rag.chunks
            ADD CONSTRAINT chunks_persons_check CHECK (jsonb_typeof(persons) = 'array');
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS chunks_persons_gin ON rag.chunks USING GIN (persons jsonb_path_ops);
CREATE INDEX IF NOT EXISTS chunks_regions_gin ON rag.chunks USING GIN (regions);

-- 2. 역할 마스터 (ROLE-01~07, 06_저장형식)
CREATE TABLE IF NOT EXISTS rag.role_master (
    role_code     TEXT    PRIMARY KEY,
    role_name_ko  TEXT    NOT NULL,
    query_cues    TEXT[]  NOT NULL DEFAULT '{}',
    doc_cues      TEXT[]  NOT NULL DEFAULT '{}',
    field_labels  TEXT[]  NOT NULL DEFAULT '{}',
    priority      INT,
    active        BOOLEAN NOT NULL DEFAULT true
);
ALTER TABLE rag.role_master OWNER TO rag_user;

-- 3. 초기 데이터 6종. 이미 있는 행은 덮어쓰지 않는다 (나중에 고친 단서 보존)
INSERT INTO rag.role_master (role_code, role_name_ko, query_cues, doc_cues, field_labels, priority) VALUES
  ('customer',      '고객',          '{고객,회원,민원인}',
                                     '{고객,고객님,회원,신청인,민원인,가입자,카드 소지자}',
                                     '{신청인:,고객명:,가입자:,민원인:}', 4),
  ('employee',      '직원',          '{직원,행원}',
                                     '{대리,과장,차장,부장,팀장,심사역,담당 직원,사원}',
                                     '{}', 3),
  ('agent',         '상담원',        '{상담사,상담원}',
                                     '{상담사,상담원,상담 직원}',
                                     '{상담사:,상담원:}', 2),
  ('staff_contact', '담당자 연락처', '{담당자,담당}',
                                     '{담당자,문의처,담당,작성자}',
                                     '{담당자:,문의처:,담당:,작성자:}', 1),
  ('guardian',      '보호자·대리인', '{대리인,보호자}',
                                     '{대리인,위임받은 자,수임인,보호자,법정대리인}',
                                     '{대리인:,보호자:,수임인:,법정대리인:}', 1),
  ('unknown',       '미상',          '{}', '{}', '{}', NULL)
ON CONFLICT (role_code) DO NOTHING;

COMMIT;
