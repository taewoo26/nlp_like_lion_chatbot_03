-- 마이그레이션 001 검증 (읽기 전용). 실행 전후에 같은 쿼리로 비교한다.

-- A. 테이블 목록: chunks, person_roles(있다면 유지), role_master
SELECT table_schema, table_name
FROM information_schema.tables
WHERE table_schema IN ('rag', 'vault')
ORDER BY 1, 2;

-- B. 데이터 건수: 실행 전후 chunks·person_roles 건수가 같아야 한다
SELECT 'rag.chunks' AS tbl, count(*) FROM rag.chunks
UNION ALL
SELECT 'rag.person_roles', count(*) FROM rag.person_roles   -- 테이블이 없으면 이 두 줄을 지우고 실행
UNION ALL
SELECT 'rag.role_master', count(*) FROM rag.role_master;    -- 실행 전에는 없으므로 이 두 줄을 지우고 실행

-- C. 기존 행 내용이 그대로인지: 실행 전후 값이 같아야 한다
SELECT count(DISTINCT doc_id) AS docs,
       md5(string_agg(chunk_id || ':' || md5(masked_text) || ':' || array_to_string(tokens, ','), '|' ORDER BY chunk_id)) AS content_hash
FROM rag.chunks;

-- D. rag.chunks 열: persons(jsonb), regions(text[]) 추가, 나머지 그대로
SELECT column_name, data_type, is_nullable, column_default
FROM information_schema.columns
WHERE table_schema = 'rag' AND table_name = 'chunks'
ORDER BY ordinal_position;

-- E. 인덱스·제약
SELECT indexname, indexdef FROM pg_indexes WHERE schemaname = 'rag' AND tablename = 'chunks' ORDER BY 1;
SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint WHERE conrelid = 'rag.chunks'::regclass ORDER BY 1;

-- F. 기존 행 기본값: 모두 빈 값이어야 한다
SELECT count(*) FILTER (WHERE persons = '[]'::jsonb) AS empty_persons,
       count(*) FILTER (WHERE regions = '{}')        AS empty_regions,
       count(*)                                      AS total
FROM rag.chunks;

-- G. 역할 마스터 6종
SELECT role_code, role_name_ko, priority, query_cues, field_labels, active
FROM rag.role_master ORDER BY priority NULLS LAST, role_code;

-- H. 소유자: chunks·role_master 모두 rag_user
SELECT schemaname, tablename, tableowner FROM pg_tables WHERE schemaname = 'rag' ORDER BY 2;
