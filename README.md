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
  → 개인정보 탐지 (규칙 기반 / 토큰 분류 / LLM 비교 후 선정)   ← 실전 1 완료: hybrid_v5
  → 가명처리 (정규화 + HMAC 결정적 토큰, 매핑 테이블 분리 저장)
  → 벡터 DB 적재 (마스킹 청크 임베딩 + 토큰 메타데이터)
  → 질의 토큰화 및 검색 (토큰 필터 + 의미 검색)
  → LLM 답변 생성 (마스킹 텍스트만 전달)
  → 출력 재검사
  → 권한 확인 후 실명 복원
```

| 단계 | 기간 | 내용 | 상태 |
| --- | --- | --- | --- |
| 실전 1 | 1주 | 탐지 방식 정량 비교 및 탐지기 선정 | ✅ 최종 탐지기 `hybrid_v5` |
| 실전 2 | 1주 | 가명처리 기반 검색 챗봇 (기본형) | 진행 예정 |
| 종합 | 2주 | 권한별 복원, 접근 통제, 출력 재검사, 통합 평가, API 서비스 | |

## 2. 시스템 구성도

<!-- TODO: 아키텍처 이미지 (docs/architecture.png) -->

## 3. 기술 스택

| 구분 | 사용 기술 |
| --- | --- |
| Language | Python 3.x |
| 탐지 | 정규식, Kiwi(kiwipiepy), 토큰 분류 모델 Veil(`1T/veil-pii-ko-lite`, ONNX INT8) |
| 비교 대상 | MoAI(`BCCard/MoAI-Privacy-Filter-INT8`), KoELECTRA(`atonlee/koelectra-ko-pii-ner`), 클로바 HCX |
| 가명처리 | HMAC-SHA256 |
| 벡터 DB | PostgreSQL + pgvector (Docker) |
| 임베딩 / LLM | 제공 클라우드 API (TODO: 최종 사용 모델 기재) |
| 서비스 | FastAPI / Streamlit (TODO) |

## 4. 폴더 구조

```
nlp_like_lion_chatbot_03/
├── detection/        # 탐지기 (아래 4-1 참고)
├── pseudonym/        # 정규화, HMAC 토큰 생성, 매핑 테이블 (실전 2)
├── rag/              # 청킹, 임베딩, 적재, 검색, 답변 생성 (실전 2)
├── service/          # 권한, 실명 복원, 출력 재검사, API (종합)
├── eval/             # run_compare.py (회사 제공 채점기, 수정 금지)
├── scripts/          # tagged_to_jsonl.py (자체 검증셋 변환), make_viewer.py (데이터 보기)
├── infra/            # docker-compose.yml, init/ (PostgreSQL + pgvector)
├── docs/             # getting_started.md, detection_guide.md, custom_testset_guide.md
├── notebooks/        # 데이터 관찰 (EDA)
├── logs/             # 데일리 스탠드업 로그 (YY-MM-DD.md)
├── data/  models/  preds/  results/   # 각자 로컬에만 (커밋 안 됨)
├── .env.example
└── requirements.txt
```

### 4-1. detection/ 한눈에 보기

**최종 탐지기 `hybrid_v5` = 아래 4개 파일만 보면 됩니다.**

```
detection/hybrid.py  ─ HybridV5Detector (결합·우선순위·필터)
 ├─ rule/regex_detector_v3.py   번호·이메일·주소 (정규식)
 ├─ rule/name_detector_v2.py    이름 (성씨 + 문맥 단서 + Kiwi)
 └─ token_cls/veil_detector.py  Veil 모델 (이름 보완 + 정규식이 놓친 번호·주소 보완)
```

| 파일 | 하는 일 |
| --- | --- |
| `base.py` | 공통 틀: `Span(시작, 끝, 라벨)`, 라벨 7종 |
| `io.py` | 데이터 읽기 (`--split dev / test / custom / holdout`), 예측 저장 |
| `postprocess.py` | 공통 경계 다듬기 (조사·호칭·괄호, 겹침 정리) |
| `registry.py` | 탐지기 이름 → 클래스. **새 탐지기는 여기에 한 줄 추가** |
| `run_detect.py` | 실행 · 문서별 ms 측정 · `preds/` 저장 |

나머지 파일은 보고서 비교표를 재현하기 위한 **실험 기록**입니다. v2·v3는 앞 버전을 상속해서 바뀐 부분만 담고 있어 v1을 지우면 최종 탐지기가 동작하지 않습니다.

| 파일 | 용도 |
| --- | --- |
| `rule/regex_detector.py`, `rule/name_detector.py`, `rule/rule_detector.py` | 규칙 기반 v1 (비교표 "규칙 기반", v2·v3의 바탕) |
| `rule/regex_detector_v2.py` | hybrid_v3·v4에서 사용 |
| `token_cls/moai.py` | 기준 모델 MoAI |
| `token_cls/hf_token.py` | KoELECTRA(atonlee)·piilot |
| `llm/llm_detector.py` | 클로바 HCX (`.env` 키 필요) |
| `hybrid.py`의 `hybrid` ~ `hybrid_v4` | 결합 탐지기 개선 과정 (아래 8-1) |

## 5. 시작하기

처음 받은 팀원은 **[docs/getting_started.md](docs/getting_started.md)** 를 순서대로 따라 하면 됩니다.

### 5-1. 사전 준비
- Python 3.x
- Docker Desktop (Windows는 WSL2 필요) — 실전 2부터

### 5-2. 설치

```bash
git clone https://github.com/taewoo26/nlp_like_lion_chatbot_03.git
cd nlp_like_lion_chatbot_03
git checkout develop

python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env             # 키 값은 팀장에게 DM으로 받기
```

데이터(`dev / test / holdout`)는 GitHub에 없습니다. 팀장에게 받아 아래 위치에 둡니다.

```
data/dev/pii_synth_finance_dev.jsonl
data/test/pii_synth_finance_test.jsonl
data/holdout/holdout_labeled.jsonl
```

### 5-3. 탐지 실행·채점

```bash
# 동작 확인 (처음 실행 시 Veil 모델 약 145MB 자동 다운로드)
python -m detection.run_detect --detector hybrid_v5 --split dev --limit 3

# 실행 → 채점
python -m detection.run_detect --detector hybrid_v5 --split test
python eval/run_compare.py --gold data/test/pii_synth_finance_test.jsonl \
  --pred hybrid_v5=preds/hybrid_v5_test.jsonl --out results/v5_test
```

다른 탐지기: `--detector rule | moai | veil | atonlee | llm | hybrid_v4 …` (목록은 `detection/registry.py`)

### 5-4. 벡터 DB 실행 (실전 2)

```bash
cd infra
docker compose up -d
```

## 6. 환경 변수

`.env` 파일은 절대 커밋하지 않습니다. `.env.example`에는 키 이름만 둡니다.

| 이름 | 설명 |
| --- | --- |
| `LLM_API_KEY`, `LLM_BASE_URL`, `LLM_MODEL` | LLM API (HCX) |
| `EMBEDDING_API_KEY` | 임베딩 API 키 |
| `HMAC_SECRET` | 가명처리 토큰 생성 키 (팀 전체 같은 값) |
| `DATABASE_URL_RAG` | 검색용 스키마 접속 정보 |
| `DATABASE_URL_VAULT` | 매핑 테이블 스키마 접속 정보 |
| `MOAI_MODEL_DIR`, `VEIL_MODEL_DIR` | 모델 폴더 (비우면 `models/` 아래 자동 다운로드) |
| `VEIL_THREADS` | Veil CPU 스레드 수 (기본 4) |

## 7. 데이터 사용 원칙

- 제공된 가상 데이터셋(`pii_synth_finance`)만 사용하며 실제 개인정보는 사용하지 않습니다.
- **개선은 dev, 중간 확인은 test**로 수행합니다.
- holdout은 **개선 전 평가를 먼저 기록한 뒤에만** 개선에 사용합니다. 개선 후 holdout 점수는 일반화 성능으로 해석하지 않습니다.
- 이름 사전에 `personas.json`(정답 인물 목록)을 사용하지 않습니다.
- 라벨 기준은 제공 데이터를 따릅니다. 성+직함(`정 과장`)과 일부가 가려진 번호(`010101-3******`)는 탐지 대상이 아닙니다 (고객사 기준, 멘토링 1회차).
- 챗봇 파이프라인에서는 LLM·임베딩 API로 마스킹된 텍스트만 전송합니다.

## 8. 실험 및 평가 결과

### 8-1. 탐지 방식 비교 (실전 1, test 200건)

| 방식 | Recall | mask_rec | Precision | F1 | 문서당 처리시간 | 일관성 |
| --- | --- | --- | --- | --- | --- | --- |
| 규칙 기반 (정규식 + Kiwi) | 0.989 | 0.997 | 1.000 | 0.994 | 3.3ms | 항상 동일 |
| 토큰 분류 기준 모델 (MoAI) | 0.810 | 0.903 | 0.915 | 0.826 | 70.0ms | 항상 동일 |
| 토큰 분류 추가 모델 (Veil) | 0.975 | 0.991 | 0.943 | 0.943 | 27.9ms | 항상 동일 |
| 토큰 분류 추가 모델 (KoELECTRA) | 0.8978 | 0.992 | 0.985 | | 14.10ms | 항상 동일 |
| LLM (HCX) ※ dev | 0.648 | 0.847 | 0.994 | 0.782 | 2,043.7ms | 3회 Jaccard 0.997 |
| **결합 (규칙 기반 + Veil, hybrid_v5)** | **0.997** | **0.999** | **1.000** | **0.994** | 31.6ms | 항상 동일 |

Recall·Precision은 partial, F1은 strict 기준. 처리시간은 M5 MacBook CPU (기기마다 다르므로 비교는 같은 기기에서).

**선정 탐지기와 근거:** 규칙 기반은 번호류·미끼 구분에 강하지만 처음 보는 문서에서 이름 재현율이 크게 떨어지고, Veil은 이름에 강하지만 대표번호 같은 미끼에 속습니다. 두 약점이 겹치지 않아 결합했으며, 결합 탐지기는 test 미끼 오탐 0건, 처음 보는 문서(자체 검증셋)에서도 mask_rec 0.998~1.000을 유지했습니다. CPU 로컬 실행이라 원문을 외부로 보내지 않습니다.

**결합 탐지기 개선 과정** (`detection/hybrid.py`)

| 버전 | 바뀐 점 | 계기 |
| --- | --- | --- |
| `hybrid` | 번호 = 정규식, 이름 = 규칙 ∪ Veil | 규칙 given 이름 0.705 ↔ Veil 1.000 |
| `hybrid_v2` | Veil 이름 필터 (`정 부장`, 상품명) | dev 오탐 분석 |
| `hybrid_v3` | 이름 단서 확실/약함 등급화 + 정규식 v2 | 자체 검증셋에서 규칙 이름 오탐 |
| `hybrid_v4` | 번호·주소도 정규식 ∪ Veil (형식 검사) | 자체 검증셋에서 새 형식 주소·카드 미탐 |
| `hybrid_v5` | 국제번호·우편번호, 경조사 용어·회사명 필터 | holdout 오류 분석 |

### 8-2. 가명처리·검색 (실전 2)

| 항목 | 결과 |
| --- | --- |
| 토큰 일관성 (쪼개짐 / 합쳐짐) | |
| 검색 Hit@k / Recall@k (질의 30개) | |

### 8-3. 통합 평가 (holdout)

| 항목 | dev | test | holdout 개선 전 (v4) | holdout 개선 후 (v5) |
| --- | --- | --- | --- | --- |
| 탐지 Recall | 1.000 | 0.997 | 0.969 | 0.997 |
| 탐지 mask_rec | 1.000 | 0.999 | 0.980 | 0.999 |
| 토큰 일관성 | | | | |
| 검색 Recall@k | | | | |
| 출력 유출 건수 | | | | |

holdout 개선 전 평가는 커밋 `9d44bd4`(2026-10-26)의 `hybrid_v4`로 수행했습니다.

## 9. 한계 및 개선 방향

- 단서가 없는 이름(`조치: 김주원에 대한`), 표 안의 이름, 한 글자 이름 일부는 놓칩니다 (holdout 이름 재현율 0.995).
- 경조사 용어·회사명 필터는 단어 목록 기반이라 새 어휘에는 한계가 있습니다.
- 개선 방향: 두 번째 모델 투표(KoELECTRA), 문서 안 다른 표기 전파(실전 2 토큰 일관성), 다양한 형식의 합성 데이터로 직접 학습.

## 10. 팀원 및 역할

| 이름 | 역할 | 주요 기여 |
| --- | --- | --- |
| 황태우 (팀장) | 데이터 분석, 규칙 기반, MoAI, Veil, LLM | 결합 탐지기 설계, 자체 검증셋 |
| 김규리 | 데이터 분석, 규칙 기반, MoAI, Veil | |
| 정민영 | 데이터 분석, 규칙 기반, MoAI, KoELECTRA, LLM | |
| 김정하 | 데이터 분석, 규칙 기반, MoAI, KoELECTRA, LLM | |

## 11. 협업 규칙

**브랜치**
- `main`: 발표·제출용 안정 버전
- `develop`: 팀 기본 브랜치 (최신 통합본)
- 개인 브랜치: 이니셜 대문자 (`HTW`, `JMY`, `KGR`, `KJH`)
- 개인 브랜치에서 작업 → `develop`으로 PR → 1명 이상 리뷰 후 머지. `main`·`develop`에 직접 push 금지
- 커밋 전 `git status`로 `.env`, `data/`, `models/`가 없는지 확인

**커밋 메시지**

```
feat: 규칙 기반 전화번호 정규식 추가
fix: 조사 붙은 이름 경계 오류 수정
exp: LLM 탐지 3회 반복 일관성 실험
docs: README 실험 결과 업데이트
```

**탐지기 공통 출력 형식** (`preds/<이름>_<split>.jsonl`, 문서당 한 줄)

```json
{"id": "D0002", "entities": [{"label": "person", "start": 12, "end": 15}], "ms": 23.4}
```

**데일리 로그**: `logs/YY-MM-DD.md` (전일 진행 사항 / 금일 진행할 사항 / 논의 사항)

## 12. 참고 자료

- 프로젝트 안내 문서 (클라비)
- 데이터셋 README (`pii_synth_finance`)
- 탐지기 상세: [docs/detection_guide.md](docs/detection_guide.md)
- 자체 검증셋 만들기: [docs/custom_testset_guide.md](docs/custom_testset_guide.md)
- 팀 노션: TODO 링크
