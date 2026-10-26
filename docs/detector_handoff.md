# 조합 탐지기 사용 안내

규칙(rule_kiwi)과 토큰 분류 모델을 결합한 탐지기의 설치·사용·평가 방법입니다. 모델 파일과 평가 데이터는 별도로 준비합니다.

## 설치와 실행

Python 3.13 환경에서 레포 루트 기준으로 실행합니다. 모델 관련 패키지는 현재 로컬에서 사용한 버전으로 고정했습니다. 다른 OS의 새 환경 설치는 별도로 확인해야 합니다.

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-dev.txt

# 사용할 모델만 준비합니다. 다운로드 후 합성 문장으로 동작을 확인합니다.
python -m detection.token_cls.koelectra --download
python -m detection.token_cls.veil --download

python -m pytest -q
```

Windows에서는 가상환경 활성화에 `.venv\Scripts\activate`를 사용합니다. 실행만 할 때는 `requirements.txt`를 설치해도 됩니다. KoELECTRA 기본·후처리는 같은 모델 파일을 사용합니다. MoAI가 필요한 경우 `python -m eval.moai_smoke --revision 60731c85761bd46d23db2e742082a7223696e1c1`로 준비합니다.

```python
from detection.combo import ComboDetector

# 'veil', 'koelectra', 'koelectra_post' 중 팀이 선정한 모델 사용
model = ComboDetector('koelectra_post', policy='split')
text = '김민준 고객에게 연락했습니다.'
for span in model.detect(text):
    assert span.text == text[span.start:span.end]
    print(span.to_dict())
```

탐지기는 한 번 생성하고 문서마다 `detect(text)`를 호출합니다. 결과는 원문 기준 `[start, end)` 위치입니다. 가명처리 팀은 이 구간을 입력으로 사용합니다. 모델 다운로드 후 추론은 로컬 CPU에서 실행합니다.

평가 데이터는 별도로 전달받아 `data/`에 놓습니다. 예측·채점 예시는 다음과 같습니다.

```bash
python -m eval.make_preds --detector koelectra_post_kiwi_split --data data/dev/pii_synth_finance_dev.jsonl --tag team_dev_v1
python -m eval.run_compare --gold data/dev/pii_synth_finance_dev.jsonl --pred koelectra_post_kiwi_split=results/preds/koelectra_post_kiwi_split_team_dev_v1.jsonl --out results/team_dev_v1
python -m eval.show_summary --dir results/team_dev_v1
```

기존 파일과 겹치면 새 tag를 사용합니다. 채점에는 항상 `--out`을 지정합니다. test는 숫자 확인, 개선은 dev에서 진행하며 holdout 평가 시점은 팀이 정합니다.

## 주요 파일과 역할

| 파일 | 역할 |
|---|---|
| `detection/base.py` | 탐지기 인터페이스와 결과 형식 `Span` 정의 |
| `detection/combo.py` | 규칙과 모델의 탐지 구간 병합, union/split 정책 적용 |
| `detection/validation.py` | 탐지 구간과 원문 위치 일치 여부 검사 |
| `detection/token_cls/koelectra.py` | KoELECTRA 모델 준비·추론·BIO 태그를 구간으로 변환 |
| `detection/token_cls/koelectra_post.py` | KoELECTRA 이름 예측을 줄바꿈에서 분리 |
| `detection/token_cls/veil.py` | Veil 모델 준비·추론·프로젝트 라벨로 변환 |
| `detection/token_cls/moai.py` | MoAI 모델 추론·BIOES 디코딩 |
| `detection/rule/detector.py`, `names.py`, `kiwi_filter.py` | 정규식·이름 후보 규칙·Kiwi 필터로 개인정보 탐지 |
| `eval/make_preds.py` | 문서별 예측과 처리 시간을 JSONL로 저장 |
| `eval/run_compare.py` | 정답과 예측을 비교해 성능·일관성·오류 CSV 생성 |
| `eval/show_summary.py` | 채점 결과를 요약 표로 정리 |
| `eval/speed_summary.py` | 저장된 반복 실행의 처리 시간 평균·중앙값 요약 |
| `eval/moai_smoke.py` | MoAI 모델 다운로드와 짧은 문장 추론 확인 |
| `tests/` | 조합 정책·모델 어댑터 단위 테스트 |
| `requirements.txt`, `requirements-dev.txt` | 실행 의존성·테스트 의존성 |

모델별 설정은 [토큰 분류 모델 안내](../detection/token_cls/README.md), 성능은 [조합 비교표](detector_comparison.md)를 참고합니다. 예측·채점 결과는 `results/`, 모델은 `models/`, 평가 데이터는 `data/`에 저장됩니다. 이 폴더들은 Git에 포함되지 않으므로 새 환경에서는 별도로 준비하거나 생성합니다.
