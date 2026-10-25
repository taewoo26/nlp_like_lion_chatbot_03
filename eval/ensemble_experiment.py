"""Fixed Dev-only A/B/C experiment on saved V2/rule predictions; no inference.

python -B -m eval.ensemble_experiment
python -B -m eval.ensemble_experiment --self-test
"""
import argparse
import ast
from collections import Counter
from datetime import date
from fractions import Fraction
import json
from pathlib import Path
import re
import unittest

import run_compare as scorer
from detection.token_cls.replay_improved import digest, evaluate
from result_paths import ROOT, LOCAL_RESULTS, private_output

POLICY = {
    'A': 'Saved KoELECTRA V2 predictions, unchanged.',
    'B': {'rank': ['mask_rec', 'full_entity_recall', 'partial_r', 'strict_r', 'strict_p'],
          'tie': 'V2', 'selection': 'Once per label using Dev, before B evaluation; exploratory.'},
    'C': {'base': 'V2; original prediction order',
          'same_key': 'Deduplicate', 'contained': 'Keep V2, including different-label containment',
          'disjoint': 'Add only after existing-rule validation',
          'extension': 'Replace one same-label contained seed after existing-rule validation',
          'partial_or_cross_label': 'Reject',
          'evidence': 'Saved rule prediction plus unchanged source regex/context checks; no Kiwi rerun',
          'gold_access': False},
    'evaluation': 'Unchanged run_compare; half-open spans; coverage unions ignore labels.',
    'policy_revision': 'fixed_v1_no_post_evaluation_tuning',
}


def coverage(entities):
    return {i for e in entities for i in range(e['start'], e['end'])}


def contains(outer, inner):
    return outer['start'] <= inner['start'] < inner['end'] <= outer['end']


def rule_checks(path):
    """Extract only existing regex assignments; never import/initialize Kiwi."""
    tree = ast.parse(path.read_text(encoding='utf-8'))
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'RuleDetector')
    init = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == '__init__')
    class Patterns:
        pass
    obj = Patterns()
    namespace = dict(re=re, self=obj)
    for node in init.body:
        if isinstance(node, ast.Assign) and all(isinstance(t, (ast.Name, ast.Tuple)) for t in node.targets):
            names = {n.id for t in node.targets for n in ast.walk(t) if isinstance(n, ast.Name)}
            if names and names <= {'left', 'right'}:
                exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), 'exec'), namespace)
        elif (isinstance(node, ast.Assign) and len(node.targets) == 1
              and isinstance(node.targets[0], ast.Attribute)
              and node.targets[0].attr.startswith('_')
              and isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Attribute)
              and isinstance(node.value.func.value, ast.Name)
              and node.value.func.value.id == 're' and node.value.func.attr == 'compile'):
            exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), 'exec'), namespace)
    return obj


def valid_rule(text, candidate, patterns):
    """Recheck saved candidate evidence, never search for new rule candidates."""
    a, b, label = candidate['start'], candidate['end'], candidate['label']
    value = text[a:b]
    before = text[max(0, a - 48):a].rsplit('\n', 1)[-1].rsplit('\r', 1)[-1]
    if label == 'person':
        after = text[b:b + 24].split('\n', 1)[0].split('\r', 1)[0]
        return bool(patterns._name_chars.fullmatch(value)
                    and not (a and '\uac00' <= text[a - 1] <= '\ud7a3')
                    and not patterns._nonperson_suffix.search(value)
                    and not patterns._nonperson_after.match(after)
                    and (patterns._name_before.search(before) or patterns._name_after.match(after)))
    attr = {'phone_number': '_phone', 'email': '_email', 'address': '_address',
            'account_number': '_account', 'card_number': '_card',
            'resident_registration_number': '_rrn'}[label]
    match = getattr(patterns, attr).match(text, a)
    if not match or match.end() != b:
        return False
    if label == 'account_number' and not patterns._account_context.search(before):
        return False
    if label == 'card_number' and not patterns._card_context.search(before):
        return False
    if label == 'resident_registration_number':
        try:
            date((1900 if value[7] in '12' else 2000) + int(value[:2]), int(value[2:4]), int(value[4:6]))
        except ValueError:
            return False
    return True


def selective(text, v2, rules, patterns):
    """Gold/IDs unavailable here. Preserve all original coverage, fixed order."""
    result = [dict(p) for p in v2]
    audit = []
    for candidate in rules:
        hits = [(i, p) for i, p in enumerate(result) if scorer.overlap(candidate, p)]
        if any(scorer.key(candidate) == scorer.key(p) for _, p in hits):
            action = 'duplicate'
        elif any(contains(p, candidate) for _, p in hits):
            action = 'contained' if all(p['label'] == candidate['label'] for _, p in hits) else 'contained_conflict'
        elif not valid_rule(text, candidate, patterns):
            action = 'evidence_rejected'
        elif not hits:
            result.append(dict(candidate)); action = 'add'
        elif (len(hits) == 1 and hits[0][1]['label'] == candidate['label']
              and contains(candidate, hits[0][1])):
            result[hits[0][0]] = dict(candidate); action = 'expand'
        else:
            action = 'overlap_conflict'
        audit.append(dict(label=candidate['label'], start=candidate['start'], end=candidate['end'],
                          action=action, overlaps=[dict(label=p['label'], start=p['start'], end=p['end']) for _, p in hits]))
    if not coverage(v2) <= coverage(result):
        raise ValueError('Selective combination lost V2 coverage')
    return result, audit


def choose_labels(docs, sources):
    statistics = {name: evaluate(docs, predictions, sources['V2']) for name, predictions in sources.items()}
    selection, evidence = {}, {}
    for label in scorer.LABELS:
        ranks = {}
        evidence[label] = {}
        for name, predictions in sources.items():
            c = statistics[name]['per_label'][label]
            full = sum(set(range(g['start'], g['end'])) <= coverage(ps)
                       for d, ps in zip(docs, predictions) for g in d['gold'] if g['label'] == label)
            ratio = lambda a, b: Fraction(a, b) if b else Fraction(0)
            ranks[name] = (ratio(c.get('chars_masked', 0), c.get('chars', 0)), ratio(full, c.get('gold', 0)),
                           ratio(c.get('partial_tp', 0), c.get('gold', 0)), ratio(c.get('strict_tp', 0), c.get('gold', 0)),
                           ratio(c.get('strict_tp', 0), c.get('pred', 0)))
            evidence[label][name] = dict(rank=[str(v) for v in ranks[name]], full_entities=full,
                                         metrics=c, errors=statistics[name]['errors'][label])
        selection[label] = 'Rule' if ranks['Rule'] > ranks['V2'] else 'V2'
    return selection, evidence


def describe(docs, predictions, v2, alternate=None):
    result = evaluate(docs, predictions, v2)
    totals = Counter()
    per_label = {label: Counter() for label in scorer.LABELS}
    losses = []
    for d, ps, old in zip(docs, predictions, v2):
        now, before, gold = coverage(ps), coverage(old), coverage(d['gold'])
        totals.update(predictions=len(ps), added_gold_chars=len((now - before) & gold),
                      lost_gold_chars=len((before - now) & gold), lost_coverage_chars=len(before - now),
                      non_gold_chars=len(now - gold), all_masked_chars=len(now))
        for g in d['gold']:
            chars = set(range(g['start'], g['end'])); full = chars <= now; was = chars <= before
            totals['full_entities'] += full
            totals['newly_full_entities'] += full and not was
            totals['entities_with_added_protection'] += bool(chars & (now - before))
            totals['lost_full_entities'] += was and not full
            per_label[g['label']]['full_entities'] += full
            per_label[g['label']]['gold'] += 1
            if chars & (before - now):
                losses.append(dict(id=d['id'], label=g['label'], start=g['start'], end=g['end'],
                                   lost_chars=len(chars & (before - now))))
    result.update(coverage=dict(totals), per_label_protection=per_label, lost_protection=losses)
    if alternate is not None:
        opportunity = Counter()
        for d, chosen, available in zip(docs, predictions, alternate):
            c, a = coverage(chosen), coverage(available)
            for g in d['gold']:
                chars = set(range(g['start'], g['end']))
                if chars - c:
                    opportunity['entities_with_alternate_added_chars'] += bool((chars - c) & a)
                    opportunity['alternate_added_chars'] += len((chars - c) & a)
                    opportunity['could_become_fully_protected'] += chars <= (c | a)
        result['discarded_alternate_opportunity'] = dict(opportunity)
    return result


class Contracts(unittest.TestCase):
    def test_relations_and_gold_free_evidence(self):
        patterns = rule_checks(ROOT / 'detection/rule/detector.py')
        text = '전화: 0212345678'
        make = lambda s, e, label='phone_number': dict(start=s, end=e, label=label)
        whole = make(4, 14)
        for rules, expected, reason in (([whole], [whole], 'duplicate'),
                                        ([make(4, 10)], [whole], 'contained')):
            out, audit = selective(text, [whole], rules, patterns)
            self.assertEqual(out, expected); self.assertEqual(audit[0]['action'], reason)
        for base, expected_action in (([], 'add'), ([make(6, 14)], 'expand'),
                                      ([make(4, 8, 'account_number')], 'overlap_conflict'),
                                      ([make(2, 8)], 'overlap_conflict'),
                                      ([make(4, 8), make(10, 14)], 'overlap_conflict')):
            out, audit = selective(text, base, [whole], patterns)
            self.assertEqual(audit[0]['action'], expected_action)
            self.assertTrue(coverage(base) <= coverage(out))
        out, audit = selective(text, [], [make(4, 8)], patterns)
        self.assertEqual(out, []); self.assertEqual(audit[0]['action'], 'evidence_rejected')

    def test_name_needs_original_context_and_exclusions(self):
        patterns = rule_checks(ROOT / 'detection/rule/detector.py')
        for text, accept in (('가상인 고객', True), ('가상인 카드', False), ('가상인 일반문장', False)):
            candidate = dict(start=0, end=3, label='person')
            self.assertEqual(bool(selective(text, [], [candidate], patterns)[0]), accept)

    def test_selection_tie_and_recall_priority(self):
        gold = [dict(start=0, end=3, label='person', text='가상인')]
        docs = [dict(id='synthetic', text='가상인 고객', gold=gold)]
        selection, _ = choose_labels(docs, {'V2': [[gold[0]]], 'Rule': [[gold[0]]]})
        self.assertEqual(selection['person'], 'V2')
        selection, _ = choose_labels(docs, {'V2': [[]], 'Rule': [[gold[0]]]})
        self.assertEqual(selection['person'], 'Rule')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=private_output, default=LOCAL_RESULTS / 'ensemble_experiment/dev')
    parser.add_argument('--self-test', action='store_true')
    args = parser.parse_args()
    if args.self_test:
        suite = unittest.defaultTestLoader.loadTestsFromTestCase(Contracts)
        if not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful():
            raise SystemExit(1)
        return
    if args.out.exists():
        parser.error('Output already exists; choose a new directory')
    if not args.out.resolve().is_relative_to(LOCAL_RESULTS.resolve()):
        parser.error('Output must be inside local_results/')
    folders = [LOCAL_RESULTS / name / 'dev' for name in ('token_cls_baseline', 'token_cls_improved_v1',
                                                        'token_cls_improved_v2', 'rule_baseline')]
    if any(args.out.resolve().is_relative_to(folder.resolve()) for folder in folders):
        parser.error('Output must be separate from existing runs')
    data = ROOT / 'data/dev/pii_synth_finance_dev.jsonl'
    rule_code = ROOT / 'detection/rule/detector.py'
    preserved_paths = [data, rule_code, ROOT / 'run_compare.py', ROOT / 'eval/cross_analysis.py',
                       *(ROOT / 'detection/token_cls' / name for name in
                         ('detector.py', 'run.py', 'postprocess.py', 'postprocess_v2.py', 'replay_improved.py'))]
    for folder in folders:
        preserved_paths.extend(p for p in folder.rglob('*') if p.is_file())
    preserved = {str(p): digest(p) for p in preserved_paths}
    for folder in (folders[0], folders[2]):
        meta = json.loads((folder / 'metadata.json').read_text(encoding='utf-8'))
        if meta['status'] != 'complete':
            raise ValueError('Incomplete source run')
    base_meta = json.loads((folders[0] / 'metadata.json').read_text(encoding='utf-8'))
    if base_meta['input_sha256'] != digest(data):
        raise ValueError('Dev input hash mismatch')
    docs = scorer.load_gold(data); ids = {d['id'] for d in docs}
    sources = {}
    for name, path in (('V2', folders[2] / 'predictions.jsonl'), ('Rule', folders[3] / 'rules_dev.jsonl')):
        rows = scorer.load_jsonl(path)
        if len(rows) != len(ids) or {r['id'] for r in rows} != ids or len(ids) != len(docs):
            raise ValueError('Duplicate/missing/extra IDs')
        predictions, _ = scorer.load_pred(path, docs)
        if sum(map(len, predictions)) != sum(len(r['entities']) for r in rows):
            raise ValueError('Invalid source spans')
        for d, ps in zip(docs, predictions):
            if len(ps) != len({scorer.key(p) for p in ps}) or any(p['label'] not in scorer.LABELS for p in ps):
                raise ValueError('Duplicate or unknown-label predictions')
        sources[name] = predictions
    # Policy is already fixed. Select B exactly once; C never sees gold/IDs.
    selection, selection_evidence = choose_labels(docs, sources)
    patterns = rule_checks(rule_code)
    variants = dict(A=sources['V2'], B=[], C=[])
    alternate = []; audit = []
    for d, v2, rule in zip(docs, sources['V2'], sources['Rule']):
        combined = []; other = []
        for label in scorer.LABELS:
            chosen = v2 if selection[label] == 'V2' else rule
            unused = rule if selection[label] == 'V2' else v2
            combined.extend(dict(p) for p in chosen if p['label'] == label)
            other.extend(dict(p) for p in unused if p['label'] == label)
        variants['B'].append(combined); alternate.append(other)
        result, events = selective(d['text'], v2, rule, patterns)
        variants['C'].append(result)
        audit.extend(dict(id=d['id'], **event) for event in events)
    evaluation = {name: describe(docs, ps, sources['V2'], alternate if name == 'B' else None)
                  for name, ps in variants.items()}
    if any(digest(Path(p)) != h for p, h in preserved.items()):
        raise ValueError('Existing sources changed during experiment')
    model_versions = dict(KoELECTRA=base_meta['model'], revision=base_meta['model_revision'],
                          postprocess='financial_context_v2',
                          rule='Regex + Kiwi saved baseline; source SHA256 in preserved_inputs',
                          rule_input_provenance='Historical input hash unavailable; IDs/spans checked, Kiwi not rerun')
    args.out.mkdir(parents=True, exist_ok=False)
    for name, ps in variants.items():
        folder = args.out / name; folder.mkdir(exist_ok=False)
        with (folder / 'predictions.jsonl').open('x', encoding='utf-8') as handle:
            for d, entities in zip(docs, ps):
                handle.write(json.dumps(dict(id=d['id'], entities=entities), ensure_ascii=False) + '\n')
        with (folder / 'evaluation.json').open('x', encoding='utf-8') as handle:
            json.dump(dict(experiment=name, model_versions=model_versions, policy=POLICY[name],
                           selection=selection if name == 'B' else None, **evaluation[name]), handle,
                      ensure_ascii=False, indent=2)
    payloads = {'policy.json': dict(conditions=POLICY, selected_models=selection,
                                   selection_evidence=selection_evidence),
                'metadata.json': dict(status='complete', documents=len(docs), gold_entities=sum(len(d['gold']) for d in docs),
                                      model_versions=model_versions, policy=POLICY, selected_models=selection,
                                      inference_rerun=False, preserved_inputs=preserved,
                                      experiment_sha256=digest(Path(__file__)),
                                      selective_actions=dict(Counter(r['action'] for r in audit)))}
    for filename, value in payloads.items():
        with (args.out / filename).open('x', encoding='utf-8') as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
    with (args.out / 'C/candidate_audit.jsonl').open('x', encoding='utf-8') as handle:
        for row in audit:
            handle.write(json.dumps(row) + '\n')
    print('SELECTED', json.dumps(selection))
    for name, values in evaluation.items():
        print(name, json.dumps(dict(overall=values['overall'], coverage=values['coverage'],
                                    new_spurious=len(values['new_spurious']),
                                    lost_exact=len(values['lost_baseline_exact']))))
    print('SELECTIVE_ACTIONS', json.dumps(payloads['metadata.json']['selective_actions']))
    print(f'Saved to {args.out}')


if __name__ == '__main__':
    main()
