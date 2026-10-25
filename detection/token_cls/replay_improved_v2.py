"""Dev-only Baseline/V1/V2 comparison from saved entities; no inference.

Run: python -B -m detection.token_cls.replay_improved_v2
"""
import argparse
from collections import Counter
import json
from pathlib import Path

from detection.base import Span
from detection.token_cls.detector import LABEL_MAP, decode_bio, validate_spans
from detection.token_cls.postprocess import recover_financial_spans as recover_v1
from detection.token_cls.postprocess_v2 import VERSION, recover_financial_spans
from detection.token_cls.replay_improved import digest, evaluate, spans
from result_paths import LOCAL_RESULTS, ROOT, private_output
import run_compare as scorer


def checked_rows(path, ids):
    rows = scorer.load_jsonl(path)
    if len(rows) != len(ids) or {r['id'] for r in rows} != ids:
        raise ValueError('Duplicate, missing or extra IDs in saved predictions')
    return {r['id']: r for r in rows}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, default=ROOT / 'data/dev/pii_synth_finance_dev.jsonl')
    parser.add_argument('--baseline', type=Path, default=LOCAL_RESULTS / 'token_cls_baseline/dev')
    parser.add_argument('--v1', type=Path, default=LOCAL_RESULTS / 'token_cls_improved_v1/dev')
    parser.add_argument('--out', type=private_output, default=LOCAL_RESULTS / 'token_cls_improved_v2/dev')
    args = parser.parse_args()
    if not args.input.resolve().is_relative_to((ROOT / 'data/dev').resolve()):
        parser.error('Dev input only')
    if not args.out.resolve().is_relative_to(LOCAL_RESULTS.resolve()):
        parser.error('Output must be inside local_results/')
    if args.out.exists():
        parser.error('Output already exists; choose a new directory')
    for source in (args.baseline, args.v1):
        if args.out.resolve().is_relative_to(source.resolve()):
            parser.error('Output must be separate from prior runs')
    # Only explicitly selected Dev directories are read, never Test trees.
    preserved_paths = [args.input, ROOT / 'run_compare.py', ROOT / 'eval/cross_analysis.py',
                       *(ROOT / 'detection/token_cls' / name for name in
                         ('detector.py', 'run.py', 'postprocess.py', 'replay_improved.py', 'test_postprocess.py'))]
    for folder in (args.baseline, args.v1):
        preserved_paths.extend(p for p in folder.rglob('*') if p.is_file())
    preserved = {str(p): digest(p) for p in preserved_paths}
    meta = json.loads((args.baseline / 'metadata.json').read_text(encoding='utf-8'))
    v1_meta = json.loads((args.v1 / 'metadata.json').read_text(encoding='utf-8'))
    if meta['status'] != 'complete' or v1_meta['status'] != 'complete' or meta['input_sha256'] != digest(args.input):
        raise ValueError('Incomplete prior run or baseline input hash mismatch')
    docs = scorer.load_gold(args.input)
    ids = {d['id'] for d in docs}
    if len(docs) != len(ids) or len(docs) != meta['documents']:
        raise ValueError('Document count or IDs inconsistent')
    source = checked_rows(args.baseline / 'predictions.jsonl', ids)
    raw = checked_rows(args.baseline / 'raw_predictions.jsonl', ids)
    v1_source = checked_rows(args.v1 / 'predictions.jsonl', ids)
    baseline, _ = scorer.load_pred(args.baseline / 'predictions.jsonl', docs)
    v1, _ = scorer.load_pred(args.v1 / 'predictions.jsonl', docs)
    variants = dict(baseline=baseline, v1=v1, v2_phone_only=[], v2_account_only=[], v2=[])
    audit = []
    for doc in docs:
        text, doc_id = doc['text'], doc['id']
        mapped, raw_spans = spans(source[doc_id]['entities']), spans(raw[doc_id]['entities'])
        for entities in (mapped, spans(v1_source[doc_id]['entities'])):
            validate_spans(text, entities, set(scorer.LABELS))
        validate_spans(text, raw_spans, {e.label for e in raw_spans})
        for token in raw[doc_id]['tokens']:
            if not 0 <= token['start'] < token['end'] <= len(text) or token['text'] != text[token['start']:token['end']]:
                raise ValueError('Invalid saved token offsets')
        if decode_bio(text, raw[doc_id]['tokens']) != raw_spans:
            raise ValueError('Saved raw BIO replay mismatch')
        if [Span(e.start, e.end, LABEL_MAP[e.label], e.text)
                for e in raw_spans if e.label in LABEL_MAP] != mapped:
            raise ValueError('Baseline mapping mismatch')
        if recover_v1(text, mapped, raw_spans)[0] != spans(v1_source[doc_id]['entities']):
            raise ValueError('Saved V1 differs from frozen V1 replay')
        for name, phone, account in (('v2_phone_only', True, False),
                                     ('v2_account_only', False, True), ('v2', True, True)):
            result, decisions = recover_financial_spans(text, mapped, raw_spans,
                                                       phone=phone, account=account)
            variants[name].append([e.to_dict() for e in result])
            audit.extend(dict(id=doc_id, variant=name, **row) for row in decisions)
    evaluation = {name: evaluate(docs, ps, baseline) for name, ps in variants.items()}
    against_v1 = evaluate(docs, variants['v2'], v1)
    differences = []
    for doc, old, new in zip(docs, v1, variants['v2']):
        old_keys, new_keys = {scorer.key(e) for e in old}, {scorer.key(e) for e in new}
        if old_keys != new_keys:
            differences.append(dict(id=doc['id'], removed=sorted(old_keys - new_keys),
                                    added=sorted(new_keys - old_keys)))
    if any(digest(Path(p)) != value for p, value in preserved.items()):
        raise ValueError('Prior code/results changed during evaluation')
    args.out.mkdir(parents=True, exist_ok=False)
    for name in ('v2_phone_only', 'v2_account_only', 'v2'):
        folder = args.out if name == 'v2' else args.out / name.removeprefix('v2_')
        folder.mkdir(exist_ok=True)
        with (folder / 'predictions.jsonl').open('x', encoding='utf-8') as handle:
            for doc, entities in zip(docs, variants[name]):
                handle.write(json.dumps(dict(id=doc['id'], entities=entities), ensure_ascii=False) + '\n')
    with (args.out / 'candidate_audit.jsonl').open('x', encoding='utf-8') as handle:
        for row in audit:
            handle.write(json.dumps(row) + '\n')
    with (args.out / 'changes.jsonl').open('x', encoding='utf-8') as handle:
        for row in audit:
            if row['variant'] == 'v2' and row['action'] in ('add', 'expand'):
                handle.write(json.dumps(row) + '\n')
    payloads = {
        'evaluation.json': dict(variants=evaluation, v2_against_v1=against_v1,
                               v1_v2_prediction_differences=differences),
        'metadata.json': dict(status='complete', version=VERSION, documents=len(docs),
                              inference_rerun=False, preserved_inputs=preserved,
                              implementation_sha256={name: digest(Path(__file__).with_name(name))
                                  for name in ('postprocess_v2.py', 'replay_improved_v2.py', 'test_postprocess_v2.py')},
                              decision_counts=dict(Counter(r['reason'] for r in audit if r['variant'] == 'v2')),
                              error_priority=['exact', 'boundary', 'label_error', 'complete_miss'],
                              spurious_definition='prediction with no overlap with any gold')}
    for filename, payload in payloads.items():
        with (args.out / filename).open('x', encoding='utf-8') as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
    for name, values in evaluation.items():
        print(json.dumps(dict(variant=name, **values['overall'], new_spurious=len(values['new_spurious']),
                              lost_exact=len(values['lost_baseline_exact']))))
    print(f'V1/V2 differing documents: {len(differences)}; saved to {args.out}')


if __name__ == '__main__':
    main()
