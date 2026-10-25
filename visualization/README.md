# KoELECTRA Dev 대시보드

프로젝트 루트에서 PowerShell로 실행합니다.

```powershell
python -m venv visualization/.venv
visualization/.venv/Scripts/python.exe -m pip install -r visualization/requirements.txt
visualization/.venv/Scripts/python.exe -m streamlit run visualization/koelectra_dashboard.py --server.address localhost
```

기본 주소는 http://localhost:8501 입니다. 종료는 터미널에서 Ctrl+C를 누릅니다.
Streamlit만 추가로 필요하며 모델·torch·transformers를 로드하지 않습니다.
Streamlit의 pandas·packaging 제약과 Baseline 의존성을 분리하기 위해 전용 가상환경을 사용합니다.

`data/dev/pii_synth_finance_dev.jsonl`, `local_results/token_cls_baseline/dev/predictions.jsonl`,
`local_results/token_cls_baseline/dev/raw_predictions.jsonl`, `local_results/token_cls_baseline/dev/eval/errors_token_cls.csv`를 읽습니다.
기존 파일 쓰기, 모델 재추론, 네트워크 추론은 수행하지 않습니다. 실행 위치와 관계없이 저장소 루트를 기준으로 파일을 찾습니다.

- 라벨·오류 필터는 매칭 완료 후 문서 선택과 강조 구간에만 적용합니다. 원문 전체는 그대로 표시됩니다.
- Partial 실패 옵션은 538 TP / 548 Gold의 미매칭 10개를 기준으로 합니다. 병합 span은 원래 경계 그대로 표시하며 앞 이름에 배정된 내역을 확인할 수 있습니다.
- 초록: Strict TP, 주황: 경계, 빨강: 평가 출력 미탐, 보라: 겹치는 정답이 없는 FP, 분홍: 라벨 오류, 파랑+점선: 병합/1:1 실패입니다. Strict/Partial FP 여부는 표에서 각각 확인합니다.
- 원시 예측은 별도 영역에 표시하고 평가 지표·기본 필터에 섞지 않습니다. D0043·D0086에서 GENERIC_ID를 확인할 수 있습니다.
- 겹치는 span은 모든 start/end 경계에서 분할해 겹침 정보를 툴팁에 유지합니다. 원문은 HTML 이스케이프와 pre-wrap으로 안전하게 표시하며 공백·탭·줄바꿈을 보존합니다.
- 기존 오류 CSV에는 좌표가 없으므로 그대로 참조 표로 표시합니다. 하이라이트와 1:1 매칭은 JSONL 좌표를 사용합니다.

다른 탐지기를 추가할 때 `Source`와 `SOURCES`에 동일 스키마 파일을 등록하면 로더·매칭·필터·렌더링을 재사용할 수 있습니다.
등록된 탐지기는 KoELECTRA와 MoAI INT8입니다. 사이드바의 `탐지기`에서 선택합니다.

`results/`는 원문 없는 팀 공유 요약 전용입니다. 대시보드는 상세 결과가 있는 `local_results/`를 읽으며, 새로 복제한 저장소에는 이 로컬 자료가 포함되지 않습니다.
MoAI는 `local_results/moai_int8/dev/`의 동일 이름 JSONL과 `eval/errors_moai.csv`를 읽습니다.
평가 JSONL에 `text`가 없어도 원문 `[start:end]`로 표시하며, raw 파일의 BIOES 토큰과 미매핑 22종도 확인할 수 있습니다.
모델을 실행하거나 새로운 예측을 생성하는 기능은 없습니다.
