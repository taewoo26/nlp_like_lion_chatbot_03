# 팀 공유 실험 결과

이 디렉터리는 Git으로 공유하는 **검증된 집계 요약 Markdown** 전용이다. 개인정보 원문, 정답·예측 문자열, 문서 ID별 사례, 토큰, 비밀 정보, 배포 조건이 확인되지 않은 데이터 파일은 포함하지 않는다.

| 요약 | 내용 |
|---|---|
| [규칙 기반](rule_baseline/dev/README.md) | 성능·규칙 설정·라벨별 오류 규모·재현성 한계 |
| [KoELECTRA](token_cls_baseline/dev/README.md) | 고정 revision·실행 환경·성능·이름 오류 집계 |
| [MoAI INT8](moai_int8/dev/README.md) | 고정 revision·ONNX 설정·성능·주요 약점 |
| [모델 비교](comparison/dev/README.md) | 3종 성능과 규칙·KoELECTRA 교차 오류 집계 |

합성 금융 dev 100문서·정답548개에 대한 저장 예측을 기존 채점기로 다시 계산하고 과거 평가 CSV와 일치하는지 확인했다. 모델 재추론은 하지 않았다. test·holdout 일반화, 통제된 CPU 속도 비교, 전체 dev 반복 일관성은 추가 확인이 필요하다.

개인 실험·상세 분석·대시보드 입력은 Git에서 제외되는 `local_results/`에 둔다. 로컬 자료가 없는 팀원도 이 요약은 읽을 수 있지만 상세 대시보드·재평가는 승인된 데이터와 모델 캐시를 별도로 준비해야 한다.

## 공유 요약 생성

프로젝트 루트에서 실행한다. 생성기는 모델별 수치·라벨·선택한 설정만 출력하고 원본 보고서나 오류 행을 복사하지 않는다.

```powershell
# 최초 생성. 기존 요약이 있으면 덮어쓰지 않고 거부한다.
.venv/Scripts/python.exe -B -m eval.export_summary

# 후속 실험은 별도 경로에 생성한 뒤 검토한다.
.venv/Scripts/python.exe -B -m eval.export_summary --private-root local_results --out local_results/share_review/new_run
```

생성 후 지표 일치·설정·원문/비밀 정보 미포함·데이터 배포 조건을 검토한 문서만 공유한다. 새 결과를 반영할 때 기존 실험 원본과 과거 공유 기록을 보존한다.
