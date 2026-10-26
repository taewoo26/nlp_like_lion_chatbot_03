"""데이터 읽기 · 예측 결과 저장 (run_compare.py 입력 형식)."""
import json
from pathlib import Path

DATA_DIR = Path("data")
SPLIT_FILES = {
    "dev": "pii_synth_finance_dev.jsonl",
    "test": "pii_synth_finance_test.jsonl",
    "custom": "pii_custom.jsonl",   # 팀이 직접 만든 검증셋 (scripts/tagged_to_jsonl.py)
    "holdout": "holdout_labeled.jsonl",  # 2026-10-26 배포. 개선 전 평가를 먼저 기록한 뒤에만 개선에 사용
}


def gold_path(split: str) -> Path:
    """data/dev/파일 또는 data/파일 둘 다 지원."""
    name = SPLIT_FILES[split]
    for p in (DATA_DIR / split / name, DATA_DIR / name):
        if p.exists():
            return p
    raise SystemExit(f"{name} 을 찾을 수 없습니다. data/{split}/ 또는 data/ 에 넣어 주세요.")


def load_docs(split: str) -> list[dict]:
    path = gold_path(split)
    with path.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def save_preds(path: Path, rows: list[dict]) -> None:
    """rows: [{"id": ..., "entities": [{label,start,end,(confidence)}], "ms": ...}]"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
