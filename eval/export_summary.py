"""Export reviewed numeric metrics and selected settings, never example text."""
import argparse
import csv
import hashlib
import json
import math
from pathlib import Path
import re

import run_compare as scorer
from result_paths import ROOT, LOCAL_RESULTS, SHARED_RESULTS

MODELS = (
    ('rule_baseline', '규칙 기반', 'Regex + Kiwi', 'rules_dev.jsonl'),
    ('token_cls_baseline', 'KoELECTRA', 'atonlee/koelectra-ko-pii-ner', 'predictions.jsonl'),
    ('moai_int8', 'MoAI INT8', 'BCCard/MoAI-Privacy-Filter-INT8', 'predictions.jsonl'),
)
METRICS = ('strict_p', 'strict_r', 'strict_f1', 'partial_p', 'partial_r', 'partial_f1', 'mask_rec')


def number(value):
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError('Expected a finite numeric aggregate')
    return value


def version(value, revision=False):
    pattern = r'[0-9a-f]{40}' if revision else r'[0-9][0-9A-Za-z.+_-]*'
    if not isinstance(value, str) or re.fullmatch(pattern, value) is None:
        raise ValueError('Unexpected version/revision format')
    return value


def load_csv(path):
    with path.open(encoding='utf-8-sig', newline='') as source:
        return list(csv.DictReader(source))


def prepare(private_root, gold):
    docs = scorer.load_gold(gold)
    reports, overall = {}, []
    for folder, title, model, filename in MODELS:
        run = private_root / folder / 'dev'
        predictions, mean_ms = scorer.load_pred(run / filename, docs)
        rows = scorer.load_jsonl(run / filename)
        if len(rows) != len(docs) or {r['id'] for r in rows} != {d['id'] for d in docs}:
            raise ValueError('Prediction document IDs do not match gold')
        metrics, counts = scorer.summarize(docs, predictions)
        saved = load_csv(run / 'eval/summary.csv')[0]
        for key in METRICS:
            if abs(metrics[key] - float(saved[key])) > 0.000051:
                raise ValueError('Saved evaluation differs from current scorer')
        overall.append((title, metrics, number(mean_ms)))
        lines = [f'# {title} dev 공유 요약', '',
                 '저장된 예측을 기존 채점기로 재집계하여 평가 CSV와 일치를 검증했다. 모델 추론은 재실행하지 않았다.', '',
                 '## 실험 설정', '', f'- 모델: `{model}`',
                 f'- 데이터: 합성 금융 dev {len(docs)}문서, 평가 7종 정답 {sum(len(d["gold"]) for d in docs)}개.',
                 '- 추가 학습 없음. warmup 없음. 시간은 로딩·파일 I/O를 제외한 저장 기록이다.']
        if folder == 'rule_baseline':
            lines += ['- 규칙: 정규식 + Kiwi NNP/문맥 제한, Kiwi worker 1.',
                      '- 과거 Kiwi 버전·입력 hash 메타데이터·코드 revision은 확인 필요.']
        else:
            meta = json.loads((run / 'metadata.json').read_text(encoding='utf-8'))
            if meta['model'] != model or meta['status'] != 'complete':
                raise ValueError('Unexpected model or incomplete run')
            if meta['input_sha256'] != hashlib.sha256(gold.read_bytes()).hexdigest():
                raise ValueError('Gold input hash differs from run metadata')
            lines += [f'- 모델 revision: `{version(meta["model_revision"], revision=True)}`',
                      f'- 토크나이저 revision: `{version(meta["tokenizer_revision"], revision=True)}`',
                      f'- CPU 스레드: {number(meta["threads"])}; 최대 길이: {number(meta["max_length"])}토큰.',
                      f'- 겹침: {number(meta.get("stride", meta.get("overlap")))}토큰.',
                      f'- Python: {version(meta["python"])}.']
            for package in ('torch', 'transformers', 'onnxruntime', 'numpy', 'tokenizers', 'huggingface-hub', 'safetensors', 'msvc-runtime'):
                if package in meta['packages']:
                    lines.append(f'- {package}: {version(meta["packages"][package])}.')
            if folder == 'token_cls_baseline':
                lines += [f'- seed: {number(meta["seed"])}; 결정적 연산 사용.',
                          '- BIO 복원. 조사·정규식 보정 없음. 원래 29종 중 7종만 평가.']
            else:
                lines += ['- CPUExecutionProvider, BIOES 전역 Viterbi, 외곽 공백만 제거. 29종 중 7종만 평가.',
                          f'- 디코더 참조 revision: `{version(meta["reference_revision"], revision=True)}`.']
        lines += ['', '## 전체 성능', '', '| 지표 | 값 |', '|---|---:|']
        lines += [f'| {key} | {100 * number(metrics[key]):.4f}% |' for key in METRICS]
        lines += [f'| 평균 ms/문서 | {mean_ms:.2f} |', '', '## 라벨별 성능·오류 규모', '',
                  '| 라벨 | 정답 | 예측 | Strict F1 | Partial F1 | Strict 불일치 정답 | Partial 미매칭 정답 |',
                  '|---|---:|---:|---:|---:|---:|---:|']
        for label in scorer.LABELS:
            c = counts[label]
            sf = scorer.prf(c['strict_tp'], c['pred'], c['gold'])[2]
            pf = scorer.prf(c['partial_tp'], c['pred'], c['gold'])[2]
            lines.append(f'| {label} | {c["gold"]} | {c["pred"]} | {100*sf:.2f}% | {100*pf:.2f}% | {c["gold"]-c["strict_tp"]} | {c["gold"]-c["partial_tp"]} |')
        lines += ['', '## 주요 해석과 한계', '',
                  '- Strict는 라벨·경계 완전 일치, Partial은 같은 라벨의 겹침을 순서대로 1:1 매칭한다.',
                  '- mask_rec은 문자 피복률이며 평가 대상 7종 예측만 입력했다.',
                  '- 오류 규모는 정답 단위 집계다. 경계 오류와 완전 미탐을 혼동하지 않는다.',
                  '- test·holdout 일반화와 전체 dev 반복 일관성은 미측정이다.',
                  '- 속도는 실행 시점이 다른 저장값으로 통제된 비교가 아니다.']
        if folder == 'token_cls_baseline':
            report = json.loads((run / 'analysis/report.json').read_text(encoding='utf-8'))
            for group, text in [('spaced', '띄어 쓴 이름'), ('josa_attached_actual', '실제 조사 결합'), ('honorific_vocative_actual', '호칭·부름말')]:
                values = report['groups'][group]
                lines.append(f'- {text}: {number(values["total"])}개 중 정확 경계 {number(values.get("exact", 0))}개, 경계 오류 {number(values.get("boundary", 0))}개.')
            lines.append(f'- 성+직함 비대상 {number(report["surname_title"]["occurrences"])}곳 중 오탐 {number(report["surname_title"]["false_positives"])}곳.')
        elif folder == 'rule_baseline':
            lines.append('- 정형 라벨보다 person 재현율이 낮다. 합성 dev의 형식에 대한 결과로만 해석한다.')
        else:
            lines.append('- person·account_number의 재현율과 address의 추가 탐지가 주요 개선 대상이다.')
        reports[f'{folder}/dev/README.md'] = '\n'.join(lines) + '\n'

    lines = ['# dev 모델 비교 공유 요약', '',
             '동일 dev 100문서·정답548개를 기존 채점기로 재집계했다. 원문·문서 ID·개별 예측은 포함하지 않는다.', '',
             '| 모델 | Strict F1 | Partial Recall | Partial F1 | mask_rec | 저장 평균 ms |',
             '|---|---:|---:|---:|---:|---:|']
    for title, m, ms in overall:
        lines.append(f'| {title} | {m["strict_f1"]*100:.2f}% | {m["partial_r"]*100:.2f}% | {m["partial_f1"]*100:.2f}% | {m["mask_rec"]*100:.2f}% | {ms:.2f} |')
    lines += ['', '## 규칙·KoELECTRA 오류 교차 집계', '',
              '| 라벨 | 정답 | 둘 다 정답 | 규칙만 정답 | KoELECTRA만 정답 | 둘 다 오류 |',
              '|---|---:|---:|---:|---:|---:|']
    cross = load_csv(private_root / 'comparison/dev/strict_cross_summary.csv')
    for row in cross:
        if row['label'] not in ('ALL', *scorer.LABELS):
            raise ValueError('Unexpected label in aggregate')
        values = [int(row[key]) for key in ('gold', 'BOTH_CORRECT', 'RULE_ONLY', 'KOELECTRA_ONLY', 'BOTH_WRONG')]
        if sum(values[1:]) != values[0]:
            raise ValueError('Invalid cross aggregate')
        lines.append('| ' + row['label'] + ' | ' + ' | '.join(map(str, values)) + ' |')
    lines += ['', '- 교차 집계는 규칙·KoELECTRA만 비교한다. 전체 성능 표는 MoAI까지 포함한다.',
              '- Oracle은 정답을 알고 선택하는 사후 상한으로 실제 하이브리드 성능이 아니다.',
              '- 규칙의 과거 입력 hash 기록은 없어 당시 입력 동일성은 독립 증명이 어렵다.',
              '- 최종 선정 기준·test/holdout 일반화·통제 CPU 측정·3회 반복은 추가 확인이 필요하다.']
    reports['comparison/dev/README.md'] = '\n'.join(lines) + '\n'
    return reports


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--private-root', type=Path, default=LOCAL_RESULTS)
    ap.add_argument('--gold', type=Path, default=ROOT / 'data/dev/pii_synth_finance_dev.jsonl')
    ap.add_argument('--out', type=Path, default=SHARED_RESULTS)
    args = ap.parse_args()
    reports = prepare(args.private_root, args.gold)
    targets = [(args.out / name, body) for name, body in reports.items()]
    if any(path.exists() for path, _ in targets):
        ap.error('Summary exists; use a new --out directory to preserve reviewed documents')
    for path, body in targets:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('x', encoding='utf-8', newline='\n') as destination:
            destination.write(body)
    print(f'Wrote {len(targets)} aggregate-only Markdown summaries')


if __name__ == '__main__':
    main()
