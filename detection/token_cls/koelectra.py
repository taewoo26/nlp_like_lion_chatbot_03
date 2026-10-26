"""KoELECTRA-small INT8: argmax BIO labels -> original character spans.

Source: https://huggingface.co/atonlee/koelectra-ko-pii-ner
Long inputs: mean logits over 510-token windows with 128-token overlap,
then a single BIO reconstruction. This window policy is project-specific.
"""
import argparse
import json
from pathlib import Path

import numpy as np
from detection.base import BaseDetector, Span

REPO_ID = 'atonlee/koelectra-ko-pii-ner'
REVISION = '1e75c01e707232401883cf364151bbe2e560c708'
MODEL_DIR = Path(__file__).resolve().parents[2] / 'models' / 'koelectra-int8' / REVISION
FILES = ['onnx/model_int8.onnx', 'config.json', 'tokenizer.json', 'tokenizer_config.json', 'README.md']
LABEL_MAP = {
    'NAME': 'person', 'PHONE': 'phone_number', 'EMAIL': 'email',
    'ADDRESS': 'address', 'RRN': 'resident_registration_number',
    'ACCOUNT_NUMBER': 'account_number', 'CARD_NUMBER': 'card_number',
}


def prepare_model():
    from huggingface_hub import snapshot_download
    print(f'다운로드: {REPO_ID}@{REVISION}', flush=True)
    snapshot_download(repo_id=REPO_ID, revision=REVISION,
                      allow_patterns=FILES, local_dir=MODEL_DIR)
    print(f'저장: {MODEL_DIR}', flush=True)


def bio_to_spans(text, offsets, tags):
    """B starts a new entity; unmatched I starts one too (documented recovery)."""
    if len(offsets) != len(tags):
        raise ValueError('Token labels and offsets have different lengths')
    result = []
    current = None

    def flush():
        if current is not None:
            start, end, label = current
            # Only trim whitespace; keep particles/titles predicted by the model.
            while start < end and text[start].isspace():
                start += 1
            while end > start and text[end - 1].isspace():
                end -= 1
            if start < end and label in LABEL_MAP:
                result.append(Span(start, end, LABEL_MAP[label], text[start:end]))

    for (start, end), tag in zip(offsets, tags):
        start, end = int(start), int(end)
        if not 0 <= start <= end <= len(text):
            raise ValueError('Invalid character offsets')
        if start == end:
            continue
        if tag == 'O':
            flush()
            current = None
            continue
        prefix, label = tag.split('-', 1)
        if prefix not in ('B', 'I'):
            raise ValueError(f'Unexpected BIO label: {tag}')
        if prefix == 'I' and current is not None and current[2] == label:
            current = (current[0], end, label)
        else:
            flush()
            current = (start, end, label)
    flush()
    return result


class KoElectraDetector(BaseDetector):
    name = 'koelectra'

    def __init__(self, threads=2):
        import onnxruntime as ort
        from transformers import AutoTokenizer
        if threads < 1:
            raise ValueError('threads must be positive')
        for filename in FILES:
            if not (MODEL_DIR / filename).is_file():
                raise FileNotFoundError('모델 준비 필요: python -m detection.token_cls.koelectra --download')
        config = json.loads((MODEL_DIR / 'config.json').read_text())
        self.labels = [config['id2label'][str(i)] for i in range(len(config['id2label']))]
        if any(t != 'O' and not t.startswith(('B-', 'I-')) for t in self.labels):
            raise ValueError('Expected BIO model labels')
        if not set(LABEL_MAP).issubset({t[2:] for t in self.labels if t != 'O'}):
            raise ValueError('Required entity labels missing from model config')
        self.tokenizer = AutoTokenizer.from_pretrained(MODEL_DIR, local_files_only=True)
        if self.tokenizer.num_special_tokens_to_add(pair=False) != 2:
            raise ValueError('Expected CLS and SEP tokens')
        options = ort.SessionOptions()
        options.intra_op_num_threads = threads
        options.inter_op_num_threads = 1
        self.session = ort.InferenceSession(str(MODEL_DIR / 'onnx/model_int8.onnx'),
                                            sess_options=options, providers=['CPUExecutionProvider'])
        self.detect('모델 준비 확인.')

    def detect(self, text):
        offsets, tags = self.predict_bio(text)
        return bio_to_spans(text, offsets, tags)

    def predict_bio(self, text):
        """Return original offsets and BIO tags for reproducible diagnostics."""
        if not text.strip():
            return [], []
        encoded = self.tokenizer(text, add_special_tokens=False, truncation=False,
                                 return_offsets_mapping=True, verbose=False)
        ids, offsets = encoded['input_ids'], encoded['offset_mapping']
        if not ids:
            return [], []
        total = np.zeros((len(ids), len(self.labels)), dtype=np.float64)
        count = np.zeros((len(ids), 1))
        for start in range(0, len(ids), 382):
            end = min(start + 510, len(ids))
            input_ids = [self.tokenizer.cls_token_id, *ids[start:end], self.tokenizer.sep_token_id]
            values = {
                'input_ids': input_ids,
                'attention_mask': [1] * len(input_ids),
                'token_type_ids': [0] * len(input_ids),
            }
            feeds = {entry.name: np.asarray([values[entry.name]], dtype=np.int64)
                     for entry in self.session.get_inputs()}
            logits = self.session.run(None, feeds)[0]
            if logits.shape != (1, len(input_ids), len(self.labels)) or not np.isfinite(logits).all():
                raise ValueError(f'Unexpected model output: {logits.shape}')
            total[start:end] += logits[0, 1:-1]
            count[start:end] += 1
            if end == len(ids):
                break
        tags = [self.labels[int(i)] for i in (total / count).argmax(axis=1)]
        return offsets, tags


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--download', action='store_true')
    args = parser.parse_args()
    if args.download:
        prepare_model()
    detector = KoElectraDetector()
    text = '김민준 씨한테 010-1234-5678로 연락해줘'
    print(f'입력: {text}')
    for span in detector.detect(text):
        print(span.to_dict())


if __name__ == '__main__':
    main()
