#!/bin/bash
# DB를 처음 만들 때 한 번만 실행된다. (볼륨이 이미 있으면 실행되지 않음)
# 검색용(rag)과 실명 매핑용(vault)을 스키마와 계정까지 분리한다.
set -e

psql -v ON_ERROR_STOP=1 \
  --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  -v rag_user="$RAG_DB_USER" -v rag_pw="$RAG_DB_PASSWORD" \
  -v vault_user="$VAULT_DB_USER" -v vault_pw="$VAULT_DB_PASSWORD" \
  -v db="$POSTGRES_DB" <<'SQL'

-- 1. 벡터 확장
CREATE EXTENSION IF NOT EXISTS vector;

-- 2. 계정
CREATE ROLE :"rag_user"   LOGIN PASSWORD :'rag_pw';
CREATE ROLE :"vault_user" LOGIN PASSWORD :'vault_pw';
GRANT CONNECT ON DATABASE :"db" TO :"rag_user", :"vault_user";

-- 3. 스키마 (각 계정이 자기 스키마의 주인)
CREATE SCHEMA rag   AUTHORIZATION :"rag_user";
CREATE SCHEMA vault AUTHORIZATION :"vault_user";

-- 4. 서로의 스키마 접근 차단
REVOKE ALL ON SCHEMA rag   FROM PUBLIC;
REVOKE ALL ON SCHEMA vault FROM PUBLIC;

-- 5. 접속하면 자기 스키마가 기본으로 잡히게
ALTER ROLE :"rag_user"   SET search_path = rag, public;
ALTER ROLE :"vault_user" SET search_path = vault, public;
SQL

echo "init 완료: rag / vault 스키마와 계정 생성"
