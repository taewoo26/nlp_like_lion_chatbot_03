"""모델 + rule_kiwi 결합 탐지기 (실험용). 합치는 규칙은 merge_spans 한 곳에만 둬요.

라벨별 모드: 'union' 둘 다 사용(겹치면 규칙 우선) / 'rule' 규칙만 / 'model' 모델만
정책은 dev에서 정하고, test는 확인에만 써요.
"""
from detection.base import BaseDetector
from detection.validation import check_spans

STRUCTURED = ('email', 'resident_registration_number', 'card_number',
              'account_number', 'phone_number')
POLICIES = {
    'union': {},                                        # 모든 라벨 합집합
    'split': {label: 'rule' for label in STRUCTURED},   # 번호류는 규칙, 이름·주소는 합집합
}


def _overlap(a, b):
    return a.start < b.end and b.start < a.end


def merge_spans(rule_spans, model_spans, modes):
    """규칙 구간을 먼저 넣고, 겹치지 않는 모델 구간만 추가해요."""
    chosen = [s for s in rule_spans if modes.get(s.label, 'union') != 'model']
    for s in model_spans:
        if modes.get(s.label, 'union') == 'rule':
            continue
        if not any(_overlap(s, c) for c in chosen):
            chosen.append(s)
    return sorted(chosen, key=lambda s: (s.start, s.end, s.label))


def _build_model(name):
    if name == 'koelectra':
        from detection.token_cls.koelectra import KoElectraDetector
        return KoElectraDetector()
    if name == 'koelectra_post':
        from detection.token_cls.koelectra_post import KoElectraPostDetector
        return KoElectraPostDetector()
    if name == 'veil':
        from detection.token_cls.veil import VeilDetector
        return VeilDetector()
    if name == 'moai':
        from detection.token_cls.moai import MoAIDetector
        return MoAIDetector()
    raise ValueError(f'모르는 모델: {name}')


class ComboDetector(BaseDetector):
    def __init__(self, model_name, policy='union'):
        if policy not in POLICIES:
            raise ValueError(f'모르는 정책: {policy}')
        from detection.rule.detector import RuleDetector
        self.model = _build_model(model_name)
        self.rule = RuleDetector(use_kiwi=True)
        self.rule.detect('김민준 씨에게 연락했다.')   # Kiwi 로딩을 시간 측정에서 제외
        self.modes = POLICIES[policy]
        self.name = f'{model_name}_kiwi' + ('' if policy == 'union' else f'_{policy}')

    def detect(self, text):
        merged = merge_spans(self.rule.detect(text), self.model.detect(text), self.modes)
        return check_spans(text, merged)
