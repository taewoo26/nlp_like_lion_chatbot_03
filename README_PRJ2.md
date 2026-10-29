# pgvector_starter — 실전 2 시작 키트

가명처리 기반 검색 챗봇(기본형)을 바로 만들어 볼 수 있도록, DB 환경과 적재·검색·평가 흐름을 미리 연결해 둔 키트입니다.
**들어 있는 마스킹은 데모용입니다. `starter/pii_pipeline.py`를 여러분의 탐지기와 가명처리 모듈로 바꾸는 것이 실전 2의 핵심 과제입니다.**

## 구성

```
pgvector_starter/
├── docker-compose.yml        PostgreSQL 16 + pgvector
├── init/01_schema.sh         rag(검색용)·vault(매핑용) 스키마와 계정을 분리해 생성
├── .env.example              설정 예시 (.env 로 복사해서 사용, 커밋 금지)
├── requirements.txt
├── starter/
│   ├── pii_pipeline.py       ★ 탐지 + 가명처리 (데모 → 여러분의 모듈로 교체)
│   ├── ingest.py             문서 → 탐지 → 가명처리 → 청크 → 임베딩 → DB 적재
│   ├── search.py             ★ 검색 (의미 검색만 하는 최소 예제 → 팀에서 설계)
│   ├── run_queries.py        검색 질의 전체 실행 → 평가용 run 파일
│   ├── chat.py               검색 + LLM 답변 (마스킹 텍스트만 전달, 토큰 그대로 표시)
│   ├── embed.py              OpenAI 호환 임베딩 API / 테스트용 로컬 임베딩
│   ├── db.py, config.py
├── eval/
│   ├── eval_holdout_retrieval.py   검색 평가 (Hit@k, Recall@k, MRR, 혼동 문서 비율)
│   └── check_token_consistency.py  토큰 일관성 채점 (미탐, 쪼개짐, 합쳐짐)
└── data/
    ├── holdout_labeled.jsonl                코퍼스 (실전 1 Holdout과 같은 파일)
    ├── holdout_retrieval_queries.jsonl      검색 질의 88개
    ├── holdout_retrieval_queries_gold.jsonl 질의별 정답 문서
    └── holdout_persons_gold.jsonl           인물 ID (토큰 일관성 채점용)
```

## 빠른 시작

```bash
cp .env.example .env              # 비밀번호, API 키, HMAC_KEY 채우기
docker compose up -d              # DB 실행 (최초 1회 init/ 스크립트가 스키마 생성)
pip install -r requirements.txt

python -m starter.ingest --reset                       # 적재
python -m starter.search "연락처가 010-0791-2924인 건 찾아줘"   # 검색 확인
python -m starter.chat "보이스피싱 피해 접수 기록 알려줘"         # 답변 확인

python -m starter.run_queries --out outputs/run_v1.jsonl   # 질의 88개 실행
python eval/eval_holdout_retrieval.py --gold data/holdout_retrieval_queries_gold.jsonl --run outputs/run_v1.jsonl
python eval/check_token_consistency.py --gold data/holdout_persons_gold.jsonl --pred outputs/pseudo_holdout_labeled.jsonl
```

API 키 없이 흐름만 확인하려면 `.env`에서 `EMBED_PROVIDER=local`로 두세요. 검색 품질은 낮지만 전체 파이프라인이 돌아갑니다.

## 무엇을 바꾸나요

두 파일을 팀에서 설계합니다. 각 파일 맨 위 주석의 입출력 약속만 지키면 나머지 코드는 그대로 동작합니다.

**1. `starter/pii_pipeline.py`: 탐지 + 가명처리**

`detect()`와 `pseudonymize()`를 교체합니다.

- `detect(text)`: 실전 1에서 선정한 탐지기를 연결합니다.
- `pseudonymize(text, spans)`: 같은 값은 문서와 질의를 가리지 않고 항상 같은 토큰이 되도록 만듭니다. 토큰 생성 방법과 정규화 규칙은 팀에서 설계합니다.
- 교체를 마치면 `IS_DEMO = False`로 바꿉니다.

**2. `starter/search.py`: 검색**

지금은 마스킹된 질의로 의미 검색만 하고, 질의 토큰은 뽑아 두기만 합니다. 토큰을 검색에 어떻게 쓸지, 의미 검색과 어떻게 합칠지는 팀에서 설계합니다. `rag.chunks.tokens`에는 GIN 인덱스가 걸려 있습니다. 설계를 바꿀 때마다 `run_queries.py --out`으로 결과를 따로 저장하고, 질의 유형별 점수로 비교해 보세요.

**3. (선택) 청킹·임베딩**

청킹(`ingest.py`)과 임베딩(`embed.py`)은 그대로 써도 되고, 직접 구현하거나 다른 임베딩 모델(로컬 한국어 모델 등)과 비교해 봐도 됩니다. 채점은 결과 파일만 보기 때문에 아래 조건만 지키면 내부 구현은 자유입니다.

- 검색 결과(run 파일)와 가명처리 결과(`outputs/pseudo_*.jsonl`, 원문 기준 위치와 토큰) 형식을 유지합니다.
- 청킹은 반드시 가명처리 **후에** 합니다. 원문을 먼저 자르면 이름·번호가 청크 경계에서 잘려 탐지를 놓치거나, 원문이 임베딩 API로 나갈 수 있습니다.
- 청크마다 포함된 토큰 목록을 함께 저장합니다 (토큰 필터 검색에 필요).
- 바꾸기 전과 후의 검색 평가 결과를 비교해 보고서에 정리합니다. Holdout 문서는 대부분 짧아서 청킹 방식에 따른 차이가 작을 수 있는데, 이것도 분석 결과입니다.

임베딩 모델을 바꾸면 `.env`의 `EMBED_DIM`을 모델 차원에 맞추고 DB를 다시 만들어야 합니다 (아래 문제 해결 참고). 답변 프롬프트(`chat.py`)도 자유롭게 바꿔 보세요.

## 데모 상태에서 일어나는 일

교체 전에 한 번 돌려 보고, 아래 현상을 직접 확인해 보세요.

- 데모 탐지기는 전화번호와 이메일만 찾기 때문에 **이름·주소·계좌번호 등은 원문 그대로 DB와 임베딩에 들어갑니다.** 검색 점수가 그럴듯하게 나와도, 그건 이름이 가려지지 않았기 때문입니다.
- 데모 토큰은 문서 안에서만 번호를 매겨(`<PHONE_NUMBER_1>`) 서로 다른 번호가 같은 토큰을 씁니다. 적재 시 "토큰 충돌" 경고가 나옵니다.
- 토큰 일관성 채점에서 이름 미탐이 100%로 나옵니다.

## 토큰 일관성 결과 읽는 법

`eval/check_token_consistency.py`는 이름(person)을 기준으로 채점하고, 사례를 `outputs/token_consistency_cases.csv`에 저장합니다. 숫자만 적지 말고 사례를 보며 원인을 분석해 주세요.

- **미탐**: 이름이 토큰으로 바뀌지 않고 원문으로 남은 경우입니다. 탐지기의 재현율 문제입니다.
- **쪼개짐**: 한 사람이 여러 토큰으로 나뉜 경우입니다. 이름만 쓴 경우(예: "시우 님"), 띄어쓰기, 조사 등 정규화 규칙에서 원인을 찾아보세요.
- **합쳐짐 - 동명이인**: 이름이 같은 다른 사람이 같은 토큰이 된 경우입니다. 실전 2 기본형처럼 이름 문자열 기준으로 토큰을 만들면 **예상된 결과**이므로, 고치려 하기보다 원인과 검색에 주는 영향(동명이인 질의, `Confuse@10` 지표)을 정리하는 데 집중하세요. 동명이인 구분은 종합프로젝트에서 다룹니다.
- **합쳐짐 - 그 외**: 이름이 다른 사람끼리 같은 토큰이 된 경우입니다. 정규화를 너무 공격적으로 했거나 토큰 생성에 문제가 있는지 확인하세요.

가명처리 결과의 위치는 마스킹된 텍스트가 아니라 **원문 기준**이어야 채점이 맞게 됩니다.

## DB 구조와 계정

| 스키마 | 계정 | 테이블 | 내용 |
| --- | --- | --- | --- |
| rag | rag_user | rag.chunks | 마스킹된 청크, 토큰 목록(GIN 인덱스), 임베딩(HNSW 인덱스) |
| vault | vault_user | vault.token_map | 토큰 ↔ 정규화 값 ↔ 원문 표기 |

두 계정은 서로의 스키마에 접근할 수 없습니다. 검색·답변 코드는 `rag_user`만 쓰고, `vault_user`는 적재(가명처리) 단계에서만 씁니다. 실명 복원과 권한은 종합에서 다룹니다.

## 지켜야 할 규칙

- `.env`와 `HMAC_KEY`는 절대 커밋하지 않습니다 (`.gitignore`에 포함).
- 임베딩·LLM API에는 가명처리된 텍스트만 보냅니다.
- 코퍼스와 질의의 가명처리는 반드시 실전 1에서 선정한 탐지기의 결과로 합니다. `holdout_labeled.jsonl`의 정답 라벨(entities)은 적재에 쓰지 않습니다. 정답 라벨로 가명처리한 결과는 "탐지가 완벽할 때의 상한선" 비교용으로만 사용하세요.
- `holdout_persons_gold.jsonl`의 인물 ID를 토큰 생성에 쓰면 정답을 보는 것과 같습니다. 채점에만 사용합니다.

## 실전 2 산출물과의 연결

| 산출물 | 만드는 방법 |
| --- | --- |
| 가명처리 모듈 | `starter/pii_pipeline.py` 교체 |
| 토큰 일관성 채점 결과 | `eval/check_token_consistency.py` + `outputs/token_consistency_cases.csv`로 원인 분석 |
| 검색 평가 결과 | `starter/search.py` 설계 → `starter/run_queries.py` → `eval/eval_holdout_retrieval.py` (설계 버전별 비교 권장) |
| 챗봇 데모·대화 시연 | `starter/chat.py`를 기반으로 Streamlit, Gradio, FastAPI 등으로 구성 |

## 문제 해결

- **스키마를 다시 만들고 싶을 때**: `docker compose down -v` 후 `docker compose up -d` (init 스크립트는 볼륨이 비어 있을 때만 실행됩니다)
- **임베딩 차원 오류**: `.env`의 `EMBED_DIM`을 사용하는 임베딩 모델 차원에 맞추고 위처럼 DB를 다시 만드세요.
- **CPU 처리 시간**: `ingest.py`와 `run_queries.py`가 문서당·질의당 처리 시간을 출력합니다. 보고서에 측정 환경과 함께 적어 주세요.
