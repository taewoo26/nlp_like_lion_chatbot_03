"""주소 비교값 정규화와 원문에서 표시할 시도·시군구 범위.

외부 주소 API를 호출하지 않는다. 지역 표시는 동봉한 행정구역 목록으로 확인하며,
목록에 없는 지역은 추측해서 노출하지 않는다. 비교값의 미확인 요소는 보존한다.
"""
import re
import unicodedata
import json
from pathlib import Path


_SIDO_GROUPS = {
    "서울특별시": ("서울특별시", "서울시", "서울"),
    "부산광역시": ("부산광역시", "부산시", "부산"),
    "대구광역시": ("대구광역시", "대구시", "대구"),
    "인천광역시": ("인천광역시", "인천시", "인천"),
    "광주광역시": ("광주광역시", "광주"),
    "대전광역시": ("대전광역시", "대전시", "대전"),
    "울산광역시": ("울산광역시", "울산시", "울산"),
    "세종특별자치시": ("세종특별자치시", "세종시", "세종"),
    "경기도": ("경기도", "경기"),
    "강원특별자치도": ("강원특별자치도", "강원도", "강원"),
    "충청북도": ("충청북도", "충북"), "충청남도": ("충청남도", "충남"),
    "전북특별자치도": ("전북특별자치도", "전라북도", "전북"),
    "전라남도": ("전라남도", "전남"),
    "경상북도": ("경상북도", "경북"), "경상남도": ("경상남도", "경남"),
    "제주특별자치도": ("제주특별자치도", "제주도", "제주"),
}
SIDO = {alias: official for official, aliases in _SIDO_GROUPS.items() for alias in aliases}
_SIDO_ALT = "|".join(sorted(SIDO, key=len, reverse=True))
_SIDO_RE = re.compile(rf"(?:{_SIDO_ALT})(?![가-힣A-Za-z0-9])")
_POSTCODE = re.compile(
    rf"^\s*(?:\([0-9]{{5}}\)|[0-9]{{5}})\s*(?=(?:{_SIDO_ALT})(?![가-힣A-Za-z0-9]))"
)
_WIDTH = {ord(chr(0xFF10 + i)): str(i) for i in range(10)}
_WIDTH.update(str.maketrans({"　": " ", "－": "-", "（": "(", "）": ")", "，": ","}))
_ROAD = re.compile(r"([가-힣][가-힣A-Za-z0-9]*(?:로|길))\s*([0-9]+(?:-[0-9]+)?)(?![0-9가-힣])")
_DETAIL = re.compile(r"([A-Za-z0-9]+)\s*(동|호|층)(?![가-힣])")
_DETAIL_START = re.compile(r"[A-Za-z0-9]+\s*(?:동|호|층)(?![가-힣])")
_JIBEON = re.compile(r"[가-힣]+(?:동|리|읍|면)\s+[0-9]+")
_REFERENCE = re.compile(r"\(([가-힣]{1,12}(?:동|가|리))\)\s*$")


def _admin_pattern(names, leading=''):
    choices = [re.escape(name).replace(r'\ ', r'[\s,]+')
               for name in sorted(names, key=len, reverse=True)]
    return re.compile(leading + '(?:' + '|'.join(choices) + r')(?![가-힣A-Za-z0-9])') if choices else None


# 원본의 출처·내용 해시·목록 한계는 JSON에 함께 기록한다. 최신 행정구역의 완전한 목록은 아니다.
_ADMIN_DATA = json.loads(Path(__file__).with_name('admin_regions.json').read_text(encoding='utf-8'))
_ADMIN_PATTERNS = {sido: _admin_pattern(names, r'[\s,]+')
                   for sido, names in _ADMIN_DATA['regions'].items()}
_LOCAL_ADMIN = _admin_pattern({name for names in _ADMIN_DATA['regions'].values() for name in names})


def region_bounds(address):
    """원문에서 보존할 지역명의 [start,end). 추정할 수 없으면 None.

    한 글자를 한 글자로 바꾸는 전각 변환만 사용하므로 원문 인덱스를 유지한다.
    """
    text = address.translate(_WIDTH)
    postcode = _POSTCODE.match(text)
    start = postcode.end() if postcode else len(text) - len(text.lstrip())
    sido = _SIDO_RE.match(text, start)
    if sido:
        end = sido.end()
        pattern = _ADMIN_PATTERNS.get(SIDO[sido.group()])
        admin = pattern.match(text, end) if pattern else None
        if admin:
            end = admin.end()
        return start, end
    admin = _LOCAL_ADMIN.match(text, start)
    return (start, admin.end()) if admin else None


def normalize_address(value):
    value = unicodedata.normalize("NFC", value).translate(_WIDTH).strip()
    if not value:
        raise ValueError("주소 비교값이 비어 있음")
    postcode = _POSTCODE.match(value)
    if postcode:
        value = value[postcode.end():]
    sido = _SIDO_RE.match(value)
    if sido:
        value = SIDO[sido.group()] + value[sido.end():]

    # 지리 사전 없이 법정동 여부를 확정하지 않는다. 이미 본문에 같은 주소
    # 요소가 있는 중복 참고항목만 제거하고 건물·동 번호·미확인 괄호는 보존한다.
    reference = _REFERENCE.search(value)
    if reference and re.search(
        rf"(?<![가-힣A-Za-z0-9]){re.escape(reference.group(1))}(?![가-힣A-Za-z0-9])",
        value[:reference.start()],
    ):
        value = value[:reference.start()].rstrip()

    korean_form = bool(sido or _ROAD.search(value) or _DETAIL_START.search(value) or _JIBEON.search(value))
    if korean_form:
        parts = re.split(r"(\([^()]*\))", value)
        value = "".join(part if part.startswith("(") and part.endswith(")")
                        else _normalize_elements(part) for part in parts)
    return re.sub(r"\s+", " ", value).strip()


def _normalize_elements(value):
    value = value.replace(",", " ")
    value = re.sub(r"(?<=[0-9])\s*번지(?=$|[\s()])", "", value)
    value = re.sub(r"(?<=[0-9])\s*-\s*(?=[0-9])", "-", value)
    value = _ROAD.sub(r"\1 \2", value)
    value = _DETAIL.sub(r"\1\2", value)
    return re.sub(r"(?<=[A-Za-z0-9]동)\s*(?=[0-9])", " ", value)
