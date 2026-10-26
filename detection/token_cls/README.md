# MoAI CPU 탐지기

모델: BCCard/MoAI-Privacy-Filter-INT8
고정 revision: `60731c85761bd46d23db2e742082a7223696e1c1`

준비된 실제 파일은 `models/moai-int8/<revision>/`에서 읽는다.
네트워크 다운로드 없이 실행하며, 준비가 안 된 경우 오류 메시지를 출력한다.

```bash
python -m detection.token_cls.moai
python -m eval.make_preds --detector moai --tag dev_baseline
```

## 디코딩 조건

- config.json의 117개 BIOES 클래스를 모두 디코딩한 다음 평가용 7종만 반환한다.
- 공개 원본의 BIOES 시작·전이·종료 규칙을 NumPy로 재구현했다.
- viterbi_calibration.json의 default biases 6개를 읽는다. 현재 버전은 모두 0이다.
- 원문 토큰 offsets를 사용하고, 구간 양끝 공백만 제거한다. 조사·호칭 보정은 하지 않는다.
- 1024토큰 창, 128토큰 겹침으로 실행한다. 겹친 토큰의 logits를 평균한 뒤
  문서 전체에 Viterbi를 적용한다. 이는 이 프로젝트에서 선택한 긴 문서 처리 정책이다.
- CPU 스레드 2개, inter-op 1개. 생성 시 합성 문장으로 1회 워밍업한다.
  평가 ms에는 토큰화·추론·디코딩을 포함하고 초기 로딩·워밍업은 제외한다.
- 모델 카드의 검증 경로를 참고했지만, BC카드 전체 평가 구현과의 동일성이나
  공개 벤치마크 재현은 검증하지 않았다.

## 라벨 대응

PERSON → person, PHONE → phone_number, EMAIL → email, ADDRESS → address,
RRN → resident_registration_number, ACCOUNT_NUMBER → account_number,
CARD_NUMBER → card_number. 나머지는 평가용 출력에서 제외한다.

## 검증

`python -m unittest discover -s tests -p test_moai_decoder.py`

경로 전수탐색과의 일치, 불가능한 시작·끝 배제, 보정값 적용, 공백·유니코드 위치,
빈 입력, 긴 문서 겹침·끝부분 보존을 확인한다.
실제 CPU 합성 문장 및 dev 앞 5건 실행 완료. 5건 정답으로 제한해 채점한 결과는
`results/moai_smoke/`에 있다. 전체 dev/test 결과는 `docs/detector_comparison.md`와 `docs/탐지 모델 코드 최종 보고서.md`를 참고한다.

## 참고

- https://huggingface.co/BCCard/MoAI-Privacy-Filter-INT8
- https://github.com/openai/privacy-filter/blob/main/opf/_core/decoding.py

# Veil INT8 탐지기

- 저장소: https://huggingface.co/1T/veil-pii-ko-lite
- 고정 revision: `a8343030ef774ad78d6f0481a6877a971cef759d`
- 공식 `veil.py`를 검토 후 원본 그대로 로딩하며 SHA-256을 검사한다.
- 필요한 ONNX·토크나이저·설정·소스 8개만 `models/veil-int8/<revision>/`에 다운로드한다.
- 설정: CPU, threads=2, max_len=512, stride=128, o_bias=0.0,
  merge_adjacent=() (모델 카드의 벤치마크 설정).
- 공식 Viterbi·공백 처리·조사 제거·창 병합은 그대로 적용한다.
- 32종 전체 추론 후 프로젝트의 7종만 반환한다. 대응표는 위 MoAI와 같다.
- 워밍업 1회는 초기화 중 실행해 문서당 시간에서 제외한다.
- 이 고정 버전의 코드는 토크나이저 overflow로 창을 만들며, 모델 카드가 설명하는
  직접 창 분할/전각 정규화 옵션과 차이가 있다. 임의로 그 기능을 추가하지 않았다.
  설치된 환경에서 5,233자 합성 입력의 비공백 문자 전체가 창에 포함되고,
  문서 끝 전화번호가 탐지되는 것을 확인했다. 모든 입력에서의 완전성을 보증하지는 않는다.

```bash
python -m detection.token_cls.veil --download
python -m detection.token_cls.veil
python -m eval.make_preds --detector veil --tag dev_baseline
python -m eval.run_compare --gold data/dev/pii_synth_finance_dev.jsonl --pred veil=results/preds/veil_dev_baseline.jsonl --out results/veil_baseline
```

검증: 어댑터 단위 테스트 3개, 실제 CPU 짧은 문장·긴 문장, dev 앞 5건 저장 및
동일 5건 정답 채점 완료. `results/veil_smoke/` 참조. 5건 성능을 전체 성능으로 해석하지 않는다.
모델·공식 후처리를 함께 평가하는 구성이므로 MoAI와 후처리가 동일한 비교는 아니다.

# KoELECTRA INT8 탐지기

- 저장소: https://huggingface.co/atonlee/koelectra-ko-pii-ner
- 고정 revision: `1e75c01e707232401883cf364151bbe2e560c708`
- `onnx/model_int8.onnx`와 토크나이저·설정·README만 다운로드한다.
- ONNX Runtime CPU, intra-op 2 / inter-op 1 스레드, 초기화 중 1회 워밍업.
- 공식 예제의 토큰별 argmax + BIO 구간 연결. B는 새 구간으로 시작하며,
  앞에 같은 라벨 구간이 없는 I도 새 구간으로 복구한다.
- NAME → person. 나머지 6종 대응은 MoAI·Veil과 동일하다.
  PLACE·POSITION·ORGANIZATION 등은 출력에서 제외하며 ADDRESS와 합치지 않는다.
- 공백 경계만 정리한다. 조사 제거·대표번호 제외 등 추가 규칙은 적용하지 않는다.
- 긴 입력은 본문 510토큰 + CLS/SEP, 128토큰 겹침. 겹친 logits를 평균하고
  문서 전체 BIO를 복원한다. 공식 카드에는 긴 문서 병합 코드가 없어 이 정책은
  프로젝트에서 정한 구현이다. 제작자 공개 지표 재현은 미검증이다.
- 검증: BIO·원문 offsets·라벨 제외·긴 창 처리 단위 테스트 4개,
  실제 CPU 짧은 문장, 5,227자 문장 끝 전화번호, dev 앞 5건 실행 및 채점.
  결과: `results/koelectra_smoke/`. 전체 dev/test 평가는 완료했으며 결과는 `docs/탐지 모델 코드 최종 보고서.md`를 참고한다.

```bash
python -m detection.token_cls.koelectra --download
python -m detection.token_cls.koelectra
python -m eval.make_preds --detector koelectra --tag dev_baseline
python -m eval.run_compare --gold data/dev/pii_synth_finance_dev.jsonl --pred koelectra=results/preds/koelectra_dev_baseline.jsonl --out results/koelectra_baseline
```

설치 의존성은 기존 ONNX Runtime, transformers, huggingface-hub, numpy를 사용한다.
PyTorch와 원격 Python 코드 실행은 필요하지 않다.

## KoELECTRA 후처리 v1 (dev 오류 참고 실험)

- `koelectra_post.py`: person 예측 구간 안의 CR/LF에서 구간을 분리하고 양끝 공백만 제거.
- 원본 `koelectra`는 그대로 제공한다. 진단용 `predict_bio(text)`를 추출했으며,
  100건 post 결과가 저장된 baseline에 이 규칙만 적용한 결과와 정확히 일치함을 확인했다.
- D0002는 빈 줄 다음 `윤`에 I-NAME이 붙어 이름 두 개가 연결됨.
  D0007의 `달래`는 B-NAME, 다음 줄 `김서연`은 I-NAME으로 이어짐.
- 진단 토큰/원문 위치: `results/koelectra_bio/name_boundaries.jsonl`.
- 줄바꿈된 실제 단일 이름을 잘못 나눌 위험이 있다. 일반화 검증 전 임시 실험이다.
  조사·오탐 단어·상품명·직함 제거는 추가하지 않았다.
- 예측: `results/preds/koelectra_post_dev_v1.jsonl` (100건).
- 비교: `results/koelectra_post_v1_compare/`. strict F1 .908→.933,
  mask_rec .993→.993. 이름 경계 오류 CSV 행 29→9, 이름 오탐 30→36.
  13문서 변경, 정확 일치 정답 20개 추가, 기존 정확 정답 손실 0개.
- 별도 오탐 증가에는 기존 넓은 구간의 잘못된 부분이 독립 예측으로 분리된 영향이 있다.
  새 원문 문자를 더 가린 것은 아니다. partial 정밀도는 .934→.925로 하락했다.

```bash
python -m eval.make_preds --detector koelectra_post --tag dev_v1
python -m eval.run_compare --gold data/dev/pii_synth_finance_dev.jsonl --pred baseline=results/preds/koelectra_dev_baseline.jsonl --pred post_v1=results/preds/koelectra_post_dev_v1.jsonl --out results/koelectra_post_v1_compare
```
