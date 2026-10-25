"""Conservative financial recovery from frozen raw entities, without gold access."""
import re

from detection.base import Span
from detection.token_cls.detector import LABEL_MAP, validate_spans

VERSION = 'financial_context_v1'
PHONE = re.compile(
    r'(?:02[ .-]?[0-9]{3,4}[ .-]?[0-9]{4}'
    r'|(?:010|070)[ .-]?[0-9]{4}[ .-]?[0-9]{4}'
    r'|(?:031|032|033|041|042|043|044|051|052|053|054|055|061|062|063|064)'
    r'[ .-]?[0-9]{3,4}[ .-]?[0-9]{4})')
NUMBER = re.compile(r'(?<![0-9A-Za-z-])[0-9]+(?:-[0-9]+){1,3}(?![0-9A-Za-z-])')
CONTACT = re.compile(r'전화|연락처|연락|휴대폰|휴대전화|직통|팩스|(?i:\btel\b|\bphone\b)')
ACCOUNT = re.compile(r'계좌(?:\s*번호)?')
OTHER_FIELD = re.compile(r'민원|접수|주문|운전\s*면허|사업자|주민|카드|계좌|대표\s*전화|고객\s*센터')
NON_ACCOUNT = re.compile(r'민원|접수|운전\s*면허|사업자|주민|카드')


def _overlap(a, b):
    return a.start < b.end and b.start < a.end


def _inside(inner, outer):
    return outer.start <= inner.start < inner.end <= outer.end


def _line_bounds(text, span):
    start, end = text.rfind('\n', 0, span.start) + 1, text.find('\n', span.end)
    return start, len(text) if end < 0 else end


def _phone_context(text, candidate, raw):
    start, end = _line_bounds(text, candidate)
    prefix = text[max(start, candidate.start - 48):candidate.start]
    if OTHER_FIELD.search(prefix):
        return False
    if CONTACT.search(prefix):
        return True
    # Personal contact card: email on this line, name on this/preceding line.
    previous = text.rfind('\n', 0, max(0, start - 1)) + 1
    return (any(e.label == 'EMAIL' and start <= e.start < e.end <= end for e in raw)
            and any(e.label == 'NAME' and max(previous, candidate.start - 100)
                    <= e.start < e.end <= candidate.start for e in raw))


def recover_financial_spans(text, mapped, raw, *, phone=True, account=True):
    """Return independent spans and position-only audit events.

    Phone: whole GENERIC_ID + full domestic format + contact context, with no
    mapped overlap. Account: one contained account seed + same-line context +
    ACCOUNT_NUMBER/DRIVER_LICENSE evidence covering every digit. Neither raw
    label is blindly remapped. Preserve baseline order/unrelated predictions.
    """
    validate_spans(text, mapped, set(LABEL_MAP.values()))
    validate_spans(text, raw, {e.label for e in raw})
    result = [Span(e.start, e.end, e.label, e.text) for e in mapped]
    events = []
    if phone:
        for e in raw:
            if e.label != 'GENERIC_ID' or not PHONE.fullmatch(e.text):
                continue
            if ((e.start and (text[e.start - 1].isalnum() or text[e.start - 1] in '-.'))
                    or (e.end < len(text) and ((text[e.end].isascii()
                        and text[e.end].isalnum()) or text[e.end] in '-.'))):
                continue
            if any(_overlap(e, p) for p in result) or not _phone_context(text, e, raw):
                continue
            result.append(Span(e.start, e.end, 'phone_number', e.text))
            events.append(dict(rule='phone_generic_contact', label='phone_number',
                               start=e.start, end=e.end, previous=[]))
    if account:
        for match in NUMBER.finditer(text):
            groups = match.group().split('-')
            if not (all(2 <= len(g) <= 6 for g in groups)
                    and 10 <= sum(map(len, groups)) <= 16):
                continue
            candidate = Span(match.start(), match.end(), 'account_number', match.group())
            seeds = [(i, p) for i, p in enumerate(result) if _overlap(candidate, p)]
            if len(seeds) != 1:
                continue
            index, seed = seeds[0]
            if (seed.label != 'account_number' or not _inside(seed, candidate)
                    or (seed.start, seed.end) == (candidate.start, candidate.end)):
                continue
            line, _ = _line_bounds(text, candidate)
            prefix = text[max(line, candidate.start - 48):candidate.start]
            markers = list(ACCOUNT.finditer(prefix))
            if (not markers or NON_ACCOUNT.search(prefix)
                    or re.search(r'[0-9]', prefix[markers[-1].end():])):
                continue
            evidence = [e for e in raw if _overlap(candidate, e)]
            if (not evidence or any(e.label not in ('ACCOUNT_NUMBER', 'DRIVER_LICENSE')
                                   or not _inside(e, candidate) for e in evidence)
                    or not any(e.label == 'ACCOUNT_NUMBER' and e.start == seed.start
                               and e.end == seed.end for e in evidence)
                    or not any(e.label == 'DRIVER_LICENSE' for e in evidence)):
                continue
            if any(not any(e.start <= pos < e.end for e in evidence)
                   for pos in range(candidate.start, candidate.end) if text[pos].isdigit()):
                continue
            result[index] = candidate
            events.append(dict(rule='account_adjacent_raw', label='account_number',
                               start=candidate.start, end=candidate.end,
                               previous=[dict(start=seed.start, end=seed.end)]))
    validate_spans(text, result, set(LABEL_MAP.values()))
    return result, events
