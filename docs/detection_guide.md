# 탐지기 실행 가이드 (실전 1)

## 구조

```
dev.jsonl ─▶ 탐지기.detect(text) ─▶ 경계 후처리 ─▶ preds/<이름>_dev.jsonl ─▶ eval/run_compare.py ─▶ results/
```

| 파일 | 역할 |
| --- | --- |
| `detection/base.py` | `Span`, `BaseDetector`, 채점 라벨 7종 |
| `detection/io.py` | 데이터 읽기 (holdout 차단), 예측 jsonl 저장 |
| `detection/postprocess.py` | 공통 경계 후처리 (조사·호칭·괄호 떼기, 겹침 정리) |
| `detection/run_detect.py` | 실행 · 문서별 ms 측정 · 1만 건 환산 출력 |
| `detection/registry.py` | 탐지기 이름 → 클래스 |
| `detection/rule/` | 규칙 기반 (정규식 + Kiwi 이름) |
| `detection/token_cls/moai.py` | 기준 모델 (ONNX + 모델 카드 Viterbi) |
| `detection/llm/llm_detector.py` | LLM (OpenAI 호환) |
| `eval/run_compare.py` | 회사 제공 채점기 (수정 금지) |

## 실행

```bash
pip install -r requirements.txt

# 규칙 기반
python -m detection.run_detect --detector rule --split dev
python eval/run_compare.py --gold data/dev/pii_synth_finance_dev.jsonl --pred rule=preds/rule_dev.jsonl --out results/rule_dev

# 기준 모델 (처음 실행 시 약 1.6GB 다운로드)
python -m detection.run_detect --detector moai --split dev --limit 3   # 먼저 3건으로 동작 확인
python -m detection.run_detect --detector moai --split dev

# LLM (.env에 LLM_API_KEY, LLM_BASE_URL, LLM_MODEL 필요) — 일관성용 3회
python -m detection.run_detect --detector llm --split dev --runs 3
python eval/run_compare.py --gold data/dev/pii_synth_finance_dev.jsonl --pred llm=preds/llm_dev_r1.jsonl,preds/llm_dev_r2.jsonl,preds/llm_dev_r3.jsonl --out results/llm_dev

# 여러 방식 한 번에 비교 (disagreements.csv도 생성)
python eval/run_compare.py --gold data/dev/pii_synth_finance_dev.jsonl \
  --pred rule=preds/rule_dev.jsonl --pred moai=preds/moai_dev.jsonl --pred llm=preds/llm_dev_r1.jsonl \
  --out results/compare_dev
```

- `--limit N`으로 일부만 돌리면 나머지 문서는 채점에서 "아무것도 못 찾음"으로 처리됨 → 동작 확인용으로만
- `--raw`: 경계 후처리 끄기 (후처리 효과를 비교표에 넣을 때)
- `--tag v2`: 파일 이름 꼬리표 (같은 탐지기 개선 전후 비교)

## 추가 토큰 분류 모델 (Hugging Face)

`detection/token_cls/hf_token.py` 하나로 여러 모델을 돌린다. 지금 등록된 것:

| 이름 | 모델 | 라이선스 | 크기 | 라벨 | 우리 7종 대응 |
|---|---|---|---|---|---|
| atonlee | atonlee/koelectra-ko-pii-ner | Apache-2.0 | 14M (KoELECTRA-small) | BIO 29종 | NAME→person, ALIEN_ID→주민번호, 나머지 직접 대응, 22종 버림 |
| piilot | ParkJunSeong/PIILOT_NER_Model | Apache-2.0 | 110M (KoELECTRA-base) | BIO 8종 | p_nm→person 등 6종 대응, **카드 라벨 없음**, IP·여권 버림 |

```bash
pip install torch
python -m detection.run_detect --detector atonlee --split dev
python -m detection.run_detect --detector piilot --split dev
```

- 처음 실행 시 `models/<이름>`에 자동 다운로드 (커밋 안 됨)
- 512토큰보다 긴 문서는 겹치게 잘라 예측 (지금 데이터는 대부분 512 이내)
- 모델 추가: `MODELS`에 저장소 이름 + 라벨 대응표를 넣고, 클래스 3줄 + registry 한 줄
- **veil** (1T/veil-pii-ko-lite, Apache-2.0, INT8 ONNX 143MB, BIOES 32종)은 `detection/token_cls/veil_detector.py`
  - 저장소의 `veil.py`(Viterbi + 구간 병합 + 조사 제거)를 원본 그대로 불러 씀 → 수정 금지
  - 모델은 `models/veil`에 자동 다운로드. 스레드 수는 `.env`의 `VEIL_THREADS`(기본 4)
- 속도 비교 주의: MoAI는 INT8 ONNX, 이 두 모델은 FP32 PyTorch → 같은 CPU에서 재되 형식 차이를 보고서에 적기

## 내 탐지기 추가하기 (각자 구현 → 비교 → 채택)

1. `detection/rule/rule_jmy.py` 처럼 파일을 만들고 `BaseDetector`를 상속해 `detect(text)`만 구현

```python
from detection.base import BaseDetector, Span

class RuleDetector(BaseDetector):
    name = "rule_jmy"
    def detect(self, text):
        return [Span(start, end, "person", text[start:end])]
```

2. `detection/registry.py`에 한 줄 추가: `"rule_jmy": "detection.rule.rule_jmy:RuleDetector",`
3. `python -m detection.run_detect --detector rule_jmy --split dev` → 같은 채점기로 비교

## 지켜야 할 것

- 개선은 dev만 보고. test는 점수 확인용 (test 오류 CSV를 보며 규칙을 고치지 않기)
- `personas.json`·정답 이름 목록을 규칙에 쓰지 않기
- 정답 필드(`persons`, `roles`, `entities`)를 탐지기 안에서 쓰지 않기 — 탐지기 입력은 `text`뿐
- LLM 탐지만 원문 외부 전송 허용 (가상 데이터 실험 한정)

## 규칙 기반 v1 dev 결과 (참고)

| strict P | strict R | strict F1 | mask_rec | ms/문서 |
| --- | --- | --- | --- | --- |
| 1.000 | 0.996 | 0.998 | 0.999 | 약 20 |

- 놓친 2개: `은서랑`, `소율이랑` (성 없는 이름 + 조사, 같은 문서에 성명이 없음)
- ⚠ dev 오류를 보며 다듬은 규칙이라 **dev 점수는 낙관적**. 실력은 test로 확인하고, 하락 폭을 보고서에 적을 것
- 이름 규칙의 일반 어휘 목록(`COMMON_WORDS`, `GIVEN_STOP`)은 dev 오탐을 보고 추가한 것 → holdout에서 새 단어가 오탐될 수 있음
