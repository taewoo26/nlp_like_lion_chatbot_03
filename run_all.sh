#!/usr/bin/env bash
# 모델별로 (여러 번) 돌려서 한 표로 성능을 보여 준다.
#
#   ./run_all.sh                                   dev 전체, 빠른 모델 6종, 각 3회
#   DETECTORS="rule koelectra" ./run_all.sh        일부 모델만
#   DETECTORS="rule moai veil koelectra llm" ./run_all.sh   (LLM 포함: API 호출이 많아요)
#   GOLD=data/test/pii_synth_finance_test.jsonl TAG=test ./run_all.sh   test 중간 확인 (경로는 ls data 로 확인)
#
# 이미 같은 TAG 로 만들어 둔 예측(_run1~_runN)이 있으면 다시 돌리지 않고 재사용해요.
set -u
PY="${PY:-python}"
GOLD="${GOLD:-data/dev/pii_synth_finance_dev.jsonl}"
TAG="${TAG:-dev_final}"
RUNS="${RUNS:-3}"
DETECTORS="${DETECTORS:-rule rule_kiwi moai veil koelectra koelectra_post}"
PRED_DIR="${PRED_DIR:-results/preds}"
OUT="results/final_${TAG}"

case "$GOLD" in
  *holdout*) echo "holdout은 최종 평가 전까지 돌리지 않아요. 중단합니다."; exit 1;;
  *test*)
    read -r -p "test 파일이에요. 점수만 확인하고, test 본문·오류를 보고 코드·프롬프트를 고치지 않기로 팀과 정했나요? (y/N) " ans
    [ "$ans" = "y" ] || { echo "중단합니다."; exit 1; };;
esac

ARGS=()
for DET in $DETECTORS; do
  echo; echo "=== $DET ==="
  FILES=$(ls "${PRED_DIR}/${DET}_${TAG}"_run*.jsonl 2>/dev/null | sort | paste -sd, -)
  COUNT=$(printf '%s' "$FILES" | tr ',' '\n' | grep -c . || true)
  if [ "$COUNT" -ge "$RUNS" ]; then
    echo "이미 있는 예측 ${COUNT}개를 재사용해요."
  else
    if ! $PY -m eval.make_preds --detector "$DET" --data "$GOLD" --tag "$TAG" --runs "$RUNS"; then
      echo "!! $DET 실행에 실패해서 건너뛰어요. (이름이 맞는지, 모델 파일이 있는지 확인)"
      continue
    fi
    FILES=$(ls "${PRED_DIR}/${DET}_${TAG}"_run*.jsonl 2>/dev/null | sort | paste -sd, -)
  fi
  if [ -z "$FILES" ]; then
    echo "!! 예측 파일을 못 찾아 건너뛰어요. 'ls $PRED_DIR' 로 파일 이름을 확인하세요."
    continue
  fi
  ARGS+=(--pred "${DET}=${FILES}")
done

[ ${#ARGS[@]} -eq 0 ] && { echo "채점할 결과가 없어요."; exit 1; }
echo; echo "=== 채점 ==="
$PY -m eval.run_compare --gold "$GOLD" "${ARGS[@]}" --out "$OUT" || exit 1
$PY -m eval.show_summary --dir "$OUT"