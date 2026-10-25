"""Write isolated mapped/raw predictions and reproducibility metadata."""
import argparse
from result_paths import private_output
import hashlib
import importlib.metadata
import json
import platform
from pathlib import Path
from time import perf_counter

from detection.rule.run import iter_documents
from detection.token_cls.detector import MODEL_ID, REVISION, TokenClassificationDetector


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--input', required=True, type=Path)
    ap.add_argument('--out', required=True, type=private_output, help='New, nonexisting run directory')
    ap.add_argument('--limit', type=int, help='Smoke run: first N input documents')
    ap.add_argument('--local-files-only', action='store_true')
    ap.add_argument('--max-length', type=int, default=512)
    ap.add_argument('--stride', type=int, default=128)
    ap.add_argument('--threads', type=int, default=1)
    args = ap.parse_args()
    if args.out.exists():
        ap.error('Output directory already exists; choose a new directory')
    if args.limit is not None and args.limit < 1:
        ap.error('limit must be positive')
    documents = list(iter_documents(args.input))
    if args.limit:
        documents = documents[:args.limit]
    if not documents:
        ap.error('No documents')
    started = perf_counter()
    detector = TokenClassificationDetector(local_files_only=args.local_files_only,
        max_length=args.max_length, stride=args.stride, threads=args.threads)
    load_ms = (perf_counter() - started) * 1000
    args.out.mkdir(parents=True, exist_ok=False)
    metadata = dict(status='incomplete', model=MODEL_ID, model_revision=REVISION,
        tokenizer_revision=REVISION, device='cpu', seed=0, threads=args.threads,
        max_length=args.max_length, stride=args.stride, warmup=False,
        timing='tokenization + forward + window fusion + BIO decoding + validation; excludes loading and file I/O',
        input=str(args.input), input_sha256=hashlib.sha256(args.input.read_bytes()).hexdigest(),
        documents=len(documents), load_ms=load_ms, python=platform.python_version(),
        platform=platform.platform(), processor=platform.processor(),
        packages={p: importlib.metadata.version(p) for p in
                  ('torch', 'transformers', 'tokenizers', 'huggingface-hub', 'safetensors', 'numpy')})
    meta_path = args.out / 'metadata.json'
    meta_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding='utf-8')
    with (args.out / 'predictions.jsonl').open('x', encoding='utf-8') as mapped, \
         (args.out / 'raw_predictions.jsonl').open('x', encoding='utf-8') as raw:
        for doc in documents:
            started = perf_counter()
            result = detector.predict(doc['text'])
            ms = (perf_counter() - started) * 1000
            row = dict(id=doc['id'], entities=[s.to_dict() for s in result.spans], ms=ms)
            mapped.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + '\n')
            raw.write(json.dumps(dict(id=doc['id'], **result.raw_dict()),
                                 ensure_ascii=False, allow_nan=False) + '\n')
    metadata['status'] = 'complete'
    meta_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'Saved {len(documents)} documents to {args.out}')


if __name__ == '__main__':
    main()
