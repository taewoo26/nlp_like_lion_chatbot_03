"""탐지 + 가명처리 모듈 (split_v3 탐지기 + HMAC 결정적 토큰 + 주소 범주화)

현재 가명처리 규칙 설계서의 기본형을 적용한다.
  - 이름 공백·하이픈·마침표 정리 및 영문 대문자화
  - 일반 이메일의 로컬 부분 보존, 도메인만 소문자화
  - +82 국내형 변환, 해외 +와 필요한 선행 0 보존
  - 번호 값 안에서만 OCR 보정, IBAN 영문자와 선행 0 보존
  - 전체 HMAC 토큰, 원문 위치 검사, 잘못된 span·충돌 시 중단
명시된 이니셜은 탐지 후 제외하며, 허용된 회피 이메일은 정규화한다.
부분 이름 연결·혼합 이니셜·국제 발신 접두번호 확장은 후속 단계다.
주소는 현재 초안에 맞춰 숫자 요소 경계·괄호 정보를 보존하고, 지역을
확인할 수 없으면 전체 주소를 치환한다. 법정동 참고항목의 지리적 검증은 하지 않는다.

약속 (ingest.py · search.py 가 이 형식에 의존)
  1. detect(text) -> [{"label", "start", "end"}, ...]          원문 기준 [start, end)
  2. pseudonymize(text, spans) -> (masked_text, records)
       records: [{"label", "start", "end", "original", "normalized", "token"}, ...]  원문 기준 위치
  3. 토큰은 TOKEN_PATTERN 모양이며 HMAC 앞 TOKEN_HEX(12)자리 hex를 사용한다.
  4. 같은 값은 문서·질의를 가리지 않고 항상 같은 토큰 (HMAC-SHA256, 키는 .env 의 HMAC_KEY)

v1 처리 방식
  - 탐지: 실전 1 선정 탐지기 koelectra_post_kiwi_split_v3 (KoELECTRA + Veil + Kiwi + 규칙)
  - 정규화 (COM-04): 추출 값의 전각 보정 → 라벨별 규칙 (OCR은 번호 값에만 적용)
  - 운전면허번호: 고정 비공개 표시 (검색 토큰과 원문 매핑을 남기지 않음)

v2 추가 (규칙 정의서 ver0.2 AD 공통 처리 방식)
  - 주소 범주화: 시도·시군구는 원문 유지, 그 아래(도로명·번지·동·호)만 토큰화
      '대구광역시 수성구 한빛로 174, 502호' → '대구광역시 수성구 <ADDRESS_…>'
  - 토큰은 주소 전체를 정규화한 값으로 만든다 (시도명·확인 가능한 요소만 통일)
  - 상세주소만 있으면(110동 905호) 전체 토큰화 (AD-06)

v4 추가 (holdout 검색 재현 실험 근거, 2026-10-30)
  - 토큰 길이 64 → 12자리 hex. 64자리는 local 임베딩에서 질의 내용보다 토큰 글자가 벡터를
    좌우했고(같은 인물 질의는 내용이 달라도 순위가 거의 같음), 청크도 187 → 257개로 늘었다.
    12자리(16^12 ≈ 281조)도 충돌 검사는 그대로 유지한다.

v5 추가 (팀 결정 2026-11-02: 주소 전체 가명처리, 지역 정보는 종합 단계에서 메타데이터로)
  - ADDRESS_MODE=full (기본): 주소 전체를 토큰 하나로 바꾼다. 시도·시군구도 본문에 남기지 않는다.
      '대구광역시 수성구 한빛로 174, 502호' → '<ADDRESS_…>'
  - ADDRESS_MODE=region : v2 방식 (시도·시군구 유지). 비교 실험용.
  - 두 모드 모두 토큰 값은 같다 (주소 전체 정규화 값 기준). 바꾸면 다시 적재해야 한다.
  - 지역만 단독으로 나온 표기('대구 수성구')는 사람을 특정하지 않으므로 두 모드 모두 그대로 둔다.
  - 지역명 이름 오탐 제거: '충남 천안시 서북구 쪽'에서 천안시·서북구가 person으로 잡혀
    모든 토큰 필터에서 결과가 0건이 되던 문제 (Q049). 행정구역 목록과 앞뒤 단어로만 판단한다.
  - embedding_text(): 임베딩에 넣을 때만 토큰을 라벨 이름(<PERSON>)으로 바꾼다.
    토큰의 '누구인지'는 토큰 필터가 맡고, 임베딩은 업무 내용에 집중하게 한다.

v5 추가
  - NAME_LINK=doc (선택, 기본 off): 같은 문서 안의 '이름만' 표기(도윤)를 하나뿐인 전체 이름(신도윤)
    토큰으로 바꾼다. 원래 '도윤' 토큰은 alias_token 으로 남겨 청크 토큰 목록에 넣으므로
    '도윤 님' 같은 질의도 계속 찾을 수 있다. holdout 시뮬레이션: 쪼개짐 13 → 6명, 새 합쳐짐 0.
  - pseudonymize(strict=False): 질의에서 정규화할 수 없는 표기가 나와도 오류로 멈추지 않고
    [개인정보] 로 가린 뒤 계속한다 (원문·토큰·매핑 없음). 문서 적재는 기존대로 strict.
"""
import hashlib
import hmac
import re
import unicodedata
from functools import lru_cache

from . import config
from .address import REGION_WORDS, SIDO, normalize_address, region_bounds
from .email_normalization import normalize_email

TOKEN_PATTERN = re.compile(r"<[A-Z_]+_[0-9A-Za-z]+>")
TOKEN_HEX = 12                       # HMAC 앞 12자리. 본문·검색·매핑에 동일하게 사용
DELETED_TOKEN = "[운전면허번호 비공개]"  # 공통 삭제 표시는 검색용 토큰에 포함하지 않음
DELETE_LABELS = {"driver_license"}   # 규칙 정의서: 운전면허번호는 가명화 대신 삭제
SUPPORTED_LABELS = {
    "person", "phone_number", "email", "address",
    "resident_registration_number", "account_number", "card_number",
} | DELETE_LABELS
POLICY_VERSION = "pii-basic-v5"
_WIDTH_MAP = {code: chr(code - 0xFEE0) for code in range(0xFF01, 0xFF5F)}
_WIDTH_MAP[0x3000] = " "


# ---------------------------------------------------------------- 탐지
@lru_cache(maxsize=1)
def _detector():
    """모델 로딩이 오래 걸리므로 처음 한 번만 만든다."""
    from detection.combo import ComboDetector
    return ComboDetector("koelectra_post", "split_v3")


def detect(text):
    spans = [{"label": s.label, "start": s.start, "end": s.end} for s in _detector().detect(text)]
    return drop_region_names(text, spans)


def drop_region_names(text, spans):
    """행정구역 이름이 person으로 잡힌 경우를 뺀다 ('충남 천안시 서북구 쪽' → 천안시·서북구).

    둘 다 만족할 때만 뺀다 → '김민구', '서울 박성구 고객' 같은 실제 이름은 유지.
      1. 이름 구간 전체가 행정구역 목록(시도 별칭·시군구)에 있는 단어
      2. 바로 앞이나 뒤 단어도 시도명·시군구명 (지역을 나열하는 문맥)
    """
    kept = []
    for s in spans:
        if s["label"] == "person":
            word = text[s["start"]:s["end"]].strip()
            before = text[:s["start"]].split()
            after = text[s["end"]:].split()
            prev = before[-1] if before else ""
            nxt = after[0] if after else ""
            if word in REGION_WORDS and (_is_region(prev) or _is_region(nxt)):
                continue
        kept.append(s)
    return kept


def _is_region(word):
    word = word.strip(" ,")
    return word in SIDO or word in REGION_WORDS


TOKEN_LABEL = re.compile(r"<([A-Z_]+)_[0-9A-Za-z]+>")


def embedding_text(masked_text):
    """임베딩 입력용 사본: 토큰을 라벨 이름만 남긴다 (<PERSON_1a2b…> → <PERSON>).

    문서와 질의 모두 같은 함수를 거쳐야 한다. DB의 masked_text·tokens는 그대로 저장한다.
    비교 실험: .env 의 EMBED_TOKENS=keep 이면 토큰을 그대로 둔다 (바꾸면 재적재 필요).
    """
    mode = (config.env("EMBED_TOKENS", "label") or "label").strip().lower()
    if mode == "keep":
        return masked_text
    if mode != "label":
        raise ValueError(f"EMBED_TOKENS 는 label 또는 keep 이어야 합니다: {mode}")
    return TOKEN_LABEL.sub(r"<\1>", masked_text)


# ---------------------------------------------------------------- 정규화
DIGIT_LABELS = {"phone_number", "resident_registration_number", "account_number", "card_number"}


def _norm_text(text):
    """전각 숫자·하이픈, OCR 혼동 글자(O→0, l→1), 한글 숫자를 고친다. 글자 수가 그대로라 위치가 맞는다."""
    from detection.extra_rules import normalize
    return normalize(text)


def normalize_value(label, value):
    if label not in SUPPORTED_LABELS:
        raise ValueError(f"지원하지 않는 라벨: {label}")
    v = unicodedata.normalize("NFC", value).translate(_WIDTH_MAP).strip()
    if not v:
        raise ValueError("비어 있는 개인정보 값")
    if label == "person":
        return re.sub(r"[\s.\-]+", "", v).upper()
    if label == "email":
        return normalize_email(v)
    if label == "account_number":
        compact = re.sub(r"\s+", "", v)
        if re.fullmatch(r"[A-Za-z]{2}[0-9]{2}[A-Za-z0-9]+", compact):
            return compact.upper()  # IBAN의 정상 영문자는 OCR 숫자로 바꾸지 않음
    if label in DIGIT_LABELS:
        v = _norm_text(v)  # 숫자 보정은 추출된 번호 값에만 적용
    if label == "phone_number":
        v = re.sub(r"[\s.\-()]", "", v)
        digits = v[1:] if v.startswith("+") else v
        if not re.fullmatch(r"[0-9]+", digits):
            raise ValueError("잘못되었거나 아직 지원하지 않는 전화번호 표기")
        if v.startswith("+82"):
            national = v[3:]
            if not national:
                raise ValueError("국가번호 뒤의 전화번호가 비어 있음")
            return national if national.startswith("0") else "0" + national
        return v  # 해외 국가번호의 +와 선행 0은 그대로 보존
    if label in DIGIT_LABELS:
        v = re.sub(r"[\s.\-()]", "", v)
        if not re.fullmatch(r"[0-9]+", v):
            raise ValueError("마스킹되었거나 지원하지 않는 번호 표기")
        return v
    if label == "address":
        return normalize_address(v)
    return re.sub(r"\s+", " ", v)


def region_prefix(address):
    """기존 호출을 위해 보존한 지역 끝 위치 함수."""
    bounds = region_bounds(address)
    return bounds[1] if bounds else 0


# ---------------------------------------------------------------- 토큰
@lru_cache(maxsize=1)
def _key():
    if not config.HMAC_KEY or not config.HMAC_KEY.strip():
        raise RuntimeError("HMAC_KEY 가 비어 있습니다. .env 에 팀 공통 키를 넣으세요 (openssl rand -hex 32).")
    return config.HMAC_KEY.encode()


def make_token(label, normalized):
    if label not in SUPPORTED_LABELS or label in DELETE_LABELS:
        raise ValueError(f"토큰 생성 대상이 아닌 라벨: {label}")
    if not normalized:
        raise ValueError("토큰 비교값이 비어 있음")
    digest = hmac.new(_key(), f"{label}|{normalized}".encode(), hashlib.sha256).hexdigest()
    return f"<{label.upper()}_{digest[:TOKEN_HEX]}>"


UNSUPPORTED_MARK = "[개인정보]"   # strict=False 에서 정규화할 수 없는 값을 가리는 고정 표시 (토큰·매핑 없음)
_SURNAME2 = ("남궁", "황보", "제갈", "선우", "독고", "사공", "서문")


def _address_keep_region():
    mode = (config.env("ADDRESS_MODE", "full") or "full").strip().lower()
    if mode not in ("full", "region"):
        raise ValueError(f"ADDRESS_MODE 는 full 또는 region 이어야 합니다: {mode}")
    return mode == "region"


def _name_link_on():
    mode = (config.env("NAME_LINK", "off") or "off").strip().lower()
    if mode not in ("off", "doc"):
        raise ValueError(f"NAME_LINK 는 off 또는 doc 이어야 합니다: {mode}")
    return mode == "doc"


def _link_given_names(text, ordered):
    """같은 문서 안에서 '이름만' 쓴 표기를 전체 이름과 잇는다 (설계서 1.2.1 연결안).

    잇는 조건 (모두 만족):
      - 둘 다 person, 한글 이름
      - 전체 이름 = 성(1글자 또는 복성) + 짧은 표기   예) 신도윤 ← 도윤
      - 같은 문서에 그런 전체 이름 후보가 정확히 하나 (후보가 둘 이상이면 원래 표기 유지)
    반환: {span 위치: 전체 이름 정규화 값}
    """
    values = {}
    for i, s in enumerate(ordered):
        if s["label"] == "person":
            try:
                values[i] = normalize_value("person", text[s["start"]:s["end"]])
            except ValueError:
                continue
    links = {}
    for i, short in values.items():
        if not re.fullmatch(r"[가-힣]+", short):
            continue
        cands = {full for full in values.values()
                 if full != short and full.endswith(short)
                 and (len(full) - len(short) == 1 or (full[:2] in _SURNAME2 and len(full) - len(short) == 2))}
        if len(cands) == 1:
            links[i] = cands.pop()
    return links


def pseudonymize(text, spans, strict=True):
    """strict=False(질의용): 정규화할 수 없는 값은 오류 대신 고정 표시로 가리고 계속한다.
    적재(문서)는 strict=True 로 두어 처리할 수 없는 값을 조용히 넘기지 않는다."""
    ordered = _validate_spans(text, spans)
    links = _link_given_names(text, ordered) if _name_link_on() else {}
    keep_region = _address_keep_region()
    out, records, last, seen = [], [], 0, {}
    for i, s in enumerate(ordered):
        original = text[s["start"]:s["end"]]
        out.append(text[last:s["start"]])
        last = s["end"]
        if s["label"] in DELETE_LABELS:
            out.append(DELETED_TOKEN)                     # 매핑을 남기지 않음
            continue
        bounds = region_bounds(original) if s["label"] == "address" else None
        if bounds and not original[bounds[1]:].strip(" ,"):
            out.append(original[bounds[0]:bounds[1]])  # 지역만 있으면 토큰·매핑 생성 안 함
            continue
        try:
            normalized = normalize_value(s["label"], original)
        except ValueError:
            if strict:
                raise
            out.append(UNSUPPORTED_MARK)                  # 원문은 내보내지 않고, 검색 토큰도 만들지 않음
            continue
        alias = None
        if i in links:                                    # '도윤' → '신도윤' 토큰, '도윤' 토큰은 검색용 별칭으로
            alias = make_token("person", normalized)
            normalized = links[i]
        token = make_token(s["label"], normalized)       # 토큰은 주소 '전체' 기준 (같은 주소 = 같은 토큰)
        identity = (s["label"], normalized)
        if token in seen and seen[token] != identity:
            raise ValueError("서로 다른 개인정보 값의 토큰 충돌")
        seen[token] = identity
        if bounds and keep_region:                        # region 모드만 시도·시군구를 앞에 남긴다
            out.append(original[bounds[0]:bounds[1]] + " ")
        out.append(token)
        record = {"label": s["label"], "start": s["start"], "end": s["end"],
                  "original": original, "normalized": normalized, "token": token}
        if alias:
            record["alias_token"] = alias
        records.append(record)
    out.append(text[last:])
    return "".join(out), records


def _validate_spans(text, spans):
    """오류 메시지에는 원문 값을 넣지 않고, 잘못된 구간을 조용히 버리지 않는다."""
    for index, span in enumerate(spans):
        if not isinstance(span, dict) or not {"start", "end", "label"} <= span.keys():
            raise ValueError(f"span 필드 오류: index={index}")
        start, end = span["start"], span["end"]
        if type(start) is not int or type(end) is not int or not 0 <= start < end <= len(text):
            raise ValueError(f"span 범위 오류: index={index}")
        if span["label"] not in SUPPORTED_LABELS:
            raise ValueError(f"span 라벨 오류: index={index}")
    ordered = sorted(spans, key=lambda s: (s["start"], s["end"]))
    if any(left["end"] > right["start"] for left, right in zip(ordered, ordered[1:])):
        raise ValueError("겹치는 개인정보 span")
    return ordered


IS_DEMO = False
