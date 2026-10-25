"""Read-only baseline comparison. Never imports or runs a detector."""
import argparse
from result_paths import private_output
import csv
import hashlib
import json
import math
import re
import statistics
from collections import Counter, defaultdict
from pathlib import Path

import run_compare as scorer

CATEGORIES = ('BOTH_CORRECT', 'RULE_ONLY', 'KOELECTRA_ONLY', 'BOTH_WRONG')
CAUSES = ('CORRECT', 'COMPLETE_MISS', 'BOUNDARY_ERROR', 'LABEL_ERROR', 'ADJACENT_MERGE')
CAUSE_KO = dict(zip(CAUSES, ('정확', '완전 미탐', '경계 오류', '라벨 오류', '인접 개체 병합')))
JOSA = re.compile(r'^(?:으로|에게|에서|이랑|은|는|이|가|을|를|와|과|랑|도)(?=[\s,.!?]|$)')
HONORIFIC = re.compile(r'^(?:님(?:께서|은|이)?|아)(?=[\s,.!?]|$)')
SURNAME = re.compile(r'(?<![가-힣])([가-힣])\s+(?:부장|대리|과장|차장|팀장|사원)(?:님)?')


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def pairs(gold, predictions, strict):
    """Same gold/prediction order as scorer.count; predictions consumed once."""
    matched, used = {}, set()
    for lab in scorer.LABELS:
        for i, g in enumerate(gold):
            if g['label'] != lab:
                continue
            for j, p in enumerate(predictions):
                if j in used or p['label'] != lab:
                    continue
                if (scorer.key(g) == scorer.key(p)) if strict else scorer.overlap(g, p):
                    matched[i] = j
                    used.add(j)
                    break
    assert len(matched) == len(set(matched.values()))
    return matched


def merged_indices(gold, predictions):
    """An overlapping prediction bridges >=2 adjacent gold entities.

    Adjacency is in the original document's gold order, regardless of label.
    This is evidence, not a multi-gold TP assignment.
    """
    order = sorted(range(len(gold)), key=lambda i: (gold[i]['start'], gold[i]['end']))
    position = {i: k for k, i in enumerate(order)}
    out = {}
    for j, p in enumerate(predictions):
        hits = [i for i in order if scorer.overlap(gold[i], p)]
        if len(hits) >= 2 and all(position[b] == position[a]+1 for a, b in zip(hits, hits[1:])):
            out[j] = hits
    return out


def cause(i, gold, predictions, strict, merges):
    if i in strict:
        return 'CORRECT'
    overlapping = [j for j, p in enumerate(predictions) if scorer.overlap(gold[i], p)]
    if any(i in hits for hits in merges.values()):
        return 'ADJACENT_MERGE'
    if any(predictions[j]['label'] == gold[i]['label'] for j in overlapping):
        return 'BOUNDARY_ERROR'
    return 'LABEL_ERROR' if overlapping else 'COMPLETE_MISS'


def write_csv(path, rows, fields=None):
    fields = fields or list(rows[0])
    with path.open('x', encoding='utf-8-sig', newline='') as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for row in rows:
            w.writerow({k: json.dumps(v, ensure_ascii=False) if isinstance(v, (list, dict)) else v
                        for k, v in row.items()})


def table(rows, fields):
    def cell(v):
        return str(v).replace('|', '\\|').replace('\n', ' ↵ ')
    return ['|'+'|'.join(fields)+'|', '|'+'|'.join('---' for _ in fields)+'|'] + [
        '|'+'|'.join(cell(r[k]) for k in fields)+'|' for r in rows]


def discover(root):
    # Intentionally never enumerate data/test or data/holdout.
    gold_candidates = []
    for path in sorted((root / 'data/dev').glob('*.jsonl')):
        docs = scorer.load_jsonl(path)
        if len(docs) == 100 and sum(len(d.get('entities', [])) for d in docs) == 548:
            gold_candidates.append(path)
    if len(gold_candidates) != 1:
        raise ValueError(f'Ambiguous dev gold: {gold_candidates}')
    selected, audit = {}, []
    for path in sorted((root / 'local_results').glob('*/dev/**/*.jsonl')):
        if 'comparison' in path.parts or 'raw' in path.name:
            continue
        rows = scorer.load_jsonl(path)
        if len(rows) != 100 or not all('id' in r and 'entities' in r for r in rows):
            continue
        system = 'rule' if 'rule_baseline' in path.parts else (
            'koelectra' if 'token_cls_baseline' in path.parts else None)
        audit.append(dict(path=str(path.relative_to(root)), documents=len(rows), system=system))
        if system:
            if system in selected:
                raise ValueError(f'Multiple prediction files for {system}')
            selected[system] = path
    if set(selected) != {'rule', 'koelectra'}:
        raise ValueError(f'Missing baseline predictions: {selected}')
    return gold_candidates[0], selected, audit


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--out', type=private_output, default=Path('local_results/comparison/dev'))
    args = ap.parse_args()
    root = Path.cwd()
    if args.out.exists():
        ap.error('Output exists; use a new output directory')
    gold_path, paths, discovered = discover(root)
    protected = [p for folder in ('local_results/rule_baseline', 'local_results/token_cls_baseline',
                                 'detection/rule', 'detection/token_cls', 'logs')
                 for p in (root / folder).rglob('*') if p.is_file() and '__pycache__' not in p.parts]
    protected += [root / p for p in ('run_compare.py', 'detection/base.py', 'label_guide_fixed.md', 'README.md')]
    before = {str(p.relative_to(root)): digest(p) for p in protected}
    docs = scorer.load_gold(gold_path)
    assert len(docs) == len({d['id'] for d in docs}) == 100
    assert sum(len(d['gold']) for d in docs) == 548
    assert all(len(d['gold']) == len({scorer.key(g) for g in d['gold']}) for d in docs)
    assert all(e['label'] in scorer.LABELS for d in docs for e in d['entities'])
    assert all(d.get('split', 'dev') == 'dev' for d in docs)
    pred, times, saved_scores = {}, {}, {}
    for system, path in paths.items():
        rows = scorer.load_jsonl(path)
        assert len(rows) == len({r['id'] for r in rows}) == 100
        by_id = {r['id']: r for r in rows}
        assert set(by_id) == {d['id'] for d in docs}
        for d in docs:
            entities = by_id[d['id']]['entities']
            assert len(entities) == len({scorer.key(e) for e in entities})
            for e in entities:
                assert e['label'] in scorer.LABELS
                assert type(e['start']) is int and type(e['end']) is int
                assert 0 <= e['start'] < e['end'] <= len(d['text'])
                assert e.get('text', d['text'][e['start']:e['end']]) == d['text'][e['start']:e['end']]
        pred[system], _ = scorer.load_pred(path, docs)
        times[system] = [by_id[d['id']]['ms'] for d in docs]
        assert all(math.isfinite(t) and t >= 0 for t in times[system])
        with (path.parent / 'eval/summary.csv').open(encoding='utf-8-sig') as f:
            saved_scores[system] = next(csv.DictReader(f))
    metadata = json.loads((paths['koelectra'].parent / 'metadata.json').read_text(encoding='utf-8'))
    assert metadata['input_sha256'] == digest(gold_path)
    assert metadata['documents'] == 100 and metadata['status'] == 'complete'

    cross, prediction_errors, mergers, surnames = [], [], [], []
    group_indices = defaultdict(list)
    strict_total, partial_total = Counter(), Counter()
    for n, d in enumerate(docs):
        G = d['gold']
        ps = {s: pred[s][n] for s in paths}
        strict = {s: pairs(G, ps[s], True) for s in paths}
        partial = {s: pairs(G, ps[s], False) for s in paths}
        merge = {s: merged_indices(G, ps[s]) for s in paths}
        for s in paths:
            strict_total[s] += len(strict[s]); partial_total[s] += len(partial[s])
            for j, hits in merge[s].items():
                mergers.append(dict(system=s, doc_id=d['id'], prediction=ps[s][j],
                                    gold=[G[i] for i in hits], affected_gold=len(hits)))
            for j, p in enumerate(ps[s]):
                overlap_gold = [g for g in G if scorer.overlap(g, p)]
                if j in strict[s].values() and j in partial[s].values():
                    continue
                typ = ('CORRECT' if j in strict[s].values() else
                       'ADJACENT_MERGE' if j in merge[s] else
                       'BOUNDARY_ERROR' if any(g['label'] == p['label'] for g in overlap_gold) else
                       'LABEL_ERROR' if overlap_gold else 'SPURIOUS_NO_OVERLAP')
                prediction_errors.append(dict(system=s, doc_id=d['id'], prediction_index=j,
                    strict_fp=j not in strict[s].values(), partial_fp=j not in partial[s].values(), type=typ,
                    prediction=p, overlapping_gold=overlap_gold,
                    context=scorer.ctx(d['text'], p['start'], p['end'])))
        for i, g in enumerate(G):
            r, k = i in strict['rule'], i in strict['koelectra']
            category = ('BOTH_CORRECT' if r and k else 'RULE_ONLY' if r else
                        'KOELECTRA_ONLY' if k else 'BOTH_WRONG')
            row = dict(entity_id=f'{d["id"]}:G{i:03}', doc_id=d['id'], gold_index=i,
                       label=g['label'], start=g['start'], end=g['end'], gold_text=g['text'],
                       form=g.get('form', ''), category=category,
                       context=scorer.ctx(d['text'], g['start'], g['end']))
            for s in paths:
                row[s+'_strict'] = i in strict[s]
                row[s+'_partial'] = i in partial[s]
                row[s+'_strict_prediction_index'] = strict[s].get(i, '')
                row[s+'_partial_prediction_index'] = partial[s].get(i, '')
                row[s+'_cause'] = cause(i, G, ps[s], strict[s], merge[s])
                row[s+'_predictions'] = [p for p in ps[s] if scorer.overlap(g, p)]
                row[s+'_label_error_overlap'] = (i not in strict[s] and any(
                    p['label'] != g['label'] for p in row[s+'_predictions']))
            index = len(cross)
            if g['label'] == 'person':
                group_indices['person_all'].append(index)
                group_indices['person_form_'+g.get('form', 'unknown')].append(index)
                suffix = d['text'][g['end']:]
                if JOSA.match(suffix): group_indices['person_actual_josa'].append(index)
                if HONORIFIC.match(suffix): group_indices['person_honorific_vocative'].append(index)
                if 'josa_attached' in d.get('hard_cases', []):
                    group_indices['person_in_josa_tagged_documents'].append(index)
                if any(any(i in hit for hit in merge[s].values()) for s in paths):
                    group_indices['person_merge_affected'].append(index)
            cross.append(row)
        for m in SURNAME.finditer(d['text']):
            target = dict(start=m.start(), end=m.end())
            if any(scorer.overlap(target, g) for g in G):
                continue
            row = dict(doc_id=d['id'], start=m.start(), end=m.end(), expression=m.group())
            for s in paths:
                row[s+'_predictions'] = [p for p in ps[s] if p['label'] == 'person' and scorer.overlap(target, p)]
                row[s+'_fp'] = bool(row[s+'_predictions'])
            surnames.append(row)

    overall, per_label = [], []
    for s in paths:
        summary, counts = scorer.summarize(docs, pred[s])
        assert strict_total[s] == sum(c['strict_tp'] for c in counts.values())
        assert partial_total[s] == sum(c['partial_tp'] for c in counts.values())
        for metric, value in summary.items():
            assert abs(value - float(saved_scores[s][metric])) <= 0.000051, (s, metric)
        errors = [p for p in prediction_errors if p['system'] == s]
        overall.append(dict(system=s, gold=548, predictions=sum(c['pred'] for c in counts.values()),
            strict_tp=strict_total[s], partial_tp=partial_total[s], **summary,
            strict_fp=sum(p['strict_fp'] for p in errors), partial_fp=sum(p['partial_fp'] for p in errors),
            spurious_no_overlap=sum(p['type'] == 'SPURIOUS_NO_OVERLAP' for p in errors),
            mean_ms=statistics.mean(times[s]), median_ms=statistics.median(times[s]),
            p95_ms=sorted(times[s])[94]))
        assert overall[-1]['predictions'] - partial_total[s] == overall[-1]['partial_fp']
        for lab in scorer.LABELS:
            c = counts[lab]
            a = scorer.prf(c['strict_tp'], c['pred'], c['gold'])
            b = scorer.prf(c['partial_tp'], c['pred'], c['gold'])
            per_label.append(dict(system=s, label=lab, gold=c['gold'], predictions=c['pred'],
                strict_tp=c['strict_tp'], partial_tp=c['partial_tp'], strict_p=a[0], strict_r=a[1],
                strict_f1=a[2], partial_p=b[0], partial_r=b[1], partial_f1=b[2]))
    cross_summary = []
    for lab in ('ALL', *scorer.LABELS):
        rows = [r for r in cross if lab == 'ALL' or r['label'] == lab]
        c = Counter(r['category'] for r in rows)
        assert sum(c.values()) == len(rows)
        row = dict(label=lab, gold=len(rows))
        for category in CATEGORIES:
            row[category] = c[category]; row[category+'_ratio'] = c[category] / len(rows)
        row['oracle_tp'] = len(rows) - c['BOTH_WRONG']
        row['oracle_recall'] = row['oracle_tp'] / len(rows)
        cross_summary.append(row)
    assert len(cross) == cross_summary[0]['gold'] == 548
    assert sum(r['gold'] for r in cross_summary[1:]) == 548
    causes = [dict(system=s, label=lab, cause=typ,
                   count=sum(r[s+'_cause'] == typ for r in cross if lab == 'ALL' or r['label'] == lab))
              for s in paths for lab in ('ALL', *scorer.LABELS) for typ in CAUSES]
    for s in paths:
        assert sum(r['count'] for r in causes if r['system']==s and r['label']=='ALL') == 548
    person_groups = []
    for group, indices in group_indices.items():
        row = dict(group=group, gold=len(indices))
        for s in paths:
            row[s+'_strict_tp'] = sum(cross[i][s+'_strict'] for i in indices)
            row[s+'_partial_tp'] = sum(cross[i][s+'_partial'] for i in indices)
            for typ in CAUSES[1:]:
                row[s+'_'+typ] = sum(cross[i][s+'_cause'] == typ for i in indices)
        person_groups.append(row)
    assert len(surnames) == 17
    after = {str(p.relative_to(root)): digest(p) for p in protected}
    assert before == after, 'A protected baseline artifact changed'
    provenance = dict(gold=str(gold_path.relative_to(root)), gold_sha256=digest(gold_path),
        discovered=discovered, source_predictions={s: dict(path=str(p.relative_to(root)), sha256=digest(p))
                                                   for s, p in paths.items()},
        scorer_sha256=digest(root/'run_compare.py'), documents=100, gold_entities=548,
        labels=scorer.LABELS, exact_duplicate_gold=0, exact_duplicate_predictions=0,
        missing_or_extra_documents=0, invalid_spans=0, source_summary_metrics_reproduced=True,
        koelectra_gold_hash_verified=True,
        rule_gold_hash_verified=False,
        rule_provenance_limitation='규칙 실행 당시 입력 hash 메타데이터가 없어 과거 원문 동일성은 독립 증명 불가. 현재 dev의 ID·원문 slice 정합성과 기존 집계 재현을 확인함.',
        protected_unchanged=True, protected_sha256=before)
    out = args.out
    out.mkdir(parents=True, exist_ok=False)
    for name, rows in [('overall_metrics.csv', overall), ('per_label_metrics.csv', per_label),
                       ('strict_cross_entities.csv', cross), ('strict_cross_summary.csv', cross_summary),
                       ('gold_error_causes.csv', causes), ('prediction_errors.csv', prediction_errors),
                       ('merged_predictions.csv', mergers), ('person_groups.csv', person_groups),
                       ('surname_title_cases.csv', surnames)]:
        write_csv(out/name, rows)
    examples = []
    for category in CATEGORIES[1:]:
        selected = [r for r in cross if r['category'] == category]
        if category == 'RULE_ONLY':
            examples.extend(selected)
            continue
        for lab in scorer.LABELS:
            examples.extend([r for r in selected if r['label'] == lab][:2])
    for s in paths:
        for typ in CAUSES[1:]:
            examples.extend([r for r in cross if r[s+'_cause'] == typ][:2])
    for group in ('person_form_spaced', 'person_actual_josa', 'person_merge_affected'):
        examples.extend([cross[i] for i in group_indices[group]][:2])
    examples = list({r['entity_id']: r for r in examples}.values())
    write_csv(out/'representative_errors.csv', examples)
    fp_examples = []
    for s in paths:
        for typ in ('SPURIOUS_NO_OVERLAP', 'BOUNDARY_ERROR', 'LABEL_ERROR', 'ADJACENT_MERGE'):
            fp_examples.extend([r for r in prediction_errors if r['system']==s and r['type']==typ][:3])
    write_csv(out/'representative_fp.csv', fp_examples)
    (out/'provenance.json').write_text(json.dumps(provenance, ensure_ascii=False, indent=2), encoding='utf-8')
    generate_report(out, overall, per_label, cross_summary, causes, person_groups,
                    cross, examples, mergers, surnames, paths)
    print(json.dumps(dict(cross_summary=cross_summary, overall=overall,
        person_groups=person_groups, causes=[r for r in causes if r['label']=='ALL'],
        surname_fp={s: sum(r[s+'_fp'] for r in surnames) for s in paths},
        merge_predictions=dict(Counter(r['system'] for r in mergers)),
        protected_unchanged=before == after), ensure_ascii=False, indent=2))


def generate_report(out, overall, per_label, cross_summary, causes, groups,
                    cross, examples, mergers, surnames, paths):
    pct = lambda x: f'{100*x:.2f}%'
    total = cross_summary[0]
    lines = ['# 규칙 기반·KoELECTRA Baseline 오류 교차 분석', '',
        '## 분석 범위와 입력 검증', '',
        '- dev 100문서, 정답 548개, 프로젝트 평가 라벨 7종만 사용했다. test·holdout은 탐색하거나 읽지 않았다.',
        *[f'- {s}: `{p.as_posix()}`' for s,p in paths.items()],
        '- 두 예측 파일은 자동 탐색했다. 문서 ID 집합, 누락·중복, 원문 slice, 라벨, 위치 유효성을 검증했다.',
        '- KoELECTRA 실행 메타데이터의 입력 SHA256은 현재 dev와 일치했다. 규칙 실행에는 당시 입력 hash 기록이 없어 과거 원문 동일성의 독립 증명에는 한계가 있다. 현재 dev와 두 예측의 정합성 및 기존 성능 CSV의 모든 지표 재현은 확인했다.',
        '- 기존 채점기 `run_compare.py`를 재사용했다. Strict는 라벨·start·end 완전 일치, Partial은 같은 라벨의 범위 겹침이다. 둘 다 문서별·개체별 1:1 대응이며 예측 인덱스를 한 번만 소비한다.',
        '- Partial은 기존 채점기와 동일한 정답·예측 순서의 greedy 매칭이다. 최대 이분매칭으로 기준을 바꾸지 않았다. Strict 중복 정답·예측은 0건이며 기존 Strict 집계와 일치한다.',
        '- 모든 기존 Baseline 코드·결과, 라벨 가이드, README, logs의 분석 전후 SHA256이 일치했다. 새 예측·후처리·Hybrid는 구현하거나 실행하지 않았다.', '',
        '## 전체 성능 비교', '']
    rows = [dict(모델=r['system'], **{k:pct(r[k]) for k in ('strict_p','strict_r','strict_f1','partial_p','partial_r','partial_f1','mask_rec')},
                 평균_ms=f'{r["mean_ms"]:.2f}') for r in overall]
    lines += table(rows, list(rows[0]))
    lines += ['', '속도는 기존 파일의 ms를 다시 집계한 값이며 이번에 추론을 재실행하지 않았다. 두 실행 모두 로딩·파일 I/O를 제외하고 warmup을 하지 않았다. 런타임 상태가 통제된 동시 벤치마크로 해석하지 않는다. 규칙의 D0001은 2,136.44ms로 평균에 크게 반영됐다. 중앙값은 규칙 4.83ms, KoELECTRA 68.20ms이고 p95는 각각 8.67ms, 108.48ms다.', '',
              '## Strict 오류 교차 분석', '', '각 비율의 분모는 해당 행의 정답 수다. 전체 네 범주의 합은 548개다.', '']
    rows = [dict(라벨=r['label'], 정답=r['gold'], **{k:f'{r[k]} ({pct(r[k+"_ratio"])})' for k in CATEGORIES},
                 Oracle=f'{r["oracle_tp"]}/{r["gold"]} ({pct(r["oracle_recall"])})') for r in cross_summary]
    lines += table(rows, list(rows[0]))
    lines += ['', f'BOTH_CORRECT + RULE_ONLY = {total["BOTH_CORRECT"] + total["RULE_ONLY"]}개로 규칙 Strict TP와 같고, '
              f'BOTH_CORRECT + KOELECTRA_ONLY = {total["BOTH_CORRECT"] + total["KOELECTRA_ONLY"]}개로 KoELECTRA Strict TP와 같다.', '',
              '## 라벨별 성능', '']
    rows = [dict(모델=r['system'], 라벨=r['label'], 정답=r['gold'], 예측=r['predictions'],
                 **{k:pct(r[k]) for k in ('strict_p','strict_r','strict_f1','partial_p','partial_r','partial_f1')}) for r in per_label]
    lines += table(rows, list(rows[0]))
    lines += ['', '## 실패 원인과 FP', '',
        '정답 개체별 실패 원인은 상호 배타적으로 분류했다. 우선순위는 정확 → 인접 개체 병합 → 같은 라벨 경계 오류 → 다른 라벨 겹침 → 완전 미탐이다. 병합은 하나의 예측이 원문 순서상 인접한 정답 2개 이상을 덮는 경우다. 병합에 딸린 여러 정답을 원인 분석에 표시하더라도 TP 매칭을 중복 부여하지 않는다.', '',
        '같은 라벨 예측과 겹치지만 다른 정답에 이미 Partial 매칭된 개체도 병합·경계 오류 근거를 남기며, 개체별 partial 열은 False로 유지한다. 주원인이 라벨 오류인 경우는 같은 라벨 예측이 없고 다른 라벨만 겹친 경우다. 완전 미탐은 7종 평가 파일에 어떤 라벨로도 겹치는 예측이 없는 경우이며 제외된 22종 원시 라벨은 판단에 사용하지 않았다.', '']
    rows = [dict(모델=s, **{CAUSE_KO[t]:next(r['count'] for r in causes if r['system']==s and r['label']=='ALL' and r['cause']==t) for t in CAUSES}) for s in paths]
    lines += table(rows, list(rows[0]))
    lines += ['', '보조 라벨 혼재 플래그: '+', '.join(f'{s} {sum(r[s+"_label_error_overlap"] for r in cross)}개 정답' for s in paths)+'. 주원인 분류와 중복될 수 있다. D0090 전화번호는 PHONE 조각들과 RRN 조각이 섞여 주원인은 경계 오류이지만 라벨 혼재도 표시한다. 따라서 주원인 라벨 오류 0개를 라벨 혼재 자체가 없었다는 뜻으로 해석하지 않는다.', '',
        'FP는 정답별 교차표와 분리했다. Strict FP는 Strict 매칭되지 않은 모든 예측이고, Partial FP는 Partial 매칭되지 않은 예측이다. 불필요한 추가 탐지는 정답과 어떤 라벨로도 겹치지 않는 예측(SPURIOUS_NO_OVERLAP)으로 별도 집계했다. 경계 오류·병합·라벨 오류 예측도 Strict FP에는 포함되므로 FP 수치를 정답 실패 수에 더하면 안 된다.', '']
    rows = [dict(모델=r['system'], Strict_FP=r['strict_fp'], Partial_FP=r['partial_fp'],
                 불필요한_추가탐지=r['spurious_no_overlap']) for r in overall]
    lines += table(rows, list(rows[0]))
    lines += ['', '## person 집중 분석', '',
        'actual_josa는 정답 직후 실제 조사가 붙은 22개다. 호칭·부름말은 별도 18개다. 기존 josa_attached 태그 문서의 전체 이름 121개와 구분했다. 그룹끼리는 중첩할 수 있다. 아래 Partial TP는 전체 문서 1:1 매칭 결과를 그대로 투영하므로 그룹별로 다시 매칭하거나 하나의 병합 예측을 여러 TP로 세지 않는다.', '']
    lines += table(groups, list(groups[0]))
    lines += ['', f'성+직함 비대상 17곳의 person 오탐: '+', '.join(f'{s} {sum(r[s+"_fp"] for r in surnames)}곳' for s in paths)+'. 이는 정답 548개에 없는 표현이므로 정답 교차표에 넣지 않았다.', '',
              '병합 예측 수: '+', '.join(f'{s} {sum(r["system"]==s for r in mergers)}개' for s in paths)+'. 원문 줄바꿈을 포함한 예측도 유지해 모델 출력의 경계 문제로 분석했다.', '',
              '## Oracle과 보완 가능성', '',
              f'둘 중 하나라도 Strict 정답인 개체는 {total["oracle_tp"]}개이며 Oracle Recall은 **{pct(total["oracle_recall"])}**다. '
              f'KoELECTRA 단독 대비 보완 가능한 정답은 RULE_ONLY {total["RULE_ONLY"]}개, Recall {pct(total["RULE_ONLY"]/548)}p에 해당한다.', '',
              'Oracle은 정답을 보고 어느 예측이 맞는지 선택하는 사후 상한이다. 실제 선택 규칙·모델을 구현한 Hybrid 성능이 아니며 Precision·F1·운영 속도를 보장하지 않는다. 단순 합집합은 오탐과 충돌도 추가하므로 이 Recall을 실제 Hybrid 성능으로 보고하지 않는다.', '',
              'RULE_ONLY 라벨 분포: '+json.dumps(dict(Counter(r['label'] for r in cross if r['category']=='RULE_ONLY')),ensure_ascii=False), '',
              '규칙 보완 사례의 공통 특징은 명확한 번호 형식 또는 제한된 문맥에서의 완전한 경계다. 계좌 5개는 모두 3-6-2 하이픈 번호이며 모델이 앞 두 자리를 빠뜨렸다. D0089는 뒤쪽 일부도 누락했다. 전화 3개는 모두 구분자 없는 번호로, D0043·D0086의 02 직통번호는 미탐이고 D0090의 010 번호는 전화·주민번호 조각으로 나뉘었다. D0085 주소는 조사 로를 포함했고, D0014의 정하은은 모델이 놓쳤다. 이름 1건의 보완을 일반적인 이름 탐지 우위로 확대 해석하지 않는다.', '',
              '## 다음 개선 우선순위', '',
              '1. **person 경계와 병합 진단**: BOTH_WRONG 27개가 모두 person이다. 모델 병합 예측 7개가 이름 14개에 영향을 주며 나머지 13개는 경계 오류다. 현재 규칙도 이 27개를 Strict로 해결하지 못하므로 단순 모델 선택만으로는 해결되지 않는다. 다음 실험에서는 줄바꿈을 넘는 병합, 띄어 쓴 이름의 성 누락, 부름말 경계를 각각 분리해 검증한다.',
              '2. **정형 라벨의 보완 후보 검증**: RULE_ONLY 10개 중 9개는 계좌·전화·주소다. 향후 충돌 해결과 라벨별 선택을 검토할 근거가 있지만 현재는 Oracle 상한만 계산했다. 새로운 선택 규칙의 Precision·mask_rec·추가 FP를 함께 검증해야 한다.',
              '3. **과탐과 프로젝트 정의 차이 검증**: 성+직함 17곳은 규칙 오탐 0, 모델 오탐 17이다. 모델의 불필요한 추가 탐지 36개에는 이 표현 외 일반어·상품명·대표번호 조각도 포함된다. 이번에는 필터를 적용하지 않았으며 향후 실험도 경계 개선과 대상 제외 효과를 분리해 측정해야 한다.',
              '4. **재현성·속도 기록 보강**: 다음 규칙 실행부터 입력 hash와 환경을 남기고, 로딩·첫 문서 초기화·정상 상태 추론을 나눠 측정한다. 현재 파일의 지연시간은 그대로 보존했다.', '',
              '## 대표 오류 사례', '',
              '예측에는 해당 정답과 겹친 모든 예측을 라벨·위치와 함께 기재했다. 빈 목록은 해당 위치에 겹치는 예측이 없다는 뜻이다. 정확 TP 여부와 Partial 1:1 배정은 전체 CSV에 있다.', '']
    for r in examples:
        lines += [f'### {r["doc_id"]} / {r["label"]} / {r["category"]}', '',
                  f'- 정답: `{r["gold_text"]}` [{r["start"]}, {r["end"]})',
                  f'- 규칙 ({CAUSE_KO[r["rule_cause"]]}): '+json.dumps(r['rule_predictions'],ensure_ascii=False),
                  f'- KoELECTRA ({CAUSE_KO[r["koelectra_cause"]]}): '+json.dumps(r['koelectra_predictions'],ensure_ascii=False), '']
    lines += ['## 성+직함과 추가 탐지 예시', '']
    for row in surnames[:3]:
        lines += [f'- {row["doc_id"]}: `{row["expression"]}` (정답 라벨 없음); 규칙 '
                  +json.dumps(row['rule_predictions'],ensure_ascii=False)+'; KoELECTRA '
                  +json.dumps(row['koelectra_predictions'],ensure_ascii=False)]
    lines += ['', '추가 예측은 `representative_fp.csv`에 원문 문맥·겹치는 정답·Strict/Partial FP 여부를 저장했다.', '',
              '## 산출물·재현', '',
              '- `overall_metrics.csv`, `per_label_metrics.csv`: 전체·라벨별 P/R/F1 및 속도.',
              '- `strict_cross_summary.csv`, `strict_cross_entities.csv`: 네 범주 집계와 548개 정답별 대응·오류 근거.',
              '- `prediction_errors.csv`: 예측 단위 Strict/Partial FP와 겹치는 정답.',
              '- `gold_error_causes.csv`, `merged_predictions.csv`, `person_groups.csv`, `surname_title_cases.csv`: 원인·병합·person 사례.',
              '- `representative_errors.csv`: 대표 사례의 문서 ID·정답·양쪽 예측.',
              '- `provenance.json`: 자동 탐색 경로, SHA256, 검증 결과와 과거 규칙 입력 provenance의 한계.', '',
              '프로젝트 루트에서 `.venv/Scripts/python.exe -B -m eval.cross_analysis`로 재현한다. 출력 경로가 있으면 거부하므로 재실행은 `--out local_results/comparison/dev_rerun`처럼 새 디렉터리를 지정한다.', '']
    (out/'REPORT.md').write_text('\n'.join(lines), encoding='utf-8')


if __name__ == '__main__':
    main()
