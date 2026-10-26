"""Pinned Veil INT8 adapter using the reviewed upstream decoder unchanged."""
import argparse
import hashlib
import importlib.util
from pathlib import Path

from detection.base import BaseDetector, Span

SOURCE_SHA256 = '72e11e0970d99a804b1f87a1c4bb3c9efa283723352a214f8de52bbc0ffe0a9b'
REPO_ID = '1T/veil-pii-ko-lite'
REVISION = 'a8343030ef774ad78d6f0481a6877a971cef759d'
MODEL_DIR = Path(__file__).resolve().parents[2] / 'models' / 'veil-int8' / REVISION
FILES = ['model.int8.onnx', 'veil.py', 'config.json', 'tokenizer.json',
         'tokenizer_config.json', 'special_tokens_map.json', 'vocab.txt', 'README.md']
LABEL_MAP = {
    'PERSON': 'person', 'PHONE': 'phone_number', 'EMAIL': 'email',
    'ADDRESS': 'address', 'RRN': 'resident_registration_number',
    'ACCOUNT_NUMBER': 'account_number', 'CARD_NUMBER': 'card_number',
}


def prepare_model():
    from huggingface_hub import snapshot_download
    print(f'다운로드: {REPO_ID}@{REVISION}', flush=True)
    snapshot_download(repo_id=REPO_ID, revision=REVISION,
                      allow_patterns=FILES, local_dir=MODEL_DIR)
    print(f'저장: {MODEL_DIR}', flush=True)


class VeilDetector(BaseDetector):
    name = 'veil'

    def __init__(self, threads=2):
        if threads < 1:
            raise ValueError('threads must be positive')
        for name in FILES:
            if not (MODEL_DIR / name).is_file():
                raise FileNotFoundError('모델 준비 필요: python -m detection.token_cls.veil --download')
        source = MODEL_DIR / 'veil.py'
        # Hash is pinned after source inspection, before any downloaded code runs.
        expected = SOURCE_SHA256
        if hashlib.sha256(source.read_bytes()).hexdigest() != expected:
            raise ValueError('Veil 실행 코드가 검토한 버전과 다릅니다.')
        spec = importlib.util.spec_from_file_location('_reviewed_veil_runtime', source)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.runtime = module.Veil(str(MODEL_DIR / 'model.int8.onnx'),
                                   tokenizer_dir=str(MODEL_DIR), threads=threads,
                                   max_len=512, stride=128, o_bias=0.0,
                                   merge_adjacent=())
        self.detect('모델 준비 확인.')

    def detect(self, text):
        if not text.strip():
            return []
        spans = []
        for item in self.runtime.predict(text):
            if item['label'] not in LABEL_MAP:
                continue
            start, end = int(item['start']), int(item['end'])
            if not 0 <= start < end <= len(text):
                raise ValueError(f'Veil이 잘못된 원문 위치를 반환했습니다: {start}, {end}')
            spans.append(Span(start, end, LABEL_MAP[item['label']], text[start:end]))
        return sorted(spans, key=lambda s: (s.start, s.end, s.label))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--download', action='store_true', help='고정 버전의 ONNX·토크나이저·공식 코드 다운로드')
    args = parser.parse_args()
    if args.download:
        prepare_model()
    detector = VeilDetector()
    text = '담당자 김철수(010-1234-5678)에게 문의 바랍니다.'
    print(f'입력: {text}')
    for span in detector.detect(text):
        print(span.to_dict())


if __name__ == '__main__':
    main()
