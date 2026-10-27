"""모델 + rule_kiwi 결합 탐지기 (실험용). 합치는 규칙은 merge_spans 한 곳에만 둬요.

라벨별 모드: 'union' 둘 다 사용(겹치면 규칙 우선) / 'rule' 규칙만 / 'model' 모델만
정책은 dev에서 정하고, test는 확인에만 써요.
"""
import re

from detection.base import BaseDetector
from detection.rule.kiwi_filter import _get_kiwi
from detection.rule.names import SURNAMES, has_strong_cue
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
}

JOB_TITLE = (r"(?:팀장|부장|차장|과장|대리|사원|주임|책임|선임|수석|이사|상무|전무|대표|"
             r"실장|본부장|센터장|매니저)")
SURNAME_TITLE = re.compile(
    rf"\s+{JOB_TITLE}(?:님)?(?:께서|에게|으로|부터|까지|은|는|이|가|을|를|도|과|와|로|께)?(?![가-힣])"
)
SURNAME_TITLE_SPAN = re.compile(rf"^[{SURNAMES}]\s+{JOB_TITLE}(?:님)?$")


def drop_surname_title(text, spans):
    """성 한 글자 또는 성+직함만 이름으로 탐지한 결과를 제거한다."""
    return [s for s in spans if not (
        s.label == 'person' and (
            (len(s.text) == 1 and s.text in SURNAMES and SURNAME_TITLE.match(text, s.end))
            or SURNAME_TITLE_SPAN.fullmatch(s.text)
        )
    )]


def drop_contextual_non_names(text, model_spans, rule_spans=()):
    """규칙이 확인하지 못한 모델 이름을 형태·필드 문맥으로 검토한다."""
    extensions = list(re.finditer(r"내선", text))
    route_fields = []
    for match in re.finditer(r"(?m)^[ \t]*접수[ \t]*경로[ \t]*[:：]", text):
        end = text.find('\n', match.end())
        route_fields.append((match.end(), len(text) if end == -1 else end))
    tokens = None
    kept = []
    for span in model_spans:
        if span.label != 'person' or any(_overlap(span, rule) for rule in rule_spans):
            kept.append(span)
            continue
        if any(m.start() <= span.start and span.end <= m.end() for m in extensions):
            continue
        if not has_strong_cue(text, span):
            if tokens is None:
                tokens = _get_kiwi().tokenize(text)
            is_verb = any(t.tag.startswith('V') and t.start == span.start and
                          t.start + t.len <= span.end for t in tokens)
            in_route = any(start <= span.start and span.end <= end
                           for start, end in route_fields)
            is_route_noun = in_route and any(
                t.tag == 'NNG' and t.start < span.end and span.start < t.start + t.len
                for t in tokens
            )
            line_start = text.rfind('\n', 0, span.start) + 1
            is_field_key = (
                not text[line_start:span.start].strip()
                and re.match(r"[ \t]*[:：]", text[span.end:]) is not None
                and any(t.start == span.start and t.start + t.len == span.end
                        and t.tag in {'NNG', 'JKO', 'JX'} for t in tokens)
            )
            if is_verb or is_route_noun or is_field_key:
                continue
        kept.append(span)
    return kept


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
        self.name = f'{model_name}_kiwi' + ('' if policy == 'union' else f'_{policy}')

    def detect(self, text):
        rule_spans = self.rule.detect(text)
        model_spans = self.model.detect(text)
        if self.policy == 'split_v2':
            return check_spans(text, self._detect_v2(text, rule_spans, model_spans))
        if self.policy in ('union', 'split'):
            model_spans = drop_contextual_non_names(text, model_spans, rule_spans)
            merged = merge_spans(rule_spans, model_spans, self.modes)
            return check_spans(text, drop_surname_title(text, merged))
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
