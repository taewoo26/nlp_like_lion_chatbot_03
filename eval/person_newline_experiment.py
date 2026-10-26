"""Dev-only saved-C newline splitting experiment; no model inference or tuning.

Run: python -B -m eval.person_newline_experiment
The split variant retains every non-CR/LF character in the original span.
The context variant keeps the original span unless every piece passes the
unchanged C valid_rule check. Gold is only used by evaluation, never splitting.
"""
import argparse
from collections import Counter
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re

import run_compare as scorer
from detection.selective import coverage, rule_checks, selective, valid_rule
from detection.token_cls.detector import LABEL_MAP, decode_bio, validate_spans
from detection.token_cls.postprocess_v2 import recover_financial_spans
from detection.token_cls.replay_improved import digest, spans
from eval.ensemble_experiment import describe
from result_paths import LOCAL_RESULTS, ROOT, private_output


def split_newline_person(text, predictions, *, patterns=None, require_context=False):
    """Return copied predictions and position-only audit; never consult gold.

    Only person spans containing CR/LF can change. No whitespace trimming,
    keyword additions, token joining, or unsupported-fragment removal occurs.
    Shape validity alone is not evidence that a piece is a real person.
    """
    if not isinstance(text, str):
        raise TypeError('text must be a string')
    if require_context and patterns is None:
        raise ValueError('Context validation requires existing rule patterns')
    try:
        validate_spans(text, spans(predictions), set(LABEL_MAP.values()))
        result, audit = [], []
        for prediction in predictions:
            a, b = prediction['start'], prediction['end']
            if prediction['label'] != 'person' or not re.search(r'[\r\n]', text[a:b]):
                result.append(dict(prediction))
                continue
            pieces = [dict(prediction, start=a + m.start(), end=a + m.end(),
                           text=text[a + m.start():a + m.end()])
                      for m in re.finditer(r'[^\r\n]+', text[a:b])]
            action = 'split'
            if len(pieces) < 2:
                action = 'keep_insufficient_pieces'
            elif require_context and not all(valid_rule(text, p, patterns) for p in pieces):
                action = 'keep_context_rejected'
            result.extend(pieces if action == 'split' else [dict(prediction)])
            audit.append(dict(start=a, end=b, action=action,
                              pieces=[dict(start=p['start'], end=p['end']) for p in pieces]))
        validate_spans(text, spans(result), set(LABEL_MAP.values()))
        removed = coverage(predictions) - coverage(result)
        if any(text[i] not in '\r\n' for i in removed):
            raise ValueError('Non-newline coverage changed')
        return result, audit
    except Exception:
        raise ValueError('Invalid newline-person experiment input or output') from None


def aggregate(result):
    """Keep existing evaluation definitions; omit entity-level source data."""
    errors = Counter()
    for values in result['errors'].values():
        errors.update(values)
    total = sum(c['gold'] for c in result['per_label'].values())
    return dict(
        overall=dict(result['overall']), per_label=result['per_label'],
        full_entities=result['coverage']['full_entities'], gold_entities=total,
        fully_protected_rate=result['coverage']['full_entities'] / total if total else 0,
        complete_misses=errors['complete_miss'],
        unprotected_chars=sum(c['unprotected_chars'] for c in result['per_label'].values()),
        boundary_errors=errors['boundary'], person_boundary_errors=result['errors']['person']['boundary'],
        spurious=errors['spurious'], new_spurious=len(result['new_spurious']),
        new_strict_fp=len(result['new_strict_fp']), new_partial_fp=len(result['new_partial_fp']),
        lost_baseline_exact=len(result['lost_baseline_exact']),
        **{k: result['coverage'][k] for k in
           ('lost_gold_chars', 'lost_coverage_chars', 'lost_full_entities', 'non_gold_chars')},
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=private_output)
    args = parser.parse_args()
    now = datetime.now(timezone(timedelta(hours=9)))
    out = args.out or LOCAL_RESULTS / 'person_newline_experiment' / now.strftime('dev_%Y%m%d_%H%M%S')
    if out.exists() or not out.resolve().is_relative_to(LOCAL_RESULTS.resolve()):
        parser.error('Choose a new directory inside local_results/')
    gold_path = ROOT / 'data/dev/pii_synth_finance_dev.jsonl'
    paths = dict(
        raw=LOCAL_RESULTS / 'token_cls_baseline/dev/raw_predictions.jsonl',
        mapped=LOCAL_RESULTS / 'token_cls_baseline/dev/predictions.jsonl',
        v2=LOCAL_RESULTS / 'token_cls_improved_v2/dev/predictions.jsonl',
        C=LOCAL_RESULTS / 'ensemble_experiment/dev/C/predictions.jsonl',
        rules=LOCAL_RESULTS / 'rule_baseline/dev/rules_dev.jsonl',
    )
    code_paths = [ROOT / name for name in
                  ('run_compare.py', 'detection/token_cls/detector.py',
                   'detection/token_cls/detector_v2.py', 'detection/token_cls/postprocess_v2.py',
                   'detection/rule/detector.py', 'detection/selective.py',
                   'detection/ensemble.py', 'eval/ensemble_experiment.py')]
    preserved = {str(p): digest(p) for p in [gold_path, *paths.values(), *code_paths]}
    docs = scorer.load_gold(gold_path)
    ids = {d['id'] for d in docs}
    if len(docs) != 100 or len(ids) != 100:
        raise ValueError('Expected 100 unique Dev documents')
    sources = {}
    for name, path in paths.items():
        rows = scorer.load_jsonl(path)
        if len(rows) != len(ids) or {r['id'] for r in rows} != ids:
            raise ValueError('Invalid saved Dev document IDs')
        sources[name] = {r['id']: r for r in rows}
    patterns = rule_checks(ROOT / 'detection/rule/detector.py')
    variants = {name: [] for name in ('baseline_C', 'newline_split', 'context_all_or_keep')}
    stage_counts, origin, decisions, separator = Counter(), Counter(), Counter(), Counter()
    for doc in docs:
        text, doc_id = doc['text'], doc['id']
        raw = sources['raw'][doc_id]
        if decode_bio(text, raw['tokens']) != spans(raw['entities']):
            raise ValueError('Raw BIO replay differs from saved entities')
        v2, _ = recover_financial_spans(
            text, spans(sources['mapped'][doc_id]['entities']), spans(raw['entities']))
        if v2 != spans(sources['v2'][doc_id]['entities']):
            raise ValueError('Frozen V2 replay mismatch')
        baseline = sources['C'][doc_id]['entities']
        combined, _ = selective(text, [s.to_dict() for s in v2],
                                sources['rules'][doc_id]['entities'], patterns)
        if [scorer.key(p) for p in combined] != [scorer.key(p) for p in baseline]:
            raise ValueError('Frozen C replay mismatch')
        variants['baseline_C'].append(baseline)
        for stage in ('mapped', 'v2', 'C'):
            stage_counts[stage] += sum(p['label'] == 'person' and bool(re.search(r'[\r\n]', text[p['start']:p['end']]))
                                       for p in sources[stage][doc_id]['entities'])
        for p in baseline:
            if p['label'] != 'person' or not re.search(r'[\r\n]', text[p['start']:p['end']]):
                continue
            origin['newline_person_spans'] += 1
            origin['same_raw_NAME_span'] += any(e['label'] == 'NAME' and (e['start'], e['end']) ==
                                                (p['start'], p['end']) for e in raw['entities'])
            origin['same_mapped_key'] += any(scorer.key(e) == scorer.key(p) for e in sources['mapped'][doc_id]['entities'])
            origin['same_v2_key'] += any(scorer.key(e) == scorer.key(p) for e in sources['v2'][doc_id]['entities'])
            hits = [g for g in doc['gold'] if scorer.overlap(g, p)]
            origin['spans_overlapping_multiple_gold_entities'] += len(hits) >= 2
            tokens = [t for t in raw['tokens'] if t['start'] < p['end'] and p['start'] < t['end']]
            for left, right in zip(tokens, tokens[1:]):
                if re.search(r'[\r\n]', text[left['end']:right['start']]):
                    origin['newline_gap_followed_by_I_NAME'] += right['tag'] == 'I-NAME'
        for name, context in [('newline_split', False), ('context_all_or_keep', True)]:
            result, audit = split_newline_person(text, baseline, patterns=patterns, require_context=context)
            variants[name].append(result)
            decisions.update(name + ':' + row['action'] for row in audit)
            removed = coverage(baseline) - coverage(result)
            separator[name + ':removed_CR_LF_chars'] += sum(text[i] in '\r\n' for i in removed)
    evaluations = {name: aggregate(describe(docs, ps, variants['baseline_C']))
                   for name, ps in variants.items()}
    if any(digest(Path(p)) != value for p, value in preserved.items()):
        raise ValueError('An existing source changed during experiment')
    payload = dict(scope='Dev-only exploratory; not a production policy', documents=len(docs),
                   newline_person_by_stage=dict(stage_counts), origin=dict(origin),
                   decisions=dict(decisions), removed_separators=dict(separator), variants=evaluations)
    metadata = dict(status='complete', created_at_kst=now.isoformat(), inference_rerun=False,
                    test_data_used=False, gold_used_by_splitter=False,
                    existing_algorithms_changed=False, source_files_unchanged=True,
                    preserved_inputs_sha256=preserved, experiment_sha256=digest(Path(__file__)),
                    policy='Split only CR/LF-containing person spans into nonempty line slices without trimming; '
                           'context variant rolls back the entire parent unless all pieces pass unchanged valid_rule',
                    recommendation='Do not apply to production C: simple split increases spurious predictions; '
                                   'context validation has no effect on this Dev set')
    out.mkdir(parents=True, exist_ok=False)
    for filename, value in [('evaluation.json', payload), ('metadata.json', metadata)]:
        with (out / filename).open('x', encoding='utf-8') as handle:
            json.dump(value, handle, ensure_ascii=True, indent=2)
    print(json.dumps(dict(saved_to=str(out), **payload), ensure_ascii=True))


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        raise SystemExit('Dev newline-person experiment failed: ' + type(error).__name__) from None
