"""Rule-only financial recovery: candidate shape, local context and model spans.

No gold, document IDs, model calls or excluded-label allowlist. Known mapped
PII of another type is never replaced. Unsupported/ambiguous candidates stay
unchanged. Audit records contain positions/reasons, never source text.
"""
import re

from detection.base import Span
from detection.token_cls.detector import LABEL_MAP, validate_spans

VERSION = 'financial_context_v2'
PHONE_BODY = (
    r'(?:02[ .-]?[0-9]{3,4}[ .-]?[0-9]{4}'
    r'|(?:010|070)[ .-]?[0-9]{4}[ .-]?[0-9]{4}'
    r'|(?:031|032|033|041|042|043|044|051|052|053|054|055|061|062|063|064)'
    r'[ .-]?[0-9]{3,4}[ .-]?[0-9]{4})')
PHONE = re.compile(r'(?<![0-9A-Za-z.-])' + PHONE_BODY + r'(?![0-9A-Za-z]|[.-][0-9])')
NUMBER = re.compile(r'(?<![0-9A-Za-z-])[0-9]+(?:-[0-9]+){0,3}(?![0-9A-Za-z-])')
CONTACT = re.compile(r'전화(?:번호)?|연락처|휴대전화|휴대폰|직통(?:번호)?|팩스|(?i:\btel\b|\bphone\b)')
ACCOUNT = re.compile(r'계좌(?:\s*번호)?')
OTHER_FIELD = re.compile(r'민원|접수|주문|운전\s*면허|사업자|주민|카드|계좌')
NON_ACCOUNT = re.compile(r'민원|접수|주문|운전\s*면허|사업자|주민|카드|전화|연락처')
COMPANY = re.compile(r'대표\s*(?:전화|번호)|고객\s*(?:센터|상담|지원)|콜\s*센터|공용|문의\s*(?:전화|처)')
FIELD_BREAK = re.compile(r'[|;；\t!?。]|\.(?=\s)')


def _overlap(a, b):
    return a.start < b.end and b.start < a.end


def _inside(a, b):
    return b.start <= a.start < a.end <= b.end


def _bounds(text, candidate):
    line = text.rfind('\n', 0, candidate.start) + 1
    end = text.find('\n', candidate.end)
    end = len(text) if end < 0 else end
    separators = list(FIELD_BREAK.finditer(text, line, candidate.start))
    field = separators[-1].end() if separators else line
    return line, end, text[max(field, candidate.start - 48):candidate.start]


def _personal_card(text, candidate, raw):
    line, end, _ = _bounds(text, candidate)
    previous = text.rfind('\n', 0, max(0, line - 1)) + 1
    # NAME/EMAIL are positive neighboring context, not candidate error labels.
    return (any(e.label == 'EMAIL' and line <= e.start < e.end <= end for e in raw)
            and any(e.label == 'NAME' and max(previous, candidate.start - 100)
                    <= e.start < e.end <= candidate.start for e in raw))


def _raw_evidence(candidate, raw):
    overlapping = [e for e in raw if _overlap(e, candidate)]
    if any(not _inside(e, candidate) for e in overlapping):
        return None  # A larger model entity must not be cut/retyped.
    return {i for e in overlapping for i in range(e.start, e.end)}


def _digits(text, span):
    return {i for i in range(span.start, span.end) if '0' <= text[i] <= '9'}


def recover_financial_spans(text, mapped, raw, *, phone=True, account=True):
    """Return independent spans plus candidate decisions (including rejections).

    Fully raw-supported phone candidates need a direct phone field or personal
    contact card. Zero-entity candidates need BOTH. Partial raw support is
    insufficient. Account extension requires a seed covering >=half the
    digits, an account field, and model support for all added digits; support
    labels are unrestricted. No normal mapped prediction is removed.
    """
    validate_spans(text, mapped, set(LABEL_MAP.values()))
    validate_spans(text, raw, {e.label for e in raw})
    result = [Span(e.start, e.end, e.label, e.text) for e in mapped]
    audit = []

    def record(candidate, action, reason, previous=()):
        audit.append(dict(label=candidate.label, start=candidate.start, end=candidate.end,
                          action=action, reason=reason,
                          previous=[dict(start=e.start, end=e.end, label=e.label) for e in previous]))

    if phone:
        for match in PHONE.finditer(text):
            candidate = Span(match.start(), match.end(), 'phone_number', match.group())
            overlaps = [p for p in result if _overlap(p, candidate)]
            if any(p.label == 'phone_number' and p.start == candidate.start
                   and p.end == candidate.end for p in overlaps):
                record(candidate, 'keep', 'existing_phone')
                continue
            if overlaps:
                record(candidate, 'reject', 'mapped_conflict')
                continue
            if re.match(r'[ .-][0-9]', text[candidate.end:]):
                record(candidate, 'reject', 'adjacent_number')
                continue
            line, _, prefix = _bounds(text, candidate)
            # Check the full local line prefix so a pipe cannot hide a company
            # heading. Never inherit a heading from a separate line.
            if (COMPANY.search(text[max(line, candidate.start - 80):candidate.start])
                    or OTHER_FIELD.search(prefix)):
                record(candidate, 'reject', 'excluded_or_other_field')
                continue
            markers = list(CONTACT.finditer(prefix))
            direct = bool(markers and re.fullmatch(r'[\s:：=()\-]{0,16}', prefix[markers[-1].end():]))
            card = _personal_card(text, candidate, raw)
            evidence = _raw_evidence(candidate, raw)
            if evidence is None:
                record(candidate, 'reject', 'raw_crosses_candidate')
                continue
            supported = _digits(text, candidate) <= evidence
            if evidence and supported and (direct or card):
                reason = 'phone_model_supported'
            elif not evidence and direct and card:
                reason = 'phone_unseen_strong_context'
            else:
                record(candidate, 'reject', 'insufficient_context_or_model_support')
                continue
            result.append(candidate)
            record(candidate, 'add', reason)

    if account:
        for match in NUMBER.finditer(text):
            candidate = Span(match.start(), match.end(), 'account_number', match.group())
            overlaps = [(i, p) for i, p in enumerate(result) if _overlap(p, candidate)]
            if not any(p.label == 'account_number' for _, p in overlaps):
                continue  # V2 does not invent account seeds.
            if len(overlaps) != 1:
                record(candidate, 'reject', 'mapped_conflict')
                continue
            index, seed = overlaps[0]
            if seed.start == candidate.start and seed.end == candidate.end:
                record(candidate, 'keep', 'existing_account')
                continue
            groups = candidate.text.split('-')
            digits = _digits(text, candidate)
            if (not _inside(seed, candidate) or not 10 <= len(digits) <= 16
                    or (len(groups) > 1 and not all(2 <= len(g) <= 6 for g in groups))
                    or len(_digits(text, seed)) < len(digits) / 2):
                record(candidate, 'reject', 'account_shape_or_seed_strength')
                continue
            _, _, prefix = _bounds(text, candidate)
            markers = list(ACCOUNT.finditer(prefix))
            if (not markers or NON_ACCOUNT.search(prefix)
                    or not re.fullmatch(r'[\s:：=(]*[가-힣A-Za-z\s]{0,24}[\s(]*',
                                        prefix[markers[-1].end():])):
                record(candidate, 'reject', 'account_context')
                continue
            evidence = _raw_evidence(candidate, raw)
            if evidence is None or not (digits - _digits(text, seed)) <= evidence:
                record(candidate, 'reject', 'insufficient_extension_evidence')
                continue
            result[index] = candidate
            record(candidate, 'expand', 'account_model_supported', [seed])
    validate_spans(text, result, set(LABEL_MAP.values()))
    return result, audit
