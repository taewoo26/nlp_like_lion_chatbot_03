-- 역할 마스터 (메타데이터 규칙 정의서 v0.2: ROLE-01~07, 06_저장형식)
-- 새 DB에서 01_schema.sh 다음에 한 번 실행된다. 이미 만든 DB에는 자동 적용되지 않는다.
--
-- 이전 02_person_roles.sql(rag.person_roles)은 이 파일로 대체했다.
--   - 역할 저장은 rag.chunks.persons(JSONB)로 확정 (회의 결정, ROLE-30)
--   - person_roles는 rag.chunks를 FK로 참조해 ingest --reset(TRUNCATE rag.chunks)을 막았고,
--     토큰 CHECK가 64자리라 현재 12자리 토큰을 받지 못했으며, guardian이 없었다.
--
-- 역할 값과 단서는 코드에 직접 쓰지 않고 이 테이블에서 읽는다 (ROLE-07).
-- 단서 목록은 정의서 01_역할값 기준. 설명성 표기(부서명, 녹취 화자 표기, '○○ 드림' 서명)는
-- 단어 목록이 아니라 규칙(ROLE-10, ROLE-14)으로 처리하므로 여기에는 넣지 않는다.
-- priority는 원본에 agent=2만 정해져 있고 나머지는 제안값이다 (ROLE-16은 충돌 시 unknown).

CREATE TABLE rag.role_master (
    role_code     TEXT    PRIMARY KEY,
    role_name_ko  TEXT    NOT NULL,
    query_cues    TEXT[]  NOT NULL DEFAULT '{}',   -- 질의에서 이 역할을 뜻하는 단어 (ROLE-45)
    doc_cues      TEXT[]  NOT NULL DEFAULT '{}',   -- 문서에서 이름 앞뒤로 판단하는 단어 (ROLE-12·13)
    field_labels  TEXT[]  NOT NULL DEFAULT '{}',   -- '항목명:'·화자 표기 (ROLE-10·11)
    priority      INT,                             -- 낮을수록 강함. unknown은 NULL
    active        BOOLEAN NOT NULL DEFAULT true
);

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
  ('unknown',       '미상',          '{}', '{}', '{}', NULL);

ALTER TABLE rag.role_master OWNER TO rag_user;
