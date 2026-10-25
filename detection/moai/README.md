# MoAI INT8 CPU 비교 모델

`BaseDetector.detect(text) -> list[Span]` 인터페이스와 기존 `run_compare.py`를 사용합니다.
규칙·KoELECTRA 구현과 기존 결과는 수정하지 않습니다.

모델은 [BCCard/MoAI-Privacy-Filter-INT8](https://huggingface.co/BCCard/MoAI-Privacy-Filter-INT8)의
`60731c85761bd46d23db2e742082a7223696e1c1` revision으로 고정했습니다.
ONNX 그래프와 external data를 ONNX Runtime 1.28.0 `CPUExecutionProvider`로 실행합니다.
PyTorch 모델 로더나 transformers pipeline을 사용하지 않습니다.
모델의 `tokenizer.json`을 Rust `tokenizers.Tokenizer`로 직접 읽으므로 새 transformers 버전도 필요하지 않습니다.

## 실행

프로젝트 루트의 PowerShell:

```powershell
.venv/Scripts/python.exe -m pip install -r detection/moai/requirements.txt
.venv/Scripts/python.exe -m detection.moai.run --input data/dev/pii_synth_finance_dev.jsonl --out local_results/moai_int8/dev
.venv/Scripts/python.exe run_compare.py --gold data/dev/pii_synth_finance_dev.jsonl --pred moai=local_results/moai_int8/dev/predictions.jsonl --out local_results/moai_int8/dev/eval
```

첫 실행은 고정 revision의 필요 파일만 `models/huggingface/`에 다운로드합니다.
캐시가 준비됐다면 `--local-files-only`로 네트워크 없이 실행합니다.
추가 실행은 `--out local_results/moai_int8/dev_run2` 등 **새 폴더**를 지정하고 평가 경로도 맞춥니다.
기존 추론 폴더는 덮어쓸 수 없습니다. 평가기의 `--out`도 해당 새 폴더 아래로 지정합니다.

`models/`는 기존 `.gitignore`에 이미 등록되어 있습니다. 모델 가중치나 DLL을 저장소에 추가하지 않습니다.
Windows에서는 requirements의 `msvc-runtime` DLL을 가상환경 내부에서 명시적으로 로드합니다.
오래된 System32 VC++ DLL이 선택되던 이 환경의 초기화 오류를 해결하며 시스템 DLL을 수정하지 않습니다.

## 디코딩과 좌표

- 모델 `config.json.id2label`의 전체 117라벨(O + 29종 BIOES)을 디코딩한 **후** 7종을 매핑합니다.
- [공식 디코더](https://github.com/openai/privacy-filter/blob/f7f00ca7fb869683eb732c010299d901457f19c3/opf/_core/decoding.py)의
  FP32 DP, `-1e9` 전이·시작·종료 제약, 동률 시 앞 ID 선택, calibration 기본 bias를 NumPy로 포팅했습니다.
  실제 추론에서는 유한 logits를 검증하고 log-softmax 후 Viterbi를 적용합니다.
  공식 코드의 비유한 입력 fallback도 포팅했으나 정상 추론 경로에서는 사용되지 않습니다.
- `viterbi_calibration.json.operating_points.default.biases`를 엄격히 검증하고 적용합니다.
  현 revision의 여섯 bias는 0입니다. Gold를 사용한 튜닝은 없습니다.
- 전체 문서를 truncation 없이 토큰화한 후 최대 1024토큰, overlap 128토큰으로 나눕니다.
  중복 구간은 토큰 위치별로 양쪽 문맥이 가장 많은 window를 선택하며 동률은 앞 window를 사용합니다.
  합친 점수에 문서 전체 Viterbi를 한 번 적용하여 chunk 경계의 개체를 이어 복원합니다.
- Rust tokenizer의 `Encoding.offsets`는 fast tokenizer의 `offset_mapping`에 해당합니다.
  원문 Python 문자열 좌표를 그대로 사용합니다. Unicode의 여러 토큰이 같은 문자 구간을 가리켜도
  토큰 **인덱스**로 관리해 토큰이 사라지지 않습니다. BIOES span의 바깥 공백만 `isspace()`로 제거하고
  내부 공백·줄바꿈·조사·구두점은 추가로 보정하지 않습니다.
- PERSON, PHONE, EMAIL, ADDRESS, RRN, ACCOUNT_NUMBER, CARD_NUMBER만 각각 기존 7종에 직접 매핑합니다.
  나머지 22종은 raw에 보관하며 평가·Mask Recall 입력에 넣지 않습니다.
  GENERIC_ID, FRN, SSN을 RRN이나 다른 라벨로 바꾸지 않습니다.

## 저장 형식과 시간

`predictions.jsonl`은 입력 문서당 정확히 한 줄입니다. 탐지하지 않은 문서도 저장합니다.

```json
{"id":"D0002","entities":[{"label":"person","start":53,"end":57}],"ms":12.3}
```

위 값은 형식 예시입니다. 실제 결과는 `local_results/moai_int8/dev/predictions.jsonl`에 있습니다.
`start/end`는 원문 기준 `[start,end)`입니다. 상세 BIOES 토큰, 원래 29종 span,
`unmapped_entities`, chunk 좌표는 `raw_predictions.jsonl`에 별도로 저장합니다.
대시보드의 `MoAI INT8` 소스로 두 파일을 함께 확인할 수 있습니다.

`ms`는 토큰화·ONNX 실행·window 결합·Viterbi·span 복원을 포함하고 로딩·JSON 직렬화·파일 I/O는 제외합니다.
`metadata.json`은 모델·tokenizer·공식 참조 revision, CPU provider, 스레드 수, bias,
모델 파일·입력 SHA256, 의존성 버전, 별도 `load_ms`, 문서별 시간 통계를 기록합니다.
기본 1스레드이며 warmup은 하지 않습니다. 첫 문서의 지연 초기화 비용은 문서 시간에 포함됩니다.

## 검증

```powershell
.venv/Scripts/python.exe -m unittest detection.moai.test_detector detection.token_cls.test_detector -v
visualization/.venv/Scripts/python.exe -m unittest visualization.test_dashboard detection.moai.test_dashboard -v
.venv/Scripts/python.exe -m detection.moai.smoke --out local_results/moai_int8/long_smoke2.json
```

`smoke`는 캐시된 실제 CPU 모델로 1024토큰 초과 문서의 끝부분 이메일 좌표와 빈 입력을 검사합니다.
단위 테스트는 chunk 경계를 가로지르는 개체, Unicode 중복 offset, 공백 보존, tail 누락을 별도로 검사합니다.

공식 구현 동등성 검증은 test-only `torch`와 고정 revision의 공식 `decoding.py`를 요구합니다.
모델 실행에는 torch가 필요하지 않습니다. 소스의 SHA256을 검증한 뒤 해당 클래스만 로드합니다.

```powershell
# 공식 소스를 models/moai-int8/official_decoding.py에 저장한 후:
.venv/Scripts/python.exe -m detection.moai.test_reference --source models/moai-int8/official_decoding.py
```

dev 결과·오류·평가 한계는 `local_results/moai_int8/dev/REPORT.md`를 참고합니다.

팀 공유용 집계 요약은 [results/moai_int8/dev/README.md](../../results/moai_int8/dev/README.md)에 있습니다. 원문·예측·상세 오류는 Git에서 제외되는 `local_results/`에만 보관합니다.
