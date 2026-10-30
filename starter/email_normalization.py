"""탐지된 이메일 값의 승인된 회피 표기만 복원한다."""
import re


_AT = re.compile(r'\(at\)|\[at\]|\s+at\s+|골뱅이', re.IGNORECASE)
_DOT = re.compile(r'\s*(?:\(dot\)|\[dot\]|(?<=\s)dot(?=\s)|닷|점|\.)\s*', re.IGNORECASE)
_LOCAL = re.compile(r'[A-Za-z0-9._%+\-]+')
_DOMAIN = re.compile(r'[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+')
_ESCAPED_DOT = re.compile(r'\(dot\)|\[dot\]|\sdot\s|닷|점', re.IGNORECASE)


def normalize_email(value):
    if '@' in value:
        local, domain = value.rsplit('@', 1)
        # 일반 표기의 로컬 부분은 바꾸지 않는다. 공백이 있는 인용형은 미지원이다.
        if not local or not domain or re.search(r'\s', local):
            raise ValueError('잘못되었거나 아직 지원하지 않는 이메일 표기')
        if _ESCAPED_DOT.search(domain):
            domain = _restore_domain(domain.strip())
        elif re.search(r'\s', domain):
            raise ValueError('잘못되었거나 아직 지원하지 않는 이메일 표기')
        return f'{local}@{domain.lower()}'

    parts = _AT.split(value, maxsplit=1)
    if len(parts) != 2:
        raise ValueError('아직 지원하지 않는 이메일 표기')
    local, domain = (part.strip() for part in parts)
    if not _LOCAL.fullmatch(local):
        raise ValueError('아직 지원하지 않는 이메일 로컬 표기')
    return f'{local}@{_restore_domain(domain).lower()}'


def _restore_domain(domain):
    domain = _DOT.sub('.', domain)
    if domain.endswith('컴'):
        domain = domain[:-1] + 'com'
    elif domain.endswith('넷'):
        domain = domain[:-1] + 'net'
    if not _DOMAIN.fullmatch(domain):
        raise ValueError('아직 지원하지 않는 이메일 도메인 표기')
    return domain
