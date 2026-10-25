"""Actual CPU long-input/tail-offset and empty-input smoke test."""
import argparse
from result_paths import private_output
import json
from pathlib import Path
from time import perf_counter

from .detector import MoAIDetector


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--out',required=True,type=private_output)
    a=ap.parse_args()
    if a.out.exists():
        ap.error('Output exists')
    model=MoAIDetector(local_files_only=True)
    text='😀 '+('This is a normal document. '*190)+'Contact John Smith at test@example.com or 010-1234-5678.'
    started=perf_counter()
    result=model.predict(text)
    elapsed=(perf_counter()-started)*1000
    assert len(result.tokens)>1024
    assert len(result.windows)>1
    assert result.windows[-1]['token_end']==len(result.tokens)
    assert result.tokens[-1]['end']==len(text)
    assert all(t['text']==text[t['start']:t['end']] for t in result.tokens)
    email_start=text.index('test@example.com')
    assert any(s.label=='email' and s.start==email_start and s.end==email_start+16 for s in result.spans)
    assert model.predict('').spans==[]
    report=dict(status='passed',text=text,characters=len(text),token_count=len(result.tokens),
        windows=result.windows,ms=elapsed,entities=[s.to_dict() for s in result.spans],
        checks=['More than 1024 tokens; no truncation','Overlapping CPU ONNX windows',
                'Original Python Unicode offsets','Email at document tail exact match','Empty input'])
    a.out.parent.mkdir(parents=True,exist_ok=True)
    with a.out.open('x',encoding='utf-8') as f: json.dump(report,f,ensure_ascii=False,indent=2)
    print(f'Passed: {len(result.tokens)} tokens, {len(result.windows)} windows, {elapsed:.1f}ms')


if __name__=='__main__':
    main()
