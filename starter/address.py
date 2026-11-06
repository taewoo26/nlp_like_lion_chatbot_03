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

# ---------------------------------------------------------------- 지역 메타데이터 (규칙 정의서 v0.2 REG)
# 주소 원문에서 '시도 시군구' 표준값을 뽑는다. 결과는 rag.chunks.regions(TEXT[])용 문자열이다.
# 주소 종류(개인·기관)는 구분하지 않는다 (REG-06). 원문 주소는 반환하지 않는다.
#
#   REG-01·07 address span 에서만 추출 (extract_regions 가 label 로 거른다)
#   REG-02    시도 + 시군구까지만. 읍면동·도로명·번지·호는 버린다
#   REG-03    (초안) 일반시에 구가 있으면 '시 구'로 저장. 목록에 '수원시 영통구'처럼 있는 경우만
#   REG-04    세종특별자치시는 시도만 저장 (목록의 세종 항목은 동·면·읍이라 쓰지 않는다)
#   REG-05    시군구가 행정구역 목록에 없으면 저장하지 않는다 (시도만 남기지 않음)
#   REG-10    시도는 표준 명칭으로 통일 (_SIDO_GROUPS)
#   REG-11    (초안) 시도 없이 시군구만 있으면, 그 이름이 전국에 하나뿐일 때만 시도를 보완
#   REG-21    extract_regions 결과는 중복 없이 처음 나온 순서
#
# 판단 보류 (구현은 가장 보수적인 쪽: 저장 안 함, region_detail 의 reason 으로 구분)
#   - 시도 뒤에 시군구가 없는 주소 ('대구광역시 ○○로 1')           → no_sigungu
#   - '광주시'만 있고 '경기'가 없는 주소 (03_지역표준화 비고와 REG-11 충돌) → ambiguous_gwangju
from collections import namedtuple

RegionResult = namedtuple("RegionResult", "value reason")
SEJONG = "세종특별자치시"
_GWANGJU_METRO = "광주광역시"

# 출장소·세종의 동/면은 시군구가 아니므로 유효성 검사(REG-05)에서 뺀다
_SIGUNGU = {sido: frozenset(n for n in names if n.endswith(("시", "군", "구")))
            for sido, names in _ADMIN_DATA["regions"].items() if sido != SEJONG}
_SIGUNGU_OWNERS = {}
for _sido, _names in _SIGUNGU.items():
    for _name in _names:
        _SIGUNGU_OWNERS.setdefault(_name, set()).add(_sido)
_SIGUNGU_PATTERNS = {sido: _admin_pattern(names, r"[\s,]*") for sido, names in _SIGUNGU.items()}
_ANY_SIGUNGU = _admin_pattern(set(_SIGUNGU_OWNERS))
_SIGUNGU_LIKE = re.compile(r"[\s,]*([가-힣]{1,10}(?:시|군|구))(?![가-힣A-Za-z0-9])(?:\s+([가-힣]{1,6}구)(?![가-힣A-Za-z0-9]))?")
_GWANGJU_CITY = re.compile(r"광주시(?![가-힣A-Za-z0-9])")


def _clean(name):
    return re.sub(r"[\s,]+", " ", name).strip()


def region_detail(address):
    """주소 한 개 → RegionResult(value, reason). value 는 '시도 시군구', '세종특별자치시' 또는 None."""
    text = unicodedata.normalize("NFC", address).translate(_WIDTH)
    postcode = _POSTCODE.match(text)
    start = postcode.end() if postcode else len(text) - len(text.lstrip())

    sido = _SIDO_RE.match(text, start)
    if sido:
        official = SIDO[sido.group()]
        if official == SEJONG:
            return RegionResult(SEJONG, "sejong")                                   # REG-04
        admin = _SIGUNGU_PATTERNS[official].match(text, sido.end())
        if admin:
            return RegionResult(f"{official} {_clean(admin.group())}", "ok")        # REG-02·03·10
        if _SIGUNGU_LIKE.match(text, sido.end()):
            return RegionResult(None, "invalid_sigungu")                            # REG-05
        return RegionResult(None, "no_sigungu")                                      # 판단 보류

    if _GWANGJU_CITY.match(text, start):                                             # 판단 보류
        rest = _SIGUNGU_PATTERNS[_GWANGJU_METRO].match(text, start + len("광주시"))
        if rest:   # '광주시 북구'처럼 광주광역시 자치구가 뒤에 오면 광주광역시 (03_지역표준화 비고)
            return RegionResult(f"{_GWANGJU_METRO} {_clean(rest.group())}", "ok")
        return RegionResult(None, "ambiguous_gwangju")

    local = _ANY_SIGUNGU.match(text, start)
    if local:                                                                        # REG-11 (초안)
        name = _clean(local.group())
        owners = _SIGUNGU_OWNERS.get(name, set())
        if len(owners) == 1:
            return RegionResult(f"{next(iter(owners))} {name}", "sido_filled")
        return RegionResult(None, "ambiguous_sigungu")
    if _SIGUNGU_LIKE.match(text, start):
        return RegionResult(None, "invalid_sigungu")                                # REG-05
    return RegionResult(None, "no_region")


def standard_region(address):
    return region_detail(address).value


def extract_regions(text, spans):
    """탐지 span 중 address 만 골라 '시도 시군구' 목록을 만든다 (REG-01·21). TEXT[] 에 그대로 넣을 수 있다."""
    out = []
    for s in spans:
        if s.get("label") != "address":
            continue
        value = standard_region(text[s["start"]:s["end"]])
        if value and value not in out:
            out.append(value)
    return out


# ---------------------------------------------------------------- 질의 속 지역 (REG-30~35)
# 질의는 주소 형식이 아니라서('대구 사는 고객', '수성구에') 조사가 붙어도 지역으로 읽는다.
QueryRegion = namedtuple("QueryRegion", "value kind word")      # kind: sido | sigungu | ambiguous
_Q_JOSA = r"(?:에서|에|의|은|는|이|가|을|를|로|으로|도|만|쪽)"
_Q_END = rf"(?=$|[^가-힣A-Za-z0-9]|{_Q_JOSA}(?![가-힣]))"


def _q_alt(names):
    return "|".join(re.escape(n).replace(r"\ ", r"\s+") for n in sorted(names, key=len, reverse=True))


_Q_SIDO = re.compile(rf"(?<![가-힣A-Za-z0-9])(?P<w>{_q_alt(SIDO)}){_Q_END}")
_Q_SIGUNGU_AFTER = {sido: re.compile(rf"\s*(?P<w>{_q_alt(names)}){_Q_END}") for sido, names in _SIGUNGU.items()}
_Q_SIGUNGU = re.compile(rf"(?<![가-힣A-Za-z0-9])(?P<w>{_q_alt(set(_SIGUNGU_OWNERS) | {'광주시'})}){_Q_END}")


def query_regions(text):
    """질의에서 지역 조건을 찾는다. [QueryRegion] (value 는 저장값과 같은 표준 표기)

    REG-30 시도만        → kind=sido     ('대구광역시': 그 시도의 모든 지역)
    REG-31 시도+시군구   → kind=sigungu  ('대구광역시 수성구')
    REG-32 시군구만 1곳  → kind=sigungu  (초안: REG-11 과 같은 보완)
    REG-33 시군구만 여러 곳, '광주시'만 → kind=ambiguous (value 는 후보 시도 목록 문자열)
    """
    text = unicodedata.normalize("NFC", text).translate(_WIDTH)
    found, used = [], []
    for m in _Q_SIDO.finditer(text):
        official = SIDO[m.group("w")]
        end = m.end()
        after = _Q_SIGUNGU_AFTER.get(official)
        sub = after.match(text, end) if after else None
        if sub:
            found.append(QueryRegion(f"{official} {_clean(sub.group('w'))}", "sigungu", m.group() + " " + sub.group("w")))
            end = sub.end()
        else:
            found.append(QueryRegion(official, "sido", m.group("w")))
        used.append((m.start(), end))
    for m in _Q_SIGUNGU.finditer(text):
        if any(s <= m.start() < e for s, e in used):
            continue
        name = _clean(m.group("w"))
        if name == "광주시":
            found.append(QueryRegion("광주광역시, 경기도 광주시", "ambiguous", name))
            continue
        owners = sorted(_SIGUNGU_OWNERS.get(name, ()))
        if len(owners) == 1:
            found.append(QueryRegion(f"{owners[0]} {name}", "sigungu", name))
        else:
            found.append(QueryRegion(", ".join(owners), "ambiguous", name))
    out = []
    for r in found:
        if r not in out:
            out.append(r)
    return out
