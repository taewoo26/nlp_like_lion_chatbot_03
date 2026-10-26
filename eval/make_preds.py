"""탐지기를 정답 문서에 돌려서 run_compare.py가 읽는 예측 jsonl을 만든다.

실행 (레포 루트에서)
    python -m eval.make_preds --detector rule
    python -m eval.make_preds --detector llm --limit 5          # LLM은 5건으로 먼저 시험
    python -m eval.make_preds --detector llm --runs 3           # 일관성 측정용 3회 반복

결과: results/preds/<탐지기>_<tag>.jsonl  (반복 실행이면 _run1, _run2 …)
주의: holdout 파일은 최종 평가 전까지 --data 로 넣지 않는다.
"""
import argparse
import json
import time
from pathlib import Path

COMBO_MODELS = ("koelectra", "koelectra_post", "veil", "moai")
COMBO_NAMES = [f"{m}_kiwi{suffix}" for m in COMBO_MODELS for suffix in ("", "_split")]

def detect_with_retry(det, text):
    """LLM의 속도 제한만 최대 3회 재시도한다. 대기 시간도 ms에 포함된다."""
    if det.name != "llm":
        return det.detect(text)
    from openai import RateLimitError
    for attempt in range(4):
        try:
            return det.detect(text)
        except RateLimitError as exc:
            if attempt == 3:
                raise
            delay = 60.0 * (attempt + 1)
            try:
                delay = max(delay, float(exc.response.headers.get("retry-after", "0")))
            except ValueError:
                pass
            print(f"429 속도 제한: {delay:g}초 후 재시도 ({attempt + 1}/3)", flush=True)
            time.sleep(delay)


def completed_ids(path, docs):
    """저장된 행이 현재 입력의 순서대로 된 접두 구간인지 확인한다."""
    if not path.exists():
        return set()
    raw = path.read_text(encoding="utf-8")
    if raw and not raw.endswith("\n"):
        raise ValueError(f"불완전한 마지막 행을 확인해 주세요: {path}")
    rows = [json.loads(line) for line in raw.splitlines() if line.strip()]
    ids = [row["id"] for row in rows]
    if ids != [doc["id"] for doc in docs[:len(rows)]]:
        raise ValueError(f"기존 예측과 입력 문서 순서가 다릅니다: {path}")
    return set(ids)


def load_detector(name: str):
    if name == "koelectra_post":
        from detection.token_cls.koelectra_post import KoElectraPostDetector
        return KoElectraPostDetector()
    if name == "koelectra":
        from detection.token_cls.koelectra import KoElectraDetector
        return KoElectraDetector()
    if name == "veil":
        from detection.token_cls.veil import VeilDetector
        return VeilDetector()
    if name == "moai":
        from detection.token_cls.moai import MoAIDetector
        return MoAIDetector()
    if name == "rule":
        from detection.rule import RuleDetector
        return RuleDetector()
    if name == "rule_kiwi":
        from detection.rule import RuleDetector
        return RuleDetector(use_kiwi=True)
    if name in COMBO_NAMES:
        from detection.combo import ComboDetector
        model, _, rest = name.partition("_kiwi")
        return ComboDetector(model, "split" if rest == "_split" else "union")
    if name == "llm":
        from detection.llm import LLMDetector
        return LLMDetector()
    raise SystemExit(f"알 수 없는 탐지기: {name}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--detector", default="rule", choices=["rule", "rule_kiwi", "llm", "moai", "veil", "koelectra", "koelectra_post", *COMBO_NAMES])
    ap.add_argument("--data", default="data/dev/pii_synth_finance_dev.jsonl")
    ap.add_argument("--tag", default="dev", help="출력 파일 이름에 붙일 구분 (dev/test)")
    ap.add_argument("--out-dir", default="results/preds")
    ap.add_argument("--runs", type=int, default=1, help="같은 문서를 몇 번 돌릴지 (LLM 일관성용)")
    ap.add_argument("--limit", type=int, default=None, help="앞에서 N건만")
    ap.add_argument("--resume", action="store_true", help="같은 모델·프롬프트로 저장된 예측부터 이어 실행")
    ap.add_argument("--interval", type=float, default=3.0, help="LLM 문서 간 대기 초(ms 측정 제외)")
    a = ap.parse_args()
    if a.interval < 0:
        ap.error("--interval은 0 이상이어야 합니다.")

    docs = [json.loads(l) for l in Path(a.data).open(encoding="utf-8") if l.strip()]
    if a.limit:
        docs = docs[:a.limit]
    det = load_detector(a.detector)
    out_dir = Path(a.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    paths = []
    for r in range(a.runs):
        suffix = f"_run{r + 1}" if a.runs > 1 else ""
        path = out_dir / f"{det.name}_{a.tag}{suffix}.jsonl"
        if path.exists() and not a.resume:
            raise SystemExit(f"기존 파일이 있습니다: {path}. 이어 실행은 --resume, 새 실험은 다른 --tag를 사용하세요.")
        done = completed_ids(path, docs) if a.resume else set()
        print(f"{path}: {len(done)}/{len(docs)}건 완료, 나머지 실행", flush=True)
        with path.open("a" if a.resume else "x", encoding="utf-8") as f:
            for d in docs:
                if d["id"] in done:
                    continue
                if det.name == "llm":
                    time.sleep(a.interval)
                t0 = time.perf_counter()
                try:
                    spans = detect_with_retry(det, d["text"])
                except ValueError as exc:
                    if det.name == "llm":
                        error_path = path.with_suffix(".failures.jsonl")
                        with error_path.open("a", encoding="utf-8") as error_file:
                            error_file.write(json.dumps({
                                "id": d["id"], "error": str(exc),
                                "response": getattr(det, "last_response", None),
                            }, ensure_ascii=False) + "\n")
                        print(f"{d['id']} 실패 응답 저장: {error_path}", flush=True)
                    raise
                ms = (time.perf_counter() - t0) * 1000
                f.write(json.dumps({
                    "id": d["id"],
                    "entities": [{"label": s.label, "start": s.start, "end": s.end} for s in spans],
                    "ms": round(ms, 2),
                }, ensure_ascii=False) + "\n")
                f.flush()
                done.add(d["id"])
                print(f"{d['id']} 저장 ({len(done)}/{len(docs)}건)", flush=True)
        paths.append(str(path))
        print(f"저장: {path} ({len(docs)}건)")

    print("\n채점 명령:")
    if a.limit is not None:
        print("주의: 일부 문서만 실행했습니다. 정답도 동일한 문서 ID로 제한한 파일을 --gold에 넣으세요.")
        return
    print(f"  python -m eval.run_compare --gold {a.data} --pred {det.name}={','.join(paths)}")


if __name__ == "__main__":
    main()
