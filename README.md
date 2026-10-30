# 개인정보 보호형 RAG 챗봇 (PII-safe RAG Chatbot)

> 클라비 기업 프로젝트 · 멋쟁이사자처럼 AI 부트캠프
> 금융사 사내 문서의 개인정보를 탐지·가명처리하고, 실명 없이도 검색되는 RAG 챗봇 PoC

<!-- TODO: 데모 GIF 또는 스크린샷 -->

---

## 1. 프로젝트 소개

금융사 상담 기록, 민원, 메일, 회의록에는 고객 실명·연락처·주민등록번호·계좌번호가 섞여 있어 그대로 LLM에 넣을 수 없습니다. 단순 마스킹은 "같은 사람이 어느 문서에 나오는지" 같은 연결 정보까지 지워 검색이 불가능해집니다.

이 프로젝트는 다음 흐름으로 문제를 해결합니다.

```
문서 수집·전처리
  → 개인정보 탐지 (규칙 기반 / 토큰 분류 / LLM 비교 후 선정)
  → 가명처리 (정규화 + HMAC 결정적 토큰, 매핑 테이블 분리 저장)
  → 벡터 DB 적재 (마스킹 청크 임베딩 + 토큰 메타데이터)
  → 질의 토큰화 및 검색 (토큰 필터 + 의미 검색)
  → LLM 답변 생성 (마스킹 텍스트만 전달)
  → 출력 재검사
  → 권한 확인 후 실명 복원
```

| 단계 | 기간 | 내용 |
| --- | --- | --- |
| 실전 1 | 1주 | 탐지 방식 3종 정량 비교 및 탐지기 선정 |
| 실전 2 | 1주 | 가명처리 기반 검색 챗봇 (기본형) |
| 종합 | 2주 | 권한별 복원, 접근 통제, 출력 재검사, 통합 평가, API 서비스 |

## 2. 시스템 구성도

<!-- TODO: 아키텍처 이미지 (docs/architecture.png) -->

## 3. 기술 스택

| 구분 | 사용 기술 |
| --- | --- |
| Language | Python 3.13.x |
| 탐지 | 정규식, Kiwi(kiwipiepy), 토큰 분류 모델(BIOES), LLM |
| 가명처리 | HMAC-SHA256 |
| 벡터 DB | PostgreSQL + pgvector (Docker) |
| 임베딩 / LLM | 제공 클라우드 API (TODO: 최종 사용 모델 기재) |
| 서비스 | FastAPI / Streamlit (TODO) |

## 4. 폴더 구조

```
nlp_like_lion_chatbot_03/
├── detection/        # 탐지기 (rule/, token_cls/, llm/) + 공통 인터페이스
├── starter/          # 실전 2 시작 키트의 적재·검색·답변 진입점 (현재 데모)
├── pseudonym/        # 정규화, HMAC 토큰 생성, 매핑 테이블
├── rag/              # 청킹, 임베딩, 적재, 검색, 답변 생성
├── service/          # 권한, 실명 복원, 출력 재검사, API
├── eval/             # 탐지·일관성·검색·유출 평가
├── infra/            # pgvector_starter (docker-compose, init)
├── results/          # 실험 결과 CSV, 비교표
├── notebooks/        # 데이터 관찰 등 탐색용
├── docs/             # 구성도, 보고서
├── .env2.example     # 실전 2 설정 예시 (실제 값은 .env에 보관)
└── requirements.txt
```

## 5. 시작하기

### 5-1. 사전 준비
- Python 3.13.x (3.13.7에서 확인)
- Docker Desktop (Windows는 WSL2 필요)

### 5-2. 설치

```bash
git clone https://github.com/taewoo26/nlp_like_lion_chatbot_03.git
cd nlp_like_lion_chatbot_03

python3.13 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
python -m pip install -r requirements.txt

# 새 환경에서는 .env2.example을 참고해 .env를 준비합니다.
# 기존 .env는 덮어쓰지 않고 필요한 설정만 추가합니다.
```

### 5-3. 벡터 DB 실행

```bash
docker compose -p pii_starter --env-file .env -f infra/compose.yaml up -d --wait
docker compose -p pii_starter --env-file .env -f infra/compose.yaml ps
```

프로젝트 루트에서 실행합니다. 현재 실전 2 설정은 `pii_rag_db` 컨테이너를
`localhost:5432`에 띄우고, 최초 초기화 시 `infra/init/01_schema.sh`로
같은 DB 안에 `rag.chunks`와 `vault.token_map`, 전용 계정을 만듭니다.
접속 설정은 `.env2.example`을 참고해 루트 `.env`에 준비합니다.
이전 `rag_db` 컨테이너의 5433 포트 및 `public` 테이블과 구분합니다.
초기화 스크립트는 빈 데이터 볼륨에서만 실행되며 `down -v`는 DB 데이터를 삭제합니다.

동작 확인:

```bash
docker compose -p pii_starter --env-file .env -f infra/compose.yaml exec db psql -U admin -d pii_rag -c "SELECT extversion FROM pg_extension WHERE extname = 'vector';"
```

### 5-4. 실행

탐지기 설치·실행은 [팀 사용 안내](docs/detector_handoff.md), 판정 기준·최신 결과·오류 분석은 [평가 보고서](docs/탐지%20모델%20코드%20최종%20보고서.md), 개선 전 후보 비교는 [초기 비교표](docs/%ED%83%90%EC%A7%80%20%EB%AA%A8%EB%8D%B8%20%EC%BD%94%EB%93%9C%20%EC%B5%9C%EC%A2%85%20%EB%B3%B4%EA%B3%A0%EC%84%9C.md#initial-comparison)를 참고하세요.

실전 2의 데모 실행 결과, 개발 전 결정 사항, 올바른 데이터 경로와 평가 순서는
[가명처리·검색 개발 전 설계서](docs/practical2_design.md)를 참고하세요.
현재 `starter/pii_pipeline.py`는 데모이며 최종 가명처리 모듈은 아직 구현하지 않았습니다.

## 6. 환경 변수

`.env` 파일은 절대 커밋하지 않습니다. 실전 2는 `.env2.example`과 `starter/config.py`를 기준으로 설정합니다.

| 이름 | 설명 |
| --- | --- |
| `LLM_API_KEY` | LLM API 키 |
| `LLM_API_BASE`, `LLM_MODEL` | 답변 API 주소와 모델 |
| `EMBED_PROVIDER` | `local`: 동작 확인용 해시 임베딩 / `api`: 임베딩 API |
| `EMBED_API_KEY`, `EMBED_API_BASE`, `EMBED_MODEL`, `EMBED_DIM` | 임베딩 API와 벡터 차원 |
| `HMAC_KEY` | 가명처리 토큰 생성 키. 현재 데모에서는 사용하지 않으며 모듈 교체 시 연결 |
| `DB_HOST`, `DB_PORT`, `POSTGRES_DB` | 실전 2 DB 접속 대상 |
| `POSTGRES_USER`, `POSTGRES_PASSWORD` | 초기화 관리자 계정 |
| `RAG_PASSWORD`, `VAULT_PASSWORD` | 검색용·매핑용 분리 계정 비밀번호 |

## 7. 데이터 사용 원칙

- 제공된 가상 데이터셋(`pii_synth_finance`)만 사용하며 실제 개인정보는 사용하지 않습니다.
- **개선은 dev, 중간 확인은 test, 최종 평가는 holdout**으로 수행합니다.
- holdout 세트는 최종 평가 전까지 열어보지 않습니다. (담당: TODO)
- 이름 사전에 `personas.json`(정답 인물 목록)을 사용하지 않습니다.
- 챗봇 파이프라인에서는 LLM·임베딩 API로 마스킹된 텍스트만 전송합니다.

## 8. 실험 및 평가 결과

> 수치는 `results/`의 실험 결과 파일 기준으로 기입합니다.

### 8-1. 탐지 방식 비교 (실전 1)

| 방식 | Recall | mask_rec | Precision | F1 | 문서당 처리시간(CPU) | 일관성 |
| --- | --- | --- | --- | --- | --- | --- |
| 규칙 기반 | | | | | | - |
| 토큰 분류 (기준 모델) | | | | | | - |
| 토큰 분류 (추가 모델) | | | | | | - |
| LLM | | | | | | |

**선정 탐지기와 근거:** TODO

### 8-2. 가명처리·검색 (실전 2)

상세 설계·합의할 기준·실행 근거는 [개발 전 설계서](docs/practical2_design.md)에서 관리합니다.
현재까지는 데모 적재·단일 검색을 확인했고, LLM 답변과 88개 질의 평가는 미완료입니다.

| 항목 | 결과 |
| --- | --- |
| 토큰 일관성 (쪼개짐 / 합쳐짐) | |
| 검색 Hit@k / Recall@k (Holdout 질의 88개) | 미측정 |

### 8-3. 통합 평가 (종합, holdout)

| 항목 | dev | test | holdout |
| --- | --- | --- | --- |
| 탐지 Recall | | | |
| 토큰 일관성 | | | |
| 검색 Recall@k | | | |
| 출력 유출 건수 | | | |

## 9. 한계 및 개선 방향

- TODO

## 10. 팀원 및 역할

| 이름 | 역할 | 주요 기여 |
| --- | --- | --- |
| | 규칙 기반 탐지 · 가명처리 | |
| | 토큰 분류 모델 · 출력 재검사 | |
| | LLM 탐지 · 평가 총괄 | |
| | RAG 파이프라인 · 권한 · API | |

## 11. 협업 규칙

**브랜치**
- `main`: 항상 실행 가능한 상태 유지
- `feat/<기능>`, `fix/<내용>`, `exp/<실험>` 브랜치에서 작업 후 PR
- PR은 1명 이상 리뷰 후 머지

**커밋 메시지**

```
feat: 규칙 기반 전화번호 정규식 추가
fix: 조사 붙은 이름 경계 오류 수정
exp: LLM 탐지 3회 반복 일관성 실험
docs: README 실험 결과 업데이트
```

**탐지기 공통 출력 형식**

```python
[{"start": 12, "end": 15, "label": "person", "text": "윤선영"}]
```

## 12. 참고 자료

- 프로젝트 안내 문서 (클라비)
- 데이터셋 README (`pii_synth_finance`)
- 팀 노션: TODO 링크
