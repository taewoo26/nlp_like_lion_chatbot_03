"""Frozen KoELECTRA inference; no lexical, regex or boundary corrections."""
from dataclasses import asdict, dataclass

from detection.base import BaseDetector, Span

MODEL_ID = "atonlee/koelectra-ko-pii-ner"
REVISION = "1e75c01e707232401883cf364151bbe2e560c708"
LABEL_MAP = {
    "NAME": "person", "PHONE": "phone_number", "EMAIL": "email",
    "ADDRESS": "address", "RRN": "resident_registration_number",
    "ACCOUNT_NUMBER": "account_number", "CARD_NUMBER": "card_number",
}


def validate_spans(text, spans, allowed):
    seen = set()
    for span in spans:
        key = (span.label, span.start, span.end)
        if (span.label not in allowed or type(span.start) is not int
                or type(span.end) is not int
                or not 0 <= span.start < span.end <= len(text)
                or span.text != text[span.start:span.end] or key in seen):
            raise ValueError(f"Invalid or duplicate span: {span}")
        seen.add(key)


def decode_bio(text, tokens):
    """Decode in original token order. Orphan/type-changing I starts an entity.

    B always starts a new entity, even next to the same type. Internal spaces
    are preserved by slicing the source. No stripping of suffixes/punctuation.
    """
    spans, current = [], None
    for token in tokens:
        tag, start, end = token['tag'], token['start'], token['end']
        prefix, _, label = tag.partition('-')
        if tag != 'O' and (prefix not in ('B', 'I') or not label):
            raise ValueError(f"Invalid BIO tag: {tag}")
        continues = (prefix == 'I' and current is not None and current[0] == label)
        if not continues and current is not None:
            lab, s, e = current
            spans.append(Span(s, e, lab, text[s:e]))
            current = None
        if tag != 'O':
            current = (label, current[1] if continues else start, end)
    if current is not None:
        lab, s, e = current
        spans.append(Span(s, e, lab, text[s:e]))
    # Stable deduplication; never join spans by string or overlap heuristics.
    return list({(s.label, s.start, s.end): s for s in spans}.values())


@dataclass
class Prediction:
    spans: list[Span]
    raw_entities: list[Span]
    tokens: list[dict]
    windows: int

    def raw_dict(self):
        return {"entities": [asdict(s) for s in self.raw_entities],
                "unmapped_entities": [asdict(s) for s in self.raw_entities if s.label not in LABEL_MAP],
                "tokens": self.tokens, "windows": self.windows}


class TokenClassificationDetector(BaseDetector):
    name = "koelectra_token_cls"

    def __init__(self, *, cache_dir="models/huggingface", local_files_only=False,
                 max_length=512, stride=128, threads=1):
        import torch
        from transformers import AutoModelForTokenClassification, AutoTokenizer

        if threads < 1:
            raise ValueError("threads must be positive")
        torch.set_num_threads(threads)
        torch.manual_seed(0)
        torch.use_deterministic_algorithms(True)
        self.torch = torch
        options = dict(revision=REVISION, cache_dir=cache_dir,
                       local_files_only=local_files_only, trust_remote_code=False)
        self.tokenizer = AutoTokenizer.from_pretrained(MODEL_ID, use_fast=True, **options)
        if not self.tokenizer.is_fast:
            raise ValueError("Fast tokenizer is required for character offsets")
        self.model = AutoModelForTokenClassification.from_pretrained(
            MODEL_ID, use_safetensors=True, **options).to('cpu').eval()
        limit = min(self.model.config.max_position_embeddings, self.tokenizer.model_max_length)
        capacity = max_length - self.tokenizer.num_special_tokens_to_add(pair=False)
        if not 4 <= max_length <= limit or not 0 <= stride < capacity:
            raise ValueError("Invalid max_length/stride")
        self.max_length, self.stride, self.threads = max_length, stride, threads
        self.raw_labels = {x[2:] for x in self.model.config.id2label.values() if x != 'O'}
        if len(self.raw_labels) != 29 or not LABEL_MAP.keys() <= self.raw_labels:
            raise ValueError("Unexpected model label schema")

    def predict(self, text: str) -> Prediction:
        if not isinstance(text, str):
            raise TypeError("text must be a string")
        encoded = self.tokenizer(
            text, truncation=True, max_length=self.max_length, stride=self.stride,
            return_overflowing_tokens=True, return_offsets_mapping=True,
            return_special_tokens_mask=True, padding=False)
        selected = {}
        for w, offsets in enumerate(encoded['offset_mapping']):
            inputs = {k: self.torch.tensor([encoded[k][w]])
                      for k in self.tokenizer.model_input_names if k in encoded}
            with self.torch.inference_mode():
                probabilities = self.model(**inputs).logits[0].softmax(-1)
                scores, ids = probabilities.max(-1)
            valid = [i for i, (s, e) in enumerate(offsets)
                     if s < e and not encoded['special_tokens_mask'][w][i]]
            for rank, i in enumerate(valid):
                s, e = offsets[i]
                if not 0 <= s < e <= len(text):
                    raise ValueError("Tokenizer produced invalid offsets")
                # Resolve repeated tokens by most surrounding context. Ties
                # retain the earlier window, independent of prediction scores.
                context = min(rank, len(valid) - rank - 1)
                token = dict(start=s, end=e, text=text[s:e],
                             tag=self.model.config.id2label[int(ids[i])],
                             confidence=float(scores[i]), window=w)
                if (s, e) not in selected or context > selected[(s, e)][0]:
                    selected[(s, e)] = (context, token)
        tokens = [v[1] for _, v in sorted(selected.items())]
        raw = decode_bio(text, tokens)
        validate_spans(text, raw, self.raw_labels)
        mapped = [Span(s.start, s.end, LABEL_MAP[s.label], s.text)
                  for s in raw if s.label in LABEL_MAP]
        validate_spans(text, mapped, set(LABEL_MAP.values()))
        return Prediction(mapped, raw, tokens, len(encoded['input_ids']))

    def detect(self, text: str) -> list[Span]:
        return self.predict(text).spans
