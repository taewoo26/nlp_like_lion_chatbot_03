"""IBAN의 국가별 길이로 원문 구간을 찾는다. 체크섬은 탐지 제외 조건이 아니다.

길이 기준: SWIFT IBAN Registry release 103 (September 2026).
https://www.swift.com/swift-resource/9606/download
공백은 허용하되 줄바꿈이나 다음 필드까지 이어 붙이지 않는다.
"""
import re

from detection.base import Span


IBAN_LENGTHS = {
    'AD': 24, 'AE': 23, 'AL': 28, 'AT': 20, 'AZ': 28,
    'BA': 20, 'BE': 16, 'BG': 22, 'BH': 22, 'BI': 27,
    'BR': 29, 'BY': 28, 'CH': 21, 'CR': 22, 'CY': 28, 'CZ': 24,
    'DE': 22, 'DJ': 27, 'DK': 18, 'DO': 28, 'EE': 20, 'EG': 29,
    'ES': 24, 'FI': 18, 'FK': 18, 'FO': 18, 'FR': 27,
    'GB': 22, 'GE': 22, 'GI': 23, 'GL': 18, 'GR': 27, 'GT': 28,
    'HN': 28, 'HR': 21, 'HU': 28, 'IE': 22, 'IL': 23, 'IQ': 23,
    'IS': 26, 'IT': 27, 'JO': 30, 'KW': 30, 'KZ': 20, 'LB': 28,
    'LC': 32, 'LI': 21, 'LT': 20, 'LU': 20, 'LV': 21, 'LY': 25,
    'MC': 27, 'MD': 24, 'ME': 22, 'MK': 19, 'MN': 20, 'MR': 27,
    'MT': 31, 'MU': 30, 'NI': 28, 'NL': 18, 'NO': 15, 'OM': 23,
    'PK': 24, 'PL': 28, 'PS': 29, 'PT': 25, 'QA': 29, 'RO': 24,
    'RS': 22, 'RU': 33, 'SA': 24, 'SC': 31, 'SD': 18, 'SE': 24,
    'SI': 19, 'SK': 24, 'SM': 27, 'SO': 23, 'ST': 25, 'SV': 28,
    'TL': 23, 'TN': 24, 'TR': 26, 'UA': 29, 'VA': 22, 'VG': 24,
    'XK': 20, 'YE': 30,
}
_START = re.compile(r'(?<![A-Za-z0-9_@])([A-Za-z]{2})[0-9]{2}')
_TAILS = {
    country: re.compile(rf'(?:[ \t]*[A-Za-z0-9]){{{length - 4}}}(?![A-Za-z0-9_@])')
    for country, length in IBAN_LENGTHS.items()
}


def iban_spans(text):
    """정규화하지 않은 원문에서 국가코드를 포함한 계좌 span을 반환한다."""
    spans = []
    for start in _START.finditer(text):
        tail = _TAILS.get(start.group(1).upper())
        match = tail.match(text, start.end()) if tail else None
        if match:
            spans.append(Span(start.start(), match.end(), 'account_number',
                              text[start.start():match.end()]))
    return spans
