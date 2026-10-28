"""모델 + rule_kiwi 결합 탐지기 (실험용). 합치는 규칙은 merge_spans 한 곳에만 둬요.

라벨별 모드: 'union' 둘 다 사용(겹치면 규칙 우선) / 'rule' 규칙만 / 'model' 모델만
정책은 dev에서 정하고, test는 확인에만 써요.
"""
from detection.base import BaseDetector
from detection.validation import check_spans
from detection.filters import clean, filter_model_names
from detection import extra_rules as v2

STRUCTURED = ('email', 'resident_registration_number', 'card_number',
              'account_number', 'phone_number')
POLICIES = {
    'union': {},                                        # 모든 라벨 합집합
    'split': {label: 'rule' for label in STRUCTURED},   # 번호류는 규칙, 이름·주소는 합집합
    'split_clean': {label: 'rule' for label in STRUCTURED},   # split + 고객사 기준 후처리
    'split_v2': {},   # 번호 보강 규칙 + 모양 검사 통과한 모델 번호도 사용 (detection/extra_rules.py)
    'split_v3': {},   # split_v2 + Veil (두 모델 동의로 애매한 이름 판단)
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
        self.policy = policy
        self.model2 = None
        if policy == 'split_v3':
            from detection.token_cls.veil import VeilDetector
            self.model2 = VeilDetector()
        self.name = f'{model_name}_kiwi' + ('' if policy == 'union' else f'_{policy}')

    def detect(self, text):
        rule_spans = self.rule.detect(text)
        model_spans = self.model.detect(text)
        if self.policy == 'split_v2':
            return check_spans(text, self._detect_v2(text, rule_spans, model_spans))
        if self.policy == 'split_v3':
            return check_spans(text, self._detect_v3(text, rule_spans, model_spans, self._veil(text, model_spans)))
        if self.modes is POLICIES['split_clean']:
            model_spans = filter_model_names(text, model_spans, rule_spans)
        merged = merge_spans(rule_spans, model_spans, self.modes)
        if self.modes is POLICIES['split_clean']:
            merged = clean(text, merged)
        return check_spans(text, merged)

    def _detect_v2(self, text, rule_spans, model_spans):
        rule_spans = v2.merge_extra(rule_spans, v2.extra_spans(text))     # ① 번호·주소·항목명 뒤 이름 보강
        raw_model = model_spans
        model_spans = v2.filter_model_v2(text, model_spans)                # ② 모델 결과는 모양이 틀린 것만 제거
        merged = merge_spans(rule_spans, model_spans, self.modes)          # ③ 규칙 우선 합치기 (번호도 모델 허용)
        merged = v2.widen_names(text, merged, raw_model)                   # ④ 모델이 더 길게 잡은 이름 (마리나 페트로바)
        merged = [v2.tidy_name(text, s) for s in merged]                   # ⑤ '시우 드림', '박별이' 정리
        merged = v2.drop_name_words(merged)                                # ⑥ 조모상·배송지·채무자, 가려진 값
        merged = v2.propagate_names(text, merged)                          # ⑥-2 확정된 짧은 이름을 문서 전체에
        return clean(text, merged)                                         # ⑦ 고객사 기준 (성+직함·대표번호·지점)

    def _detect_v3(self, text, rule_spans, k_raw, v_raw):
        rule_spans = v2.merge_extra(rule_spans, v2.extra_spans(text))     # ① 보강 규칙 (v2와 동일)
        model_spans = v2.combine_models(text, k_raw, v_raw)               # ② KoELECTRA + Veil (동의로 애매한 이름 판단)
        merged = merge_spans(rule_spans, model_spans, self.modes)          # ③ 규칙 우선 합치기
        merged = v2.widen_addresses(merged, model_spans)                   # ③-2 KoELECTRA가 주소를 더 길게 잡았으면 채택
        merged = v2.widen_names(text, merged, k_raw + v_raw)               # ④ 이름 넓히기
        merged = [v2.tidy_name(text, s) for s in merged]                   # ⑤ 이름 앞뒤 정리
        merged = v2.drop_name_words(merged)                                # ⑥ 역할어·경조사·가려진 값
        merged = v2.propagate_names(text, merged)                          # ⑥-2 확정된 짧은 이름 전파
        merged = clean(text, merged)                                       # ⑦ 고객사 기준
        return v2.apply_exclusions(text, merged)                           # ⑧ 규칙 정의서 제외규칙 (마스킹·공용메일·법인·우편번호)

    def _veil(self, text, k_raw):
        """Veil은 필요한 줄에만 실행 (VEIL_FULL=1 이면 예전처럼 문서 전체)."""
        import os
        from detection.base import Span
        if os.environ.get('VEIL_FULL'):
            return self.model2.detect(text)
        out = []
        for s, e in v2.veil_chunks(text, k_raw):
            for sp in self.model2.detect(text[s:e]):
                out.append(Span(sp.start + s, sp.end + s, sp.label, text[sp.start + s:sp.end + s]))
        return out