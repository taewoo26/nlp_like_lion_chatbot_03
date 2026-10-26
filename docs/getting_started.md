# 팀원 시작 가이드 — develop 받은 뒤 처음 할 일

목표: **내 컴퓨터에서 최종 탐지기 `hybrid_v5`를 돌려 팀장과 같은 점수가 나오는지 확인**하기.
같은 점수가 나오면 환경이 맞는 것이고, 그다음부터 각자 작업을 시작한다.

## 1. 코드 받기
```bash
git fetch origin
git checkout develop
git pull origin develop

# 내 브랜치를 develop 최신으로 맞추기 (브랜치 이름은 이니셜 대문자: JMY, KGR, KJH …)
git checkout 내브랜치          # 처음이면: git checkout -b 내브랜치
git merge develop
```

## 2. 파이썬 환경
```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```
- Veil·MoAI는 onnxruntime으로 돌아가서 torch가 없어도 됨 ("PyTorch was not found" 경고는 무시)
- atonlee·piilot을 돌릴 때만 `pip install torch`

## 3. 비밀값 (.env)
```bash
cp .env.example .env
```
- API 키·비밀번호는 **팀장에게 DM으로** 받아서 채운다. GitHub·노션·단톡방에 올리지 않는다.

## 4. 데이터 넣기 (GitHub에는 없음)
팀장에게 받아서 아래 위치에 둔다. `data/`는 커밋되지 않는다.
```
data/dev/pii_synth_finance_dev.jsonl
data/test/pii_synth_finance_test.jsonl
data/holdout/holdout_labeled.jsonl
```

## 5. 동작 확인 (3건만)
```bash
python -m detection.run_detect --detector hybrid_v5 --split dev --limit 3
```
- 처음 실행하면 Veil 모델(약 145MB)을 `models/veil`에 자동으로 받는다
- `[veil] 1T/veil-pii-ko-lite · INT8 ONNX · 라벨 32종` 줄이 보이면 정상

## 6. 점수 재현 확인
```bash
for s in dev test holdout; do python -m detection.run_detect --detector hybrid_v5 --split $s; done
python eval/run_compare.py --gold data/dev/pii_synth_finance_dev.jsonl --pred hybrid_v5=preds/hybrid_v5_dev.jsonl --out results/v5_dev
python eval/run_compare.py --gold data/test/pii_synth_finance_test.jsonl --pred hybrid_v5=preds/hybrid_v5_test.jsonl --out results/v5_test
python eval/run_compare.py --gold data/holdout/holdout_labeled.jsonl --pred hybrid_v5=preds/hybrid_v5_holdout.jsonl --out results/v5_holdout
```
| | strict F1 | mask_rec |
|---|---|---|
| dev | 1.000 | 1.000 |
| test | 0.994 | 0.999 |
| holdout | 0.997 | 0.999 |

- **정확도는 같아야 정상** (같은 코드·같은 모델). 다르면 팀장에게 출력 화면 공유
- **ms/문서는 컴퓨터마다 다름.** 결과표에는 평균 내지 말고 사람별로 기기(CPU)와 함께 적는다 (멘토링 1회차 피드백)

## 7. 그다음 작업 방식
- 새 탐지기·개선 버전은 새 파일로 만들고 `detection/registry.py`에 한 줄 추가 (예: `"hybrid_v6_jmy": ...`)
  → `python -m detection.run_detect --detector hybrid_v6_jmy --split dev`
- 개선은 **dev**(와 holdout 오류 분석)로, **test**는 확인용. 바꾼 뒤에는 dev·test·holdout을 모두 다시 채점해 떨어지지 않았는지 확인
- 내 브랜치에 커밋 → `git push origin 내브랜치` → GitHub에서 **develop으로 PR** → 리뷰 후 병합
- main·develop에 직접 push 금지. 커밋 전 `git status`로 `.env`, `data/`, `models/`가 없는지 확인

## 문서
- 탐지기 구조·실행법: `docs/detection_guide.md`
- 처음 보는 문서로 검증셋 만들기: `docs/custom_testset_guide.md`
