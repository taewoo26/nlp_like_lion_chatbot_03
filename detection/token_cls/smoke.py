"""Real-model smoke checks on three dev documents and a repeated long text."""
import argparse
from result_paths import private_output
import json
from pathlib import Path

from detection.rule.run import iter_documents
from detection.token_cls.detector import TokenClassificationDetector, validate_spans, LABEL_MAP


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--input', type=Path, required=True)
    ap.add_argument('--out', type=private_output, required=True)
    args = ap.parse_args()
    docs = list(iter_documents(args.input))[:3]
    detector = TokenClassificationDetector(local_files_only=True)
    checks = []
    for d in docs:
        a, b = detector.predict(d['text']), detector.predict(d['text'])
        assert a == b, 'Repeated CPU predictions differ'
        assert detector.detect(d['text']) == a.spans
        checks.append(dict(id=d['id'], entities=len(a.spans), deterministic=True))
    text = (docs[0]['text'] + '\n') * 12
    long = detector.predict(text)
    reference = detector.tokenizer(text, add_special_tokens=False,
                                   return_offsets_mapping=True, truncation=False)
    offsets = [tuple(x) for x in reference['offset_mapping'] if x[0] < x[1]]
    assert long.windows > 1
    assert [(t['start'], t['end']) for t in long.tokens] == offsets, 'Lost/duplicated tokens'
    assert long.tokens[-1]['end'] == offsets[-1][1]
    validate_spans(text, long.spans, set(LABEL_MAP.values()))
    assert detector.predict('').spans == []
    report = dict(status='passed', documents=checks, long_windows=long.windows,
                  long_tokens=len(offsets), long_characters=len(text),
                  full_offset_coverage=True, empty_text=True)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open('x', encoding='utf-8') as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(json.dumps(report, ensure_ascii=False))


if __name__ == '__main__':
    main()
