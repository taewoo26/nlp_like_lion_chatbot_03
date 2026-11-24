# Airflow (로컬 PoC)

LocalExecutor 구성. 설계는 노션 '인프라 구성' > 'Airflow 설계'.

| DAG | 실행 | 하는 일 |
|---|---|---|
| `env_check` | 수동 | 패키지·마운트·outputs 쓰기·프로젝트 DB 접속 확인 |
| `person_ingest` | 수동 | `python -m starter.ingest` (preflight → ingest → postcheck) |
| `audit_daily_summary` | 매일 00:10 (서울) | 전날 감사 로그 집계 → `outputs/audit_reports/audit_summary_<날짜>.json` |

## 실행 (레포 루트, PowerShell)

```powershell
# 0. 프로젝트 DB 먼저 (네트워크 pii_starter_default 가 생김)
docker compose -p pii_starter --env-file .env -f infra/compose.yaml up -d --wait

# 1. 설정 파일: 복사 후 비밀번호·JWT 값 채우기
Copy-Item airflow\.env.example airflow\.env

# 2. 이미지 빌드 (처음엔 오래 걸림)
docker compose --env-file airflow/.env -f airflow/compose.yaml build

# 3. 메타DB 초기화 + 관리자 계정 (exited with code 0 이면 성공)
docker compose --env-file airflow/.env -f airflow/compose.yaml up airflow-init

# 4. 실행 → http://localhost:8080 (admin / AIRFLOW_ADMIN_PASSWORD)
docker compose --env-file airflow/.env -f airflow/compose.yaml up -d

# 끄기 (실행 기록 유지) / 완전 삭제
docker compose --env-file airflow/.env -f airflow/compose.yaml down
docker compose --env-file airflow/.env -f airflow/compose.yaml down -v
```

## 메모
- 프로젝트 코드·`.env`·DB가 필요한 서비스는 `airflow-scheduler` 하나뿐 (LocalExecutor 라 태스크가 여기서 실행됨).
- `.env` 의 DSN 은 로컬용(127.0.0.1)이라 태스크 안에서 host 를 `PROJECT_DB_HOST` 로 바꿔 쓴다.
- `person_ingest` 를 `reset=true` 로 돌리면 프로젝트 DB 적재를 지우고 다시 적재한다.
- `audit_daily_summary` 는 DB를 read-only 세션으로 읽고, 결과는 파일로만 남긴다.
