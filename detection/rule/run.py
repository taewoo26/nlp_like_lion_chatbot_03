"""Write predictions without invoking evaluation.

From the project root (output parent must already exist):
    python -m detection.rule.run --input data/dev/pii_synth_finance_dev.jsonl --output local_results/rules_dev.jsonl

Kiwi initialization is excluded from ms. There is no warmup, so the first
document may include lazy initialization costs. JSON serialization and I/O
are excluded. No gold entities or hard-case metadata reach the detector.
"""

import argparse
from result_paths import private_output
import json
import sys
from pathlib import Path
from time import perf_counter
from typing import TYPE_CHECKING, Iterator

if TYPE_CHECKING:
    from detection.base import BaseDetector


def iter_documents(path: Path) -> Iterator[dict]:
    """Validate IDs/text and yield only the two inference input fields."""
    seen = set()
    with path.open(encoding="utf-8-sig") as source:
        for line_number, line in enumerate(source, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Input line {line_number}: invalid JSON ({exc.msg})") from exc
            if not isinstance(row, dict):
                raise ValueError(f"Input line {line_number}: expected a JSON object")
            doc_id, text = row.get("id"), row.get("text")
            if not isinstance(doc_id, str) or not doc_id.strip():
                raise ValueError(f"Input line {line_number}: id must be a nonempty string")
            if not isinstance(text, str):
                raise ValueError(f"Input line {line_number}: text must be a string")
            if doc_id in seen:
                raise ValueError(f"Input line {line_number}: duplicate document id")
            seen.add(doc_id)
            yield {"id": doc_id, "text": text}


def predict_document(detector: "BaseDetector", document: dict) -> dict:
    started = perf_counter()
    spans = detector.detect(document["text"])
    elapsed_ms = (perf_counter() - started) * 1000
    return {
        "id": document["id"],
        "entities": [span.to_dict() for span in spans],
        "ms": elapsed_ms,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Regex + Kiwi baseline prediction writer")
    parser.add_argument("--input", required=True, type=Path, help="Document JSONL with id and text")
    parser.add_argument("--output", required=True, type=private_output, help="New prediction JSONL; parent must exist")
    args = parser.parse_args()

    if not args.input.is_file():
        parser.error("Input must be an existing file")
    if args.input.resolve() == args.output.resolve():
        parser.error("Input and output paths must differ")
    if not args.output.parent.is_dir():
        parser.error("Output parent directory must already exist")
    if args.output.exists():
        parser.error("Output already exists; choose a new filename")

    try:
        # Validate the entire input before model initialization or output writes.
        documents = list(iter_documents(args.input))
        if not documents:
            raise ValueError("Input has no documents")
    except (OSError, UnicodeError, ValueError) as exc:
        parser.error(str(exc))

    # Delay the optional dependency import until CLI/input validation completes.
    try:
        from detection.rule.detector import RuleDetector
    except ModuleNotFoundError as exc:
        parser.error(f"Required module is unavailable: {exc.name}. Check the existing environment.")

    detector = RuleDetector()
    completed = 0
    try:
        # Exclusive creation prevents accidental overwrite, including a race
        # after the exists() check. No directory or temporary file is created.
        with args.output.open("x", encoding="utf-8", newline="\n") as destination:
            for document in documents:
                prediction = predict_document(detector, document)
                destination.write(json.dumps(prediction, ensure_ascii=False, allow_nan=False) + "\n")
                completed += 1
    except FileExistsError:
        parser.error("Output already exists; choose a new filename")
    except Exception:
        # Keep partial output for inspection rather than deleting/moving files.
        print(
            f"Prediction failed; output may be incomplete ({completed} documents written). "
            "Do not evaluate this output as a completed run.",
            file=sys.stderr,
        )
        raise
    print(f"Saved predictions for {completed} documents to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
