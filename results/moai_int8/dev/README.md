# MoAI INT8 dev 공유 요약

저장된 예측을 기존 채점기로 재집계하여 평가 CSV와 일치를 검증했다. 모델 추론은 재실행하지 않았다.

## 실험 설정

- 모델: `BCCard/MoAI-Privacy-Filter-INT8`
- 데이터: 합성 금융 dev 100문서, 평가 7종 정답 548개.
- 추가 학습 없음. warmup 없음. 시간은 로딩·파일 I/O를 제외한 저장 기록이다.
- 모델 revision: `60731c85761bd46d23db2e742082a7223696e1c1`
- 토크나이저 revision: `60731c85761bd46d23db2e742082a7223696e1c1`
- CPU 스레드: 1; 최대 길이: 1024토큰.
- 겹침: 128토큰.
- Python: 3.13.0.
- onnxruntime: 1.28.0.
- numpy: 2.5.4.
- tokenizers: 0.22.1.
- huggingface-hub: 0.36.0.
- msvc-runtime: 14.51.36231.
- CPUExecutionProvider, BIOES 전역 Viterbi, 외곽 공백만 제거. 29종 중 7종만 평가.
- 디코더 참조 revision: `f7f00ca7fb869683eb732c010299d901457f19c3`.

## 전체 성능

| 지표 | 값 |
|---|---:|
| strict_p | 87.1094% |
| strict_r | 81.3869% |
| strict_f1 | 84.1509% |
| partial_p | 90.2344% |
| partial_r | 84.3066% |
| partial_f1 | 87.1698% |
| mask_rec | 91.8792% |
| 평균 ms/문서 | 1186.86 |

## 라벨별 성능·오류 규모

| 라벨 | 정답 | 예측 | Strict F1 | Partial F1 | Strict 불일치 정답 | Partial 미매칭 정답 |
|---|---:|---:|---:|---:|---:|---:|
| person | 340 | 281 | 81.80% | 85.67% | 86 | 74 |
| phone_number | 82 | 93 | 91.43% | 91.43% | 2 | 2 |
| email | 33 | 33 | 87.88% | 100.00% | 4 | 0 |
| address | 41 | 58 | 82.83% | 82.83% | 0 | 0 |
| resident_registration_number | 20 | 24 | 90.91% | 90.91% | 0 | 0 |
| account_number | 16 | 6 | 54.55% | 54.55% | 10 | 10 |
| card_number | 16 | 17 | 96.97% | 96.97% | 0 | 0 |

## 주요 해석과 한계

- Strict는 라벨·경계 완전 일치, Partial은 같은 라벨의 겹침을 순서대로 1:1 매칭한다.
- mask_rec은 문자 피복률이며 평가 대상 7종 예측만 입력했다.
- 오류 규모는 정답 단위 집계다. 경계 오류와 완전 미탐을 혼동하지 않는다.
- test·holdout 일반화와 전체 dev 반복 일관성은 미측정이다.
- 속도는 실행 시점이 다른 저장값으로 통제된 비교가 아니다.
- person·account_number의 재현율과 address의 추가 탐지가 주요 개선 대상이다.
