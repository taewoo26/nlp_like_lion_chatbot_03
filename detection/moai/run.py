"""Write MoAI JSONL predictions without changing other baseline outputs."""
import argparse
from result_paths import private_output
import hashlib
import importlib.metadata
import json
import platform
from pathlib import Path
from time import perf_counter

import numpy as np

from detection.rule.run import iter_documents
from .detector import FILES, MODEL_ID, REVISION, REFERENCE_REVISION, LABEL_MAP, MoAIDetector


def sha256(path):
    digest = hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda: f.read(1024*1024), b''):
            digest.update(block)
    return digest.hexdigest()


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--input', required=True, type=Path)
    ap.add_argument('--out', required=True, type=private_output)
    ap.add_argument('--local-files-only', action='store_true')
    ap.add_argument('--cache-dir', default='models/huggingface')
    ap.add_argument('--max-length', type=int, default=1024)
    ap.add_argument('--overlap', type=int, default=128)
    ap.add_argument('--threads', type=int, default=1)
    ap.add_argument('--limit', type=int)
    a = ap.parse_args()
    if a.out.exists():
        ap.error('Output directory exists; choose a new directory')
    if a.limit is not None and a.limit < 1:
        ap.error('limit must be positive')
    documents = list(iter_documents(a.input))
    if a.limit:
        documents = documents[:a.limit]
    if not documents:
        ap.error('No documents')
    started = perf_counter()
    detector = MoAIDetector(cache_dir=a.cache_dir, local_files_only=a.local_files_only,
                          max_length=a.max_length, overlap=a.overlap, threads=a.threads)
    load_ms = (perf_counter()-started)*1000
    a.out.mkdir(parents=True, exist_ok=False)
    metadata = dict(status='incomplete', model=MODEL_ID, model_revision=REVISION,
        tokenizer_revision=REVISION, reference_revision=REFERENCE_REVISION,
        provider=detector.session.get_providers(), threads=a.threads,
        max_length=a.max_length, overlap=a.overlap, label_map=LABEL_MAP,
        id2label=detector.id2label, biases=detector.biases, load_ms=load_ms,
        input=str(a.input), input_sha256=sha256(a.input), documents=len(documents),
        python=platform.python_version(), platform=platform.platform(), processor=platform.processor(),
        warmup=False, fusion='maximum context per token index; earliest window on ties; global Viterbi',
        timing='tokenization + ONNX forward + fusion + log-softmax + Viterbi + spans; excludes loading, serialization, file I/O',
        packages={p: importlib.metadata.version(p) for p in
                  ('onnxruntime', 'numpy', 'tokenizers', 'huggingface-hub')},
        artifacts={name: sha256(detector.model_dir/name) for name in FILES})
    if platform.system() == 'Windows':
        metadata['packages']['msvc-runtime'] = importlib.metadata.version('msvc-runtime')
    meta_path = a.out/'metadata.json'
    def save_meta():
        meta_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding='utf-8')
    save_meta()
    times = []
    with (a.out/'predictions.jsonl').open('x', encoding='utf-8') as mapped, \
         (a.out/'raw_predictions.jsonl').open('x', encoding='utf-8') as raw:
        for doc in documents:
            started = perf_counter()
            result = detector.predict(doc['text'])
            ms = (perf_counter()-started)*1000
            times.append(ms)
            entities = [dict(label=s.label, start=s.start, end=s.end) for s in result.spans]
            mapped.write(json.dumps(dict(id=doc['id'], entities=entities, ms=ms),
                                    ensure_ascii=False, allow_nan=False)+'\n')
            raw.write(json.dumps(dict(id=doc['id'], **result.raw_dict()),
                                 ensure_ascii=False, allow_nan=False)+'\n')
            print(f"{doc['id']}: {len(result.spans)} mapped, {len(result.windows)} windows, {ms:.1f} ms", flush=True)
    metadata.update(status='complete', timing_ms=dict(total=sum(times), mean=float(np.mean(times)),
        median=float(np.median(times)), p95=float(np.percentile(times,95)), min=min(times), max=max(times)))
    save_meta()
    print(f'Loading: {load_ms:.1f} ms; inference mean: {np.mean(times):.1f} ms/doc; saved {len(times)} documents to {a.out}')


if __name__ == '__main__':
    main()
