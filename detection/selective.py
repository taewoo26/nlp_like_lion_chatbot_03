"""Shared frozen C policy for saved-prediction replay and live detection."""
import ast
from datetime import date
import re

import run_compare as scorer


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
