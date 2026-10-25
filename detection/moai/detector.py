"""117-label constrained BIOES inference with ONNX Runtime CPU only."""
from dataclasses import dataclass
import json
from pathlib import Path

import numpy as np

from detection.base import BaseDetector, Span
from .decoding import ViterbiDecoder, load_biases

MODEL_ID = 'BCCard/MoAI-Privacy-Filter-INT8'
REVISION = '60731c85761bd46d23db2e742082a7223696e1c1'
REFERENCE_REVISION = 'f7f00ca7fb869683eb732c010299d901457f19c3'
FILES = ('config.json', 'viterbi_calibration.json', 'tokenizer.json',
         'tokenizer_config.json', 'model_quantized.onnx', 'model_quantized.onnx_data')
LABEL_MAP = {
    'PERSON': 'person', 'PHONE': 'phone_number', 'EMAIL': 'email',
    'ADDRESS': 'address', 'RRN': 'resident_registration_number',
    'ACCOUNT_NUMBER': 'account_number', 'CARD_NUMBER': 'card_number',
}


def windows(length, max_length, overlap):
    if not 1 <= max_length <= 1024 or not 0 <= overlap < max_length:
        raise ValueError('Require 1 <= max_length <= 1024 and 0 <= overlap < max_length')
    start = 0
    while start < length:
        end = min(start + max_length, length)
        yield start, end
        if end == length:
            break
        start = end - overlap


def decode_spans(text, tokens):
    spans, active = [], None
    def append(label, start, end):
        while start < end and text[start].isspace():
            start += 1
        while end > start and text[end-1].isspace():
            end -= 1
        if start < end:
            spans.append(Span(start, end, label, text[start:end]))
    for token in tokens:
        tag = token['tag']
        if tag == 'O':
            if active is not None:
                raise ValueError('Unclosed BIOES entity')
            continue
        boundary, label = tag.split('-', 1)
        s, e = token['start'], token['end']
        if boundary in {'B', 'S'}:
            if active is not None:
                raise ValueError('Invalid BIOES start')
            active = (label, s, e)
        else:
            if active is None or active[0] != label:
                raise ValueError('Invalid BIOES continuation')
            active = (label, active[1], max(active[2], e))
        if boundary in {'E', 'S'}:
            append(*active)
            active = None
    if active is not None:
        raise ValueError('Unclosed BIOES sequence')
    return list({(s.label, s.start, s.end): s for s in spans}.values())


@dataclass
class Prediction:
    spans: list[Span]
    raw_entities: list[Span]
    tokens: list[dict]
    windows: list[dict]

    def raw_dict(self):
        return dict(entities=[s.to_dict() for s in self.raw_entities],
                    unmapped_entities=[s.to_dict() for s in self.raw_entities if s.label not in LABEL_MAP],
                    tokens=self.tokens, windows=self.windows)


class MoAIDetector(BaseDetector):
    name = 'moai_int8'

    def __init__(self, *, cache_dir='models/huggingface', local_files_only=False,
                 max_length=1024, overlap=128, threads=1):
        from .runtime import prepare_runtime
        prepare_runtime()
        import onnxruntime as ort
        from huggingface_hub import snapshot_download
        from tokenizers import Tokenizer

        list(windows(1, max_length, overlap))
        if threads < 1:
            raise ValueError('threads must be positive')
        self.model_dir = Path(snapshot_download(MODEL_ID, revision=REVISION,
            cache_dir=cache_dir, local_files_only=local_files_only, allow_patterns=list(FILES)))
        config = json.loads((self.model_dir/'config.json').read_text(encoding='utf-8'))
        self.id2label = {int(k): v for k, v in config['id2label'].items()}
        self.biases = load_biases(self.model_dir/'viterbi_calibration.json')
        self.decoder = ViterbiDecoder(self.id2label, self.biases)
        if len(self.id2label) != 117 or len(self.decoder.entity_labels) != 29 or not LABEL_MAP.keys() <= self.decoder.entity_labels:
            raise ValueError('Unexpected MoAI model taxonomy')
        # Use the model's Rust tokenizer backend directly: no transformers model
        # loader, pipeline, remote Python code, or PyTorch runtime dependency.
        self.tokenizer = Tokenizer.from_file(str(self.model_dir/'tokenizer.json'))
        self.tokenizer.no_truncation()
        self.tokenizer.no_padding()
        options = ort.SessionOptions()
        options.intra_op_num_threads = threads
        options.inter_op_num_threads = 1
        options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        self.session = ort.InferenceSession(str(self.model_dir/'model_quantized.onnx'),
            sess_options=options, providers=['CPUExecutionProvider'])
        if self.session.get_providers() != ['CPUExecutionProvider']:
            raise ValueError('CPUExecutionProvider required')
        self.inputs = self.session.get_inputs()
        if any(i.name not in {'input_ids', 'attention_mask', 'position_ids', 'token_type_ids'}
               or i.type != 'tensor(int64)' for i in self.inputs):
            raise ValueError('Unexpected ONNX input schema')
        self.max_length, self.overlap, self.threads = max_length, overlap, threads

    def predict(self, text):
        if not isinstance(text, str):
            raise TypeError('text must be a string')
        encoded = self.tokenizer.encode(text, add_special_tokens=False)
        # Keep token indices, not (start,end) keys: byte-level Unicode pieces
        # can share character offsets and must remain separate in Viterbi.
        ids, offsets = encoded.ids, encoded.offsets
        if any(not 0 <= s <= e <= len(text) for s, e in offsets):
            raise ValueError('Invalid tokenizer character offsets')
        fused = np.empty((len(ids), len(self.id2label)), dtype=np.float32)
        contexts = np.full(len(ids), -1, dtype=np.int64)
        owners = np.full(len(ids), -1, dtype=np.int64)
        chunks = []
        for w, (start, end) in enumerate(windows(len(ids), self.max_length, self.overlap)):
            data = dict(input_ids=np.array([ids[start:end]], dtype=np.int64),
                        attention_mask=np.ones((1, end-start), dtype=np.int64),
                        position_ids=np.arange(end-start, dtype=np.int64)[None, :],
                        token_type_ids=np.zeros((1, end-start), dtype=np.int64))
            logits = self.session.run(['logits'], {i.name: data[i.name] for i in self.inputs})[0]
            if logits.shape != (1, end-start, len(self.id2label)) or not np.isfinite(logits).all():
                raise ValueError('Invalid ONNX logits')
            scores = logits[0].astype(np.float32)
            scores = scores - scores.max(axis=1, keepdims=True)
            scores -= np.log(np.exp(scores).sum(axis=1, keepdims=True))
            context = np.minimum(np.arange(end-start), np.arange(end-start)[::-1])
            use = context > contexts[start:end]  # earliest window wins ties
            fused[start:end][use] = scores[use]
            contexts[start:end][use] = context[use]
            owners[start:end][use] = w
            chunks.append(dict(token_start=start, token_end=end,
                               start=offsets[start][0], end=offsets[end-1][1]))
        if len(ids) and (owners < 0).any():
            raise ValueError('Chunking left uncovered tokens')
        path = self.decoder.decode(fused)
        tokens = [dict(index=i, token_id=ids[i], start=s, end=e, text=text[s:e],
                       tag=self.id2label[label], confidence=float(np.exp(fused[i, label])),
                       window=int(owners[i])) for i, ((s, e), label) in enumerate(zip(offsets, path))]
        raw = decode_spans(text, tokens)
        mapped = [Span(s.start, s.end, LABEL_MAP[s.label], s.text) for s in raw if s.label in LABEL_MAP]
        return Prediction(mapped, raw, tokens, chunks)

    def detect(self, text):
        return self.predict(text).spans
