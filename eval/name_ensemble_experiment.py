"""이름에만 Veil을 추가하는 실험. 기존 combo 정책은 수정하지 않는다.

python -m eval.name_ensemble_experiment --data data/dev/pii_synth_finance_dev.jsonl \
    --out results/name_ensemble_dev_v1
번호·주소는 KoELECTRA post + Kiwi split의 결과로 고정한다.
합의는 라벨·시작·끝의 완전 일치이며 규칙 이름은 기존처럼 우선한다.
"""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time

from detection.base import Span
from detection.combo import (
    POLICIES, _build_model, drop_contextual_non_names,
    drop_surname_title, merge_spans,
)
from detection.rule.detector import RuleDetector
from detection.rule.names import has_strong_cue
from detection.validation import check_spans
from eval.run_compare import load_gold

VARIANTS = ('baseline', 'veil', 'union', 'agreement', 'agreement_cue')


def key(span):
    return span.label, span.start, span.end


def combine_names(text, rule, koelectra, veil):
    """정답 없이 후보를 결합한다. 단독 후보의 제거 전/후를 별도 정책으로 비교."""
    k = drop_contextual_non_names(text, koelectra, rule)
    v = drop_contextual_non_names(text, veil, rule)
    baseline = drop_surname_title(text, merge_spans(rule, k, POLICIES['split']))
    fixed = [s for s in baseline if s.label != 'person']
    rule_names = [s for s in rule if s.label == 'person']
    kn = [s for s in k if s.label == 'person']
    vn = [s for s in v if s.label == 'person']
    kk, vk = {key(s) for s in kn}, {key(s) for s in vn}
    union = list({key(s): s for s in [*kn, *vn]}.values())
    agreed = [s for s in kn if key(s) in vk]
    candidates = dict(baseline=kn, veil=vn, union=union, agreement=agreed,
                      agreement_cue=[s for s in union if key(s) in kk & vk
                                     or has_strong_cue(text, s)])
    outputs = {}
    for variant, names in candidates.items():
        # 이름 외 구간을 먼저 보호하므로 이름 정책이 번호·주소를 바꾸지 않는다.
        protected = merge_spans(fixed, rule_names, {})
        merged = drop_surname_title(text, merge_spans(protected, names, {}))
        outputs[variant] = check_spans(text, merged)
    return outputs, k, v


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    docs = load_gold(args.data)
    if len({d['id'] for d in docs}) != len(docs):
        raise ValueError('중복 문서 ID')
    args.out.mkdir(parents=True, exist_ok=False)
    detectors = dict(rule=RuleDetector(use_kiwi=True),
                     koelectra=_build_model('koelectra_post'), veil=_build_model('veil'))
    for det in detectors.values():
        det.detect('김민준 씨에게 연락했다.')
    predictions = {variant: [] for variant in VARIANTS}
    evidence, components, timing = [], [], Counter()
    for index, doc in enumerate(docs, 1):
        raw, elapsed = {}, {}
        for name, det in detectors.items():
            start = time.perf_counter()
            raw[name] = det.detect(doc['text'])
            elapsed[name] = (time.perf_counter() - start) * 1000
            timing[name] += elapsed[name]
        components.append(dict(id=doc['id'], ms=elapsed,
                               predictions={n: [s.to_dict() for s in ss] for n, ss in raw.items()}))
        outputs, k, v = combine_names(doc['text'], raw['rule'], raw['koelectra'], raw['veil'])
        for variant, spans in outputs.items():
            predictions[variant].append(dict(id=doc['id'], entities=[s.to_dict() for s in spans]))
        rkeys = {key(s) for s in raw['rule']}
        kkeys, vkeys = {key(s) for s in k}, {key(s) for s in v}
        candidates = {key(s): s for s in [*raw['rule'], *k, *v] if s.label == 'person'}
        evidence.append(dict(id=doc['id'], candidates=[dict(**s.to_dict(),
            rule=key(s) in rkeys, koelectra=key(s) in kkeys, veil=key(s) in vkeys,
            strong_cue=has_strong_cue(doc['text'], s)) for s in candidates.values()]))
        if index % 25 == 0:
            print(f'{index}/{len(docs)}문서 실행', flush=True)
    def write_jsonl(path, rows):
        path.write_text(''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in rows), encoding='utf-8')
    write_jsonl(args.out / 'evidence.jsonl', evidence)
    write_jsonl(args.out / 'components.jsonl', components)
    # 채점 파일에는 합성 실행 시간을 넣지 않는다. 컴포넌트 시간은 별도 기록한다.
    for variant, rows in predictions.items():
        pred = args.out / f'{variant}.jsonl'
        write_jsonl(pred, rows)
        subprocess.run([sys.executable, '-m', 'eval.run_compare', '--gold', str(args.data),
                        '--pred', f'name_{variant}={pred}', '--out', str(args.out / variant)], check=True)
    manifest = dict(data=str(args.data), documents=len(docs),
        data_sha256=hashlib.sha256(args.data.read_bytes()).hexdigest(),
        head=subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip(),
        source_sha256={str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in [
            Path(__file__), Path('detection/combo.py'), Path('detection/rule/detector.py'),
            Path('detection/rule/names.py'), Path('detection/rule/kiwi_filter.py'),
            Path('detection/token_cls/koelectra_post.py'), Path('detection/token_cls/veil.py')]},
        component_mean_ms={k: total / len(docs) for k, total in timing.items()} if docs else {},
        notes=['번호·주소 등 이름 외 구간은 baseline으로 고정',
               '이름 규칙은 한 표가 아니라 공통으로 보존하는 기반',
               'agreement는 모델 이름 완전 일치. 약한 겹침은 합의 아님',
               'evidence의 모델 지지는 공통 문맥 필터 적용 후 기준',
               '컴포넌트 시간은 실제 각 탐지 호출 측정. 앙상블 전체 지연 시간 아님'])
    (args.out / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')


if __name__ == '__main__':
    main()
