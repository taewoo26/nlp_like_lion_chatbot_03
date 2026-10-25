"""Compare with pinned upstream Torch decoder; Torch is test-only.

python -m detection.moai.test_reference --source models/moai-int8/official_decoding.py
Source is separately downloaded from REFERENCE_REVISION, never executed as a
module: only the ViterbiCRFDecoder class AST is used, with explicit globals.
"""
import argparse
from result_paths import private_output
import ast
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Sequence

import numpy as np

from .decoding import BIAS_KEYS, ViterbiDecoder
from .detector import REFERENCE_REVISION


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--source',required=True,type=Path)
    ap.add_argument('--config',type=Path,default=Path('models/moai-int8/config.json'))
    ap.add_argument('--out',type=private_output)
    args=ap.parse_args()
    digest=hashlib.sha256(args.source.read_bytes()).hexdigest()
    if digest != '785cc314e62b783bf673fdbed09475125f1975d8f0f5cd8a4ee340257e47c926':
        raise ValueError('Source does not match the pinned official decoder')
    import torch
    torch.set_num_threads(1)
    tree=ast.parse(args.source.read_text(encoding='utf-8'))
    node=next(x for x in tree.body if isinstance(x,ast.ClassDef) and x.name=='ViterbiCRFDecoder')
    scope=dict(torch=torch,dataclass=dataclass,LabelInfo=SimpleNamespace,Sequence=Sequence,_NEG_INF=-1e9)
    exec(compile(ast.Module(body=[node],type_ignores=[]),str(args.source),'exec'),scope)
    labels={int(k):v for k,v in json.loads(args.config.read_text(encoding='utf-8'))['id2label'].items()}
    background=next(i for i,v in labels.items() if v=='O')
    info=SimpleNamespace(token_to_span_label={i:'O' if v=='O' else v.split('-',1)[1] for i,v in labels.items()},
        token_boundary_tags={i:None if v=='O' else v.split('-',1)[0] for i,v in labels.items()},
        background_token_label=background,background_span_label='O')
    rng=np.random.default_rng(20261023)
    checked=0
    for bias in [dict.fromkeys(BIAS_KEYS,0.),dict(zip(BIAS_KEYS,rng.normal(size=6).tolist()))]:
        reference=scope['ViterbiCRFDecoder'](info,**bias)
        actual=ViterbiDecoder(labels,bias)
        for original,ported in [(reference._start_scores,actual.start),(reference._end_scores,actual.end),
                                (reference._transition_scores,actual.transitions)]:
            np.testing.assert_array_equal(original.numpy(),ported)
        for n in [0,1,2,3,31,128,1024,1100]:
            for variant in range(5):
                values=np.zeros((n,len(labels)),np.float32) if variant==0 else rng.normal(size=(n,len(labels))).astype(np.float32)
                tensor=torch.from_numpy(values).log_softmax(-1)
                expected=reference.decode(tensor)
                assert actual.decode(tensor.numpy())==expected,(n,variant)
                checked+=1
    result=dict(status='passed',cases=checked,classes=len(labels),reference_revision=REFERENCE_REVISION,
                source_sha256=hashlib.sha256(args.source.read_bytes()).hexdigest(),
                checks='Exact start/end/transition tables and decoded paths; zero/nonzero biases; empty/tied/random emissions; up to 1100 tokens')
    if args.out:
        args.out.parent.mkdir(parents=True,exist_ok=True)
        with args.out.open('x',encoding='utf-8') as f: json.dump(result,f,indent=2)
    print(json.dumps(result,indent=2))


if __name__=='__main__':
    main()
