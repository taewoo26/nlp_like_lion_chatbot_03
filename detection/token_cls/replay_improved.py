"""Dev saved-prediction replay and ablation evaluation, without inference.

Run: python -B -m detection.token_cls.replay_improved
Baseline detector.py/run.py are preserved; run_compare supplies all metrics.
"""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path

from detection.base import Span
from detection.token_cls.detector import LABEL_MAP, decode_bio, validate_spans
from detection.token_cls.postprocess import VERSION, recover_financial_spans
from eval.cross_analysis import pairs
from result_paths import LOCAL_RESULTS, ROOT, private_output
import run_compare as scorer


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def spans(entities):
    return [Span(e['start'], e['end'], e['label'], e['text']) for e in entities]


def error_counts(docs, predictions):
    counts = {label: Counter(exact=0, boundary=0, label_error=0, complete_miss=0,
                             spurious=0) for label in scorer.LABELS}
    for doc, predicted in zip(docs, predictions):
        for gold in doc['gold']:
            overlaps = [p for p in predicted if scorer.overlap(gold, p)]
            same = [p for p in overlaps if p['label'] == gold['label']]
            kind = ('exact' if any(scorer.key(gold) == scorer.key(p) for p in same)
                    else 'boundary' if same else 'label_error' if overlaps else 'complete_miss')
            counts[gold['label']][kind] += 1
        for p in predicted:
            if not any(scorer.overlap(g, p) for g in doc['gold']):
                counts[p['label']]['spurious'] += 1
    return counts


def evaluate(docs, predictions, baseline):
    metrics, counts = scorer.summarize(docs, predictions)
    labels = {}
    for label, c in counts.items():
        sp, sr, sf = scorer.prf(c['strict_tp'], c['pred'], c['gold'])
        pp, pr, pf = scorer.prf(c['partial_tp'], c['pred'], c['gold'])
        labels[label] = dict(c, strict_p=sp, strict_r=sr, strict_f1=sf,
                             partial_p=pp, partial_r=pr, partial_f1=pf,
                             mask_rec=c['chars_masked'] / c['chars'] if c['chars'] else 0,
                             unprotected_chars=c['chars'] - c['chars_masked'])
    new_fp, lost_exact, changed_other = [], [], []
    new_strict_fp, new_partial_fp = [], []
    extra_masked_chars = 0
    for doc, before, after in zip(docs, baseline, predictions):
        old_keys, new_keys = {scorer.key(p) for p in before}, {scorer.key(p) for p in after}
        for p in after:
            if scorer.key(p) not in old_keys and not any(scorer.overlap(g, p) for g in doc['gold']):
                new_fp.append(dict(id=doc['id'], label=p['label'], start=p['start'], end=p['end']))
        for g in doc['gold']:
            if scorer.key(g) in old_keys and scorer.key(g) not in new_keys:
                lost_exact.append(dict(id=doc['id'], label=g['label'], start=g['start'], end=g['end']))
        for strict, added in ((True, new_strict_fp), (False, new_partial_fp)):
            def unmatched_keys(predicted):
                matched = set(pairs(doc['gold'], predicted, strict).values())
                return {scorer.key(p) for j, p in enumerate(predicted) if j not in matched}
            for label, start, end in sorted(unmatched_keys(after) - unmatched_keys(before)):
                added.append(dict(id=doc['id'], label=label, start=start, end=end))
        covered = lambda entities: {i for e in entities for i in range(e['start'], e['end'])}
        extra_masked_chars += len((covered(after) - covered(before)) - covered(doc['gold']))
        unrelated = lambda keys: {k for k in keys if k[0] not in ('phone_number', 'account_number')}
        if unrelated(old_keys) != unrelated(new_keys):
            changed_other.append(doc['id'])
    return dict(overall=metrics, per_label=labels, errors=error_counts(docs, predictions),
                new_spurious=new_fp, lost_baseline_exact=lost_exact,
                new_strict_fp=new_strict_fp, new_partial_fp=new_partial_fp,
                newly_masked_non_gold_chars=extra_masked_chars,
                changed_unrelated_documents=changed_other)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, default=ROOT / 'data/dev/pii_synth_finance_dev.jsonl')
    parser.add_argument('--baseline', type=Path, default=LOCAL_RESULTS / 'token_cls_baseline/dev')
    parser.add_argument('--out', type=private_output, default=LOCAL_RESULTS / 'token_cls_improved_v1/dev')
    args = parser.parse_args()
    if not args.input.resolve().is_relative_to((ROOT / 'data/dev').resolve()):
        parser.error('This experiment accepts Dev input only')
    if not args.out.resolve().is_relative_to(LOCAL_RESULTS.resolve()):
        parser.error('Experiment output must be inside local_results/')
    if args.out.exists():
        parser.error('Output already exists; choose a new directory')
    if args.out.resolve().is_relative_to(args.baseline.resolve()):
        parser.error('Output must be separate from the baseline')
    paths = [args.input, *(args.baseline / n for n in
             ('metadata.json', 'predictions.jsonl', 'raw_predictions.jsonl')),
             ROOT / 'detection/token_cls/detector.py', ROOT / 'detection/token_cls/run.py',
             ROOT / 'run_compare.py']
    preserved = {str(p): digest(p) for p in paths}
    meta = json.loads((args.baseline / 'metadata.json').read_text(encoding='utf-8'))
    if meta['status'] != 'complete' or meta['input_sha256'] != digest(args.input):
        raise ValueError('Baseline input provenance mismatch or incomplete run')
    docs = scorer.load_gold(args.input)
    if len(docs) != meta['documents']:
        raise ValueError('Document count differs from baseline metadata')
    source = scorer.load_jsonl(args.baseline / 'predictions.jsonl')
    raw = scorer.load_jsonl(args.baseline / 'raw_predictions.jsonl')
    ids = {d['id'] for d in docs}
    for rows in (docs, source, raw):
        if len(rows) != len(ids) or {r['id'] for r in rows} != ids:
            raise ValueError('Duplicate, missing or extra document IDs')
    source_by_id, raw_by_id = ({r['id']: r for r in rows} for rows in (source, raw))
    baseline, _ = scorer.load_pred(args.baseline / 'predictions.jsonl', docs)
    variants = {'baseline': baseline, 'phone_only': [], 'account_only': [], 'combined': []}
    audit = []
    for doc in docs:
        text = doc['text']
        mapped = spans(source_by_id[doc['id']]['entities'])
        raw_row = raw_by_id[doc['id']]
        raw_spans = spans(raw_row['entities'])
        validate_spans(text, mapped, set(scorer.LABELS))
        validate_spans(text, raw_spans, {e.label for e in raw_spans})
        for token in raw_row['tokens']:
            if not 0 <= token['start'] < token['end'] <= len(text) or token['text'] != text[token['start']:token['end']]:
                raise ValueError('Invalid raw token offsets')
        if decode_bio(text, raw_row['tokens']) != raw_spans:
            raise ValueError('Raw BIO replay differs from saved entities')
        expected = [Span(e.start, e.end, LABEL_MAP[e.label], e.text)
                    for e in raw_spans if e.label in LABEL_MAP]
        if expected != mapped:
            raise ValueError('Mapped baseline differs from raw entities')
        for name, phone, account in (('phone_only', True, False),
                                     ('account_only', False, True), ('combined', True, True)):
            output, events = recover_financial_spans(text, mapped, raw_spans, phone=phone, account=account)
            variants[name].append([e.to_dict() for e in output])
            audit.extend(dict(id=doc['id'], variant=name, **e) for e in events)
    evaluation = {name: evaluate(docs, ps, baseline) for name, ps in variants.items()}
    if any(digest(Path(p)) != value for p, value in preserved.items()):
        raise ValueError('An input changed during evaluation')
    args.out.mkdir(parents=True, exist_ok=False)
    for name in ('phone_only', 'account_only', 'combined'):
        folder = args.out if name == 'combined' else args.out / name
        folder.mkdir(exist_ok=True)
        with (folder / 'predictions.jsonl').open('x', encoding='utf-8') as handle:
            for doc, entities in zip(docs, variants[name]):
                handle.write(json.dumps(dict(id=doc['id'], entities=entities), ensure_ascii=False) + '\n')
    with (args.out / 'changes.jsonl').open('x', encoding='utf-8') as handle:
        for row in audit:
            handle.write(json.dumps(row) + '\n')
    for filename, payload in (
        ('evaluation.json', evaluation),
        ('metadata.json', dict(status='complete', version=VERSION, documents=len(docs),
                               inference_rerun=False, preserved_inputs=preserved,
                               implementation_sha256={name: digest(Path(__file__).with_name(name))
                                   for name in ('postprocess.py', 'replay_improved.py')},
                               error_priority=['exact', 'boundary', 'label_error', 'complete_miss'],
                               spurious_definition='prediction with no overlap with any gold'))):
        with (args.out / filename).open('x', encoding='utf-8') as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
    for name, values in evaluation.items():
        print(json.dumps(dict(variant=name, **values['overall'],
                              new_spurious=len(values['new_spurious']),
                              lost_baseline_exact=len(values['lost_baseline_exact'])), ensure_ascii=True))
    print(f'Saved to {args.out}')


if __name__ == '__main__':
    main()
