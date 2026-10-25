"""Deterministic decoder, Unicode, chunking and evaluation integration tests."""
import itertools
import json
from pathlib import Path
from types import SimpleNamespace
import unittest

import numpy as np

from .decoding import BIAS_KEYS, ViterbiDecoder, load_biases
from .detector import LABEL_MAP, MoAIDetector, decode_spans, windows


class DecoderTests(unittest.TestCase):
    def setUp(self):
        self.labels = dict(enumerate(['O','B-PERSON','I-PERSON','E-PERSON','S-PERSON',
                                     'B-FRN','I-FRN','E-FRN','S-FRN']))
        self.biases = dict.fromkeys(BIAS_KEYS, 0.)
        self.decoder = ViterbiDecoder(self.labels, self.biases)

    def test_global_optimum_and_constraints(self):
        rng = np.random.default_rng(31)
        for _ in range(8):
            scores = rng.normal(size=(4,9)).astype(np.float32)
            path = self.decoder.decode(scores)
            def score(p):
                value = scores[0,p[0]] + self.decoder.start[p[0]]
                for i in range(1,len(p)):
                    value = value + self.decoder.transitions[p[i-1],p[i]] + scores[i,p[i]]
                return value + self.decoder.end[p[-1]]
            best = max(itertools.product(range(9), repeat=4), key=score)
            self.assertEqual(path, list(best))
        scores = np.zeros((2,9),np.float32)
        scores[:,2] = 10  # illegal I at document start/end
        path = self.decoder.decode(scores)
        self.assertNotEqual(path, [2,2])
        self.assertEqual(path, [0,0])

    def test_biases_change_path(self):
        biased = dict(self.biases, transition_bias_background_to_start=5.)
        scores = np.zeros((2,9),np.float32)
        self.assertNotEqual(self.decoder.decode(scores), ViterbiDecoder(self.labels,biased).decode(scores))

    def test_empty_ties_and_schema(self):
        self.assertEqual(self.decoder.decode(np.empty((0,9))), [])
        self.assertEqual(self.decoder.decode(np.zeros((8,9))), [0]*8)
        with self.assertRaises(ValueError):
            ViterbiDecoder({0:'O',1:'B-PERSON'},self.biases)
        with self.assertRaises(ValueError):
            self.decoder.decode(np.zeros((2,8)))

    def test_trim_internal_spaces_unicode_and_adjacent_names(self):
        text = '😀  김 민수 \n이영희  '
        tokens = [dict(tag='B-PERSON',start=1,end=4),
                  dict(tag='E-PERSON',start=4,end=9),
                  dict(tag='S-PERSON',start=9,end=len(text))]
        spans = decode_spans(text,tokens)
        self.assertEqual([(s.start,s.end,s.text) for s in spans],[(3,7,'김 민수'),(9,12,'이영희')])
        self.assertNotIn('FRN',LABEL_MAP)
        self.assertNotIn('SSN',LABEL_MAP)
        self.assertNotIn('GENERIC_ID',LABEL_MAP)

    def test_windows_cover_tail_and_validate(self):
        chunks = list(windows(2501,1024,128))
        self.assertEqual(chunks,[(0,1024),(896,1920),(1792,2501)])
        self.assertEqual(set().union(*(set(range(s,e)) for s,e in chunks)),set(range(2501)))
        self.assertEqual(list(windows(0,1024,128)),[])
        for maximum,overlap in [(1025,128),(1024,1024),(0,0),(20,-1)]:
            with self.assertRaises(ValueError): list(windows(1,maximum,overlap))

    def test_long_document_global_decode_crosses_chunk_and_unicode_duplicates(self):
        # Same real inference/fusion/decoding path; synthetic session isolates
        # boundary correctness from model quality, including duplicate offsets.
        detector = MoAIDetector.__new__(MoAIDetector)
        n=2200
        text='😀'+'가'*2198
        offsets=[(0,1),(0,1)]+[(i,i+1) for i in range(1,len(text))]
        detector.tokenizer=SimpleNamespace(encode=lambda *a,**k: SimpleNamespace(ids=list(range(n)),offsets=offsets))
        detector.id2label=self.labels
        detector.decoder=self.decoder
        detector.max_length,detector.overlap=1024,128
        detector.inputs=[SimpleNamespace(name='input_ids')]
        calls=[]
        def run(outputs,feeds):
            ids=feeds['input_ids'][0]; calls.append(len(ids))
            logits=np.full((1,len(ids),9),-20,np.float32)
            for j,i in enumerate(ids):
                lab=1 if i==950 else 3 if i==1040 else 2 if 950<i<1040 else 4 if i==2199 else 0
                logits[0,j,lab]=20
            return [logits]
        detector.session=SimpleNamespace(run=run)
        result=detector.predict(text)
        self.assertEqual(calls,[1024,1024,408])
        self.assertEqual(len(result.tokens),n)
        self.assertEqual([(s.start,s.end) for s in result.spans],[(949,1040),(2198,2199)])
        self.assertEqual([t['index'] for t in result.tokens],list(range(n)))
        self.assertEqual(result.tokens[1023]['tag'],'I-PERSON')


class StoredResultsTests(unittest.TestCase):
    @unittest.skipUnless(Path('local_results/moai_int8/dev/metadata.json').exists(), 'Run dev inference first')
    def test_complete_jsonl_dashboard_and_score(self):
        import run_compare as scorer
        from visualization.koelectra_dashboard import SOURCES,load_source
        root=Path('local_results/moai_int8/dev')
        meta=json.loads((root/'metadata.json').read_text(encoding='utf-8'))
        self.assertEqual(meta['status'],'complete')
        docs=scorer.load_gold('data/dev/pii_synth_finance_dev.jsonl')
        rows=scorer.load_jsonl(root/'predictions.jsonl')
        self.assertEqual(len(rows),100)
        self.assertEqual({r['id'] for r in rows},{d['id'] for d in docs})
        for row in rows:
            self.assertEqual(set(row),{'id','entities','ms'})
            self.assertGreaterEqual(row['ms'],0)
            for entity in row['entities']:
                self.assertEqual(set(entity),{'label','start','end'})
                self.assertIn(entity['label'],LABEL_MAP.values())
        self.assertTrue(any(not r['entities'] for r in rows))
        predictions,_=scorer.load_pred(root/'predictions.jsonl',docs)
        metrics,_=scorer.summarize(docs,predictions)
        dashboard,display=load_source(SOURCES['MoAI INT8'])
        self.assertEqual(len(dashboard),100)
        self.assertEqual(metrics,display)


if __name__ == '__main__':
    unittest.main()
