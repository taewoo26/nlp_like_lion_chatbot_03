"""Dev-only report using the existing scorer; regex is for error analysis only."""
import argparse
from result_paths import private_output
import csv
import json
import re
import statistics
from collections import Counter
from pathlib import Path

import run_compare as scoring


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--gold', type=Path, required=True)
    ap.add_argument('--run', type=private_output, required=True)
    args = ap.parse_args()
    docs = scoring.load_gold(args.gold)
    predictions, mean_ms = scoring.load_pred(args.run / 'predictions.jsonl', docs)
    source = scoring.load_jsonl(args.run / 'predictions.jsonl')
    assert len(source) == len(docs) == len({r['id'] for r in source})
    assert {r['id'] for r in source} == {d['id'] for d in docs}
    assert all(e['label'] in scoring.LABELS for r in source for e in r['entities'])
    metrics, counts = scoring.summarize(docs, predictions)
    out = private_output(args.run / 'analysis')
    out.mkdir(exist_ok=False)
    fields = ['label', 'gold', 'pred', 'strict_tp', 'partial_tp',
              'strict_p', 'strict_r', 'strict_f1', 'partial_p', 'partial_r', 'partial_f1']
    label_rows = []
    for lab in scoring.LABELS:
        c = counts[lab]
        label_rows.append([lab, c['gold'], c['pred'], c['strict_tp'], c['partial_tp'],
                           *scoring.prf(c['strict_tp'], c['pred'], c['gold']),
                           *scoring.prf(c['partial_tp'], c['pred'], c['gold'])])
    with (out / 'per_label_full.csv').open('x', encoding='utf-8-sig', newline='') as f:
        w = csv.writer(f); w.writerow(fields); w.writerows(label_rows)

    groups = {k: [] for k in ('spaced', 'josa_attached_actual', 'honorific_vocative_actual',
                              'josa_tag_document_person')}
    surname_rows = []
    # Used only after inference, never to correct predictions.
    josa = re.compile(r'^(?:으로|에게|에서|이랑|은|는|이|가|을|를|와|과|랑|도)(?=[\s,.!?]|$)')
    honorific = re.compile(r'^(?:님(?:께서|은|이)?|아)(?=[\s,.!?]|$)')
    surname = re.compile(r'(?<![가-힣])([가-힣])\s+(?:부장|대리|과장|차장|팀장|사원)(?:님)?')
    evidence = []
    for d, ps in zip(docs, predictions):
        for g in d['gold']:
            if g['label'] != 'person':
                continue
            same = [p for p in ps if p['label'] == 'person' and scoring.overlap(g, p)]
            exact = any((p['start'], p['end']) == (g['start'], g['end']) for p in same)
            typ = 'exact' if exact else 'boundary' if same else 'label_error' if any(
                scoring.overlap(g, p) for p in ps) else 'miss'
            selected = []
            if g.get('form') == 'spaced': selected.append('spaced')
            suffix = d['text'][g['end']:]
            if josa.match(suffix): selected.append('josa_attached_actual')
            if honorific.match(suffix): selected.append('honorific_vocative_actual')
            if 'josa_attached' in d.get('hard_cases', []): selected.append('josa_tag_document_person')
            for k in selected:
                row = dict(group=k, id=d['id'], gold=g['text'], start=g['start'], end=g['end'],
                           result=typ, predicted=[p['text'] for p in same],
                           context=scoring.ctx(d['text'], g['start'], g['end']))
                groups[k].append(row); evidence.append(row)
        for m in surname.finditer(d['text']):
            candidate = dict(start=m.start(), end=m.end())
            if any(scoring.overlap(candidate, g) for g in d['gold']):
                continue
            hit = [p for p in ps if p['label'] == 'person' and scoring.overlap(candidate, p)]
            surname_rows.append(dict(id=d['id'], expression=m.group(), start=m.start(), end=m.end(),
                                     false_positive=bool(hit), predicted=[p['text'] for p in hit]))
    group_summary = {k: dict(total=len(rows), **dict(Counter(r['result'] for r in rows)))
                     for k, rows in groups.items()}
    times = [r['ms'] for r in source]
    raw = scoring.load_jsonl(args.run / 'raw_predictions.jsonl')
    extra = Counter(e['label'] for r in raw for e in r['unmapped_entities'])
    merged = [dict(id=d['id'], prediction=p['text'],
                   gold=[g['text'] for g in d['gold'] if g['label'] == 'person' and scoring.overlap(g, p)])
              for d, ps in zip(docs, predictions) for p in ps if p['label'] == 'person'
              and sum(g['label'] == 'person' and scoring.overlap(g, p) for g in d['gold']) > 1]
    report = dict(metrics=metrics, mean_ms=mean_ms, median_ms=statistics.median(times),
                  p95_ms=sorted(times)[max(0, int(len(times)*.95+.999)-1)],
                  total_inference_ms=sum(times), groups=group_summary,
                  surname_title=dict(occurrences=len(surname_rows),
                                     false_positives=sum(r['false_positive'] for r in surname_rows)),
                  unmapped_entities=dict(extra), window_counts=dict(Counter(r['windows'] for r in raw)),
                  merged_person_predictions=merged)
    for filename, value in [('report.json', report), ('person_evidence.json', evidence),
                            ('surname_title_evidence.json', surname_rows)]:
        (out / filename).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    lines = ['# Token classification dev baseline', '',
             'Frozen pretrained model; no training, suffix removal, regex supplementation or confidence filtering.',
             '', '100 dev documents only. Strict/partial metrics reuse run_compare.py.', '',
             '|label|gold|pred|strict P|strict R|strict F1|partial P|partial R|partial F1|',
             '|---|---:|---:|---:|---:|---:|---:|---:|---:|']
    for r in label_rows:
        lines.append('|'+'|'.join([str(x) for x in r[:3]]+[f'{x:.4f}' for x in r[5:]])+'|')
    lines += ['', 'Overall: '+json.dumps(metrics), '',
              f'CPU, 1 thread, no warmup. Mean {mean_ms:.2f} ms/document; median {statistics.median(times):.2f} ms; '
              f'p95 {report["p95_ms"]:.2f} ms. Loading and file I/O excluded.', '',
              '## Person error groups', '',
              '|group|gold|exact|boundary|label error|miss|', '|---|---:|---:|---:|---:|---:|']
    for k, c in group_summary.items():
        lines.append('|'+k+'|'+'|'.join(str(c.get(x, 0)) for x in
                     ('total','exact','boundary','label_error','miss'))+'|')
    lines += ['', 'Actual suffix groups inspect characters immediately after the gold span. '
              'The josa tag group instead counts ALL person entities in tagged documents, matching the existing hard-case table. '
              'Groups can overlap; these are diagnostic recalls, not independent P/R scores.', '',
              f'{len(merged)} person predictions overlap multiple person gold spans. '
              'Per-gold overlap diagnostics may count each covered name as found; the main partial metric uses 1:1 matching '
              'and penalizes these merges. Do not interpret hard-case overlap recall as main partial recall.', '',
              f'Surname + title: {len(surname_rows)} unlabeled occurrences; '
              f'{report["surname_title"]["false_positives"]} overlaps with predicted person spans.', '',
              '## Error examples', '']
    for k, rows in groups.items():
        lines.append(f'### {k}')
        for row in [r for r in rows if r['result'] != 'exact'][:5]:
            lines.append(f'- {row["id"]}: {row["gold"]} → {row["predicted"]} ({row["result"]}); {row["context"]}')
    lines += ['', '### Surname + title']
    for row in surname_rows:
        if row['false_positive']:
            lines.append(f'- {row["id"]}: {row["expression"]} → {row["predicted"]}')
    lines += ['', '## Extra model labels', '', json.dumps(dict(extra), ensure_ascii=False), '',
              'All raw entities and fused BIO tokens are in raw_predictions.jsonl. '
              'Unmapped labels are excluded from the scored file, including mask_rec.']
    (out / 'REPORT.md').write_text('\n'.join(lines)+'\n', encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
