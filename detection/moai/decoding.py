"""NumPy CPU port of OpenAI's BIOES ViterbiCRFDecoder.

Reference: openai/privacy-filter, f7f00ca7fb869683eb732c010299d901457f19c3,
opf/_core/decoding.py (Apache-2.0). See NOTICE and test_reference.py.
The finite -1e9 masks, FP32 arithmetic, first-index ties, end scores and
nonfinite fallback deliberately match the reference CPU implementation.
"""
import json

import numpy as np

BIAS_KEYS = tuple('transition_bias_' + key for key in (
    'background_stay', 'background_to_start', 'inside_to_continue',
    'inside_to_end', 'end_to_background', 'end_to_start'))


def load_biases(path):
    data = json.loads(path.read_text(encoding='utf-8'))
    def exact(obj, keys):
        if not isinstance(obj, dict) or set(obj) != set(keys):
            raise ValueError(f'Invalid calibration schema; expected {keys}')
    exact(data, ['operating_points'])
    exact(data['operating_points'], ['default'])
    exact(data['operating_points']['default'], ['biases'])
    biases = data['operating_points']['default']['biases']
    exact(biases, BIAS_KEYS)
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) for v in biases.values()):
        raise ValueError('Calibration biases must be numeric')
    return {k: float(v) for k, v in biases.items()}


class ViterbiDecoder:
    def __init__(self, id2label, biases):
        if set(id2label) != set(range(len(id2label))):
            raise ValueError('id2label must have contiguous integer IDs')
        self.labels = [id2label[i] for i in range(len(id2label))]
        if len(set(self.labels)) != len(self.labels) or self.labels.count('O') != 1:
            raise ValueError('Expected unique BIOES labels and one O')
        self.parts = [('', 'O') if lab == 'O' else tuple(lab.split('-', 1))
                      for lab in self.labels]
        groups = {}
        for tag, lab in self.parts:
            if lab == 'O':
                continue
            if tag not in {'B', 'I', 'E', 'S'}:
                raise ValueError('Expected BIOES label space')
            groups.setdefault(lab, set()).add(tag)
        if any(tags != set('BIES') for tags in groups.values()):
            raise ValueError('Incomplete BIOES label space')
        self.entity_labels = set(groups)
        n = len(self.labels)
        self.start = np.full(n, -1e9, np.float32)
        self.end = np.full(n, -1e9, np.float32)
        self.transitions = np.full((n, n), -1e9, np.float32)
        for i, (tag, lab) in enumerate(self.parts):
            if lab == 'O' or tag in {'B', 'S'}:
                self.start[i] = 0
            if lab == 'O' or tag in {'E', 'S'}:
                self.end[i] = 0
            for j, (next_tag, next_lab) in enumerate(self.parts):
                if lab == 'O' or tag in {'E', 'S'}:
                    valid = next_lab == 'O' or next_tag in {'B', 'S'}
                    key = ('background_' if lab == 'O' else 'end_')
                    key += ('stay' if lab == 'O' else 'to_background') if next_lab == 'O' else 'to_start'
                else:
                    valid = lab == next_lab and next_tag in {'I', 'E'}
                    key = 'inside_to_continue' if next_tag == 'I' else 'inside_to_end'
                if valid:
                    self.transitions[i, j] = biases['transition_bias_' + key]

    def decode(self, logprobs):
        emissions = np.asarray(logprobs, dtype=np.float32)
        if emissions.ndim != 2 or emissions.shape[1] != len(self.labels):
            raise ValueError('Expected [seq_len, num_classes] scores')
        if not len(emissions):
            return []
        scores = emissions[0] + self.start
        back = np.empty((len(emissions)-1, len(self.labels)), dtype=np.int64)
        for i in range(1, len(emissions)):
            transitions = scores[:, None] + self.transitions
            back[i-1] = transitions.argmax(axis=0)
            scores = transitions.max(axis=0) + emissions[i]
        # Reference-only degenerate fallback; ordinary inference always uses DP.
        if not np.isfinite(scores).any():
            return emissions.argmax(axis=1).tolist()
        last = int((scores + self.end).argmax())
        path = [last]
        for i in range(len(emissions)-2, -1, -1):
            last = int(back[i, last])
            path.append(last)
        return path[::-1]
