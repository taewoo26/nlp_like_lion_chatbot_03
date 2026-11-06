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
# 이름 오탐 판정용 단어 목록: 시도 별칭 + 시군구 ('천안시 서북구'는 '천안시', '서북구'로도 등록)
REGION_WORDS = frozenset(
    set(SIDO)
    | {part for names in _ADMIN_DATA['regions'].values() for name in names for part in [name, *name.split()]}
)


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

# ---------------------------------------------------------------- 지역 메타데이터 (규칙 정의서 REG)
# 시군구 이름 → 그 이름이 있는 시도들. '중구'처럼 여러 시도에 있으면 시도를 추측하지 않는다.
_SIGUNGU_OWNERS = {}
for _sido, _names in _ADMIN_DATA['regions'].items():
    for _name in _names:
        if not _name.endswith('출장소'):                 # 시군구가 아닌 행정 출장소는 제외
            _SIGUNGU_OWNERS.setdefault(_name, set()).add(_sido)

_REGION_JOSA = ('에서', '으로', '에', '로', '쪽', '의', '은', '는', '이', '가', '도')


def _clean_admin(name):
    return re.sub(r'[\s,]+', ' ', name).strip(' ,')


def regions_of(address):
    """주소 원문 → {'대구광역시', '대구광역시 수성구'} (REG-02·03·04·10·11·20).

    시도 단독 값도 함께 넣어 '대구 사는 고객'을 배열 포함 조건 하나로 찾게 한다.
    시도 없이 시군구만 있으면 전국에 하나뿐일 때만 시도를 채우고, 확정할 수 없으면 빈 집합.
    """
    text = unicodedata.normalize('NFC', address).translate(_WIDTH)
    postcode = _POSTCODE.match(text)
    start = postcode.end() if postcode else len(text) - len(text.lstrip())
    sido = _SIDO_RE.match(text, start)
    if sido:
        official = SIDO[sido.group()]
        out = {official}
        pattern = _ADMIN_PATTERNS.get(official)
        admin = pattern.match(text, sido.end()) if pattern else None
        if admin:
            name = _clean_admin(admin.group())
            if name in _SIGUNGU_OWNERS:
                out.add(f'{official} {name}')
        return out
    admin = _LOCAL_ADMIN.match(text, start)
    if admin:
        name = _clean_admin(admin.group())
        owners = _SIGUNGU_OWNERS.get(name, set())
        if len(owners) == 1:
            official = next(iter(owners))
            return {official, f'{official} {name}'}
    return set()


def _strip_region_josa(word):
    """'아산시에' → '아산시'. 지역 이름으로 확인될 때만 조사를 뗀다."""
    w = word.strip(' ,.?!')
    if w in REGION_WORDS:
        return w
    for josa in _REGION_JOSA:
        if w.endswith(josa) and w[:-len(josa)] in REGION_WORDS:
            return w[:-len(josa)]
    return w


def query_regions(query):
    """질문 → (필터 값 목록, 되물을 시군구 이름) (REG-30~33).

    '대구 사는 고객'        → (['대구광역시'], None)
    '대구 수성구 고객'      → (['대구광역시 수성구'], None)
    '수성구 고객'           → (['대구광역시 수성구'], None)   전국에 하나뿐인 시군구
    '중구 고객'             → ([], '중구')                     어느 시도인지 되묻기
    지역 단어 없음          → ([], None)
    """
    words = [_strip_region_josa(w) for w in unicodedata.normalize('NFC', query).split()]
    for i, word in enumerate(words):
        if word in SIDO or word in _SIGUNGU_OWNERS:
            found = regions_of(' '.join(words[i:]))
            if found:
                return [max(found, key=len)], None
            if len(_SIGUNGU_OWNERS.get(word, ())) > 1:
                return [], word
    return [], None
