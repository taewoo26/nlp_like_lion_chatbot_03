-- 새 DB에서는 01_schema.sh 다음에 실행된다. 기존 DB에는 별도로 적용한다.
CREATE TABLE IF NOT EXISTS rag.person_roles (
    chunk_id       TEXT NOT NULL REFERENCES rag.chunks(chunk_id) ON DELETE CASCADE,
    mention_index  INT NOT NULL CHECK (mention_index >= 0),
    doc_id         TEXT NOT NULL,
    person_token   TEXT NOT NULL CHECK (person_token ~ '^<PERSON_[0-9a-f]{64}>$'),
    role           TEXT NOT NULL CHECK (role IN ('customer', 'employee', 'agent', 'staff_contact', 'unknown')),
    PRIMARY KEY (chunk_id, mention_index)
);
ALTER TABLE rag.person_roles OWNER TO rag_user;
CREATE INDEX IF NOT EXISTS person_roles_doc_token_role_idx
    ON rag.person_roles (doc_id, person_token, role);
