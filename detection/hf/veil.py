import importlib.util
import re
from pathlib import Path

from kiwipiepy import Kiwi
from huggingface_hub import snapshot_download

from detection.base import BaseDetector, Span

MODEL_ID = "1T/veil-pii-ko-lite"

_LABEL_MAP = {
    "PERSON": "person",
    "PHONE": "phone_number",
    "EMAIL": "email",
    "ADDRESS": "address",
    "RRN": "resident_registration_number",
    "ACCOUNT_NUMBER": "account_number",
    "CARD_NUMBER": "card_number",
}

# 고객센터 대표번호 (1588-XXXX 등) — 개인 식별 불가
_REPR_PHONE = re.compile(r'^1[0-9]{3}-[0-9]{4}$')

# 서울 지역번호 02 전화번호 — 앞뒤 숫자가 없는 경우만 매칭 (긴 숫자열 오매칭 방지)
_PHONE_02 = re.compile(r'(?<!\d)02[-. ]?\d{3,4}[-. ]?\d{4}(?!\d)')

# 내선번호: "내선" 키워드 뒤 4자리 숫자 — 개인 식별 불가
_INTERCOM = re.compile(r'내선\s*(\d{3,5})')

# 직함 목록 — 성씨(1글자)+직함 또는 직함 단독은 person PII 아님
_TITLES = {
    "부장", "차장", "과장", "대리", "사원", "팀장", "본부장",
    "이사", "상무", "전무", "사장", "실장", "수석", "선임", "주임", "매니저",
}

# 조사 단일 문자 — 복합 성씨 경계 연장 판단에 사용
_JOSA_CHARS = set('은이을의에도만가를로와과')

# 복합 성씨 (2글자) — 성씨+직함 패턴에서 prefix 길이 2도 허용
_COMPOUND_SURNAMES = {
    "남궁", "선우", "황보", "독고", "제갈", "서문", "동방", "사공",
    "어금", "장곡",
}

# 카드 상품명 접미어 — person 스팬 직후에 오면 오탐
_CARD_PRODUCT = re.compile(r'\s*(포인트\s*카드|체크\s*카드|신용\s*카드|직불\s*카드|선불\s*카드)')

# 법인명 접미어 — person 스팬 직후에 붙으면 오탐 (예: 미르보안)
_CORP_SUFFIX = re.compile(r'(보안|은행|증권|캐피탈|파이낸셜|카드사|금융|투자|자산|생명|손해보험|화재)')

# 상호명 접미어 — address 스팬이 이것으로 끝나면 오탐 (예: 다온편의점)
_STORE_SUFFIX = re.compile(r'(편의점|마트|슈퍼마켓?|카페|식당|음식점|주유소|약국)$')

# 참석자 목록 패턴: 쉼표로 나열된 2~4글자 한글 이름
_NAME_IN_LIST = re.compile(r'[가-힣]{2,4}(?=\s*[,،、]|\s*$|\s*불참|\s*\()')
# 이름 확률 매우 높은 컨텍스트 (NNP 체크 생략)
_LIST_CONTEXT_HIGH = re.compile(r'(참석|불참)\s*[:：]')
# 이름 확률 보통인 컨텍스트 (NNP 체크 유지)
_LIST_CONTEXT_LOW = re.compile(r'(작성자?|담당자?|수신|발신)\s*[:：]')
# 업무배분 bullet: "- 이름: 업무내용" 형식
_TASK_BULLET = re.compile(r'(?:^|\n)[ \t]*[-•]\s+([가-힣]{2,4})\s*[:：]')


class VeilDetector(BaseDetector):
    name = "hf_veil"

    def __init__(self):
        model_dir = Path(snapshot_download(repo_id=MODEL_ID))

        spec = importlib.util.spec_from_file_location("veil_module", model_dir / "veil.py")
        veil_module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(veil_module)

        self._veil = veil_module.Veil(
            str(model_dir / "model.int8.onnx"),
            tokenizer_dir=str(model_dir),
            merge_adjacent=(),
        )
        self._kiwi = Kiwi()

    def detect(self, text: str) -> list[Span]:
        raw = self._veil.predict(text)
        spans = []
        for entity in raw:
            base_label = _LABEL_MAP.get(entity["label"])
            if base_label is None:
                continue
            start, end = entity["start"], entity["end"]
            spans.append(Span(start=start, end=end, label=base_label, text=text[start:end]))

        # 모델이 미탐하는 패턴을 정규식으로 보완
        spans = self._augment_phone_02(text, spans)
        spans = self._augment_list_names(text, spans)
        spans = self._augment_task_names(text, spans)

        return self._postprocess(text, spans)

    # ── 보완 탐지 ────────────────────────────────────────────────

    def _augment_phone_02(self, text: str, spans: list[Span]) -> list[Span]:
        """02 지역번호 전화번호를 정규식으로 보완."""
        covered = {(s.start, s.end) for s in spans}
        for m in _PHONE_02.finditer(text):
            s, e = m.start(), m.end()
            if not any(s < ce and cs < e for cs, ce in covered):
                spans.append(Span(start=s, end=e, label="phone_number", text=m.group()))
                covered.add((s, e))
        return spans

    def _augment_list_names(self, text: str, spans: list[Span]) -> list[Span]:
        """참석자/담당자 목록에서 모델이 미탐한 이름을 보완."""
        covered = {(s.start, s.end) for s in spans}
        new_spans = []

        # 참석/불참: 이름 확률 매우 높음 → NNP 체크 생략
        for ctx_m in _LIST_CONTEXT_HIGH.finditer(text):
            line_start = ctx_m.end()
            line_end = text.find('\n', line_start)
            if line_end == -1:
                line_end = len(text)
            segment = text[line_start:line_end]
            for nm in _NAME_IN_LIST.finditer(segment):
                s = line_start + nm.start()
                e = line_start + nm.end()
                if any(s < ce and cs < e for cs, ce in covered):
                    continue
                new_spans.append(Span(start=s, end=e, label="person", text=nm.group()))
                covered.add((s, e))

        # 작성자/담당자 등: NNP(고유명사) 확인 — 일반명사(NNG) 제외
        for ctx_m in _LIST_CONTEXT_LOW.finditer(text):
            line_start = ctx_m.end()
            line_end = text.find('\n', line_start)
            if line_end == -1:
                line_end = len(text)
            segment = text[line_start:line_end]
            for nm in _NAME_IN_LIST.finditer(segment):
                s = line_start + nm.start()
                e = line_start + nm.end()
                if any(s < ce and cs < e for cs, ce in covered):
                    continue
                tokens = self._kiwi.tokenize(nm.group())
                if any(t.tag == 'NNP' for t in tokens):
                    new_spans.append(Span(start=s, end=e, label="person", text=nm.group()))
                    covered.add((s, e))

        return spans + new_spans

    def _augment_task_names(self, text: str, spans: list[Span]) -> list[Span]:
        """'- 이름: 업무내용' bullet 형식에서 미탐 이름 보완. 첫 토큰이 NNG이면 제외."""
        covered = {(s.start, s.end) for s in spans}
        new_spans = []
        for m in _TASK_BULLET.finditer(text):
            name = m.group(1)
            s = m.start(1)
            e = m.end(1)
            if any(s < ce and cs < e for cs, ce in covered):
                continue
            tokens = self._kiwi.tokenize(name)
            # 첫 형태소가 일반명사(NNG)이면 키워드로 간주 — 제외
            if tokens and tokens[0].tag == 'NNG':
                continue
            new_spans.append(Span(start=s, end=e, label="person", text=name))
            covered.add((s, e))
        return spans + new_spans

    # ── 후처리 ──────────────────────────────────────────────────

    def _postprocess(self, text: str, spans: list[Span]) -> list[Span]:
        # 내선번호 위치 사전 수집
        intercom_ranges = [(m.start(1), m.end(1)) for m in _INTERCOM.finditer(text)]

        result = []
        for span in spans:
            # 1. 줄바꿈 포함 스팬 분리
            parts = self._split_newline(text, span)
            for part in parts:
                # 2. 내선번호 필터 (address/phone 모두)
                if part.label in ("address", "phone_number"):
                    if any(s <= part.start and part.end <= e for s, e in intercom_ranges):
                        continue
                # 3. 대표번호 필터
                if part.label == "phone_number" and _REPR_PHONE.match(part.text):
                    continue
                # 4. person 전용 후처리
                if part.label == "person":
                    part = self._extend_compound_name(text, part)
                    trimmed = self._trim_josa(part)
                    if trimmed is None:
                        continue
                    # 경계오류 방지: 트리밍으로 너무 짧아졌으면 원본 유지
                    part = trimmed if len(trimmed.text) >= 2 else part
                    if self._is_title_person(part.text):
                        continue
                    # 카드 상품명 오탐 필터 (하린 포인트카드, 봄날 체크카드 등)
                    after = text[part.end:part.end + 10]
                    if _CARD_PRODUCT.match(after):
                        continue
                    # 법인명 오탐 필터 (미르보안 등)
                    if _CORP_SUFFIX.match(text[part.end:part.end + 8]):
                        continue
                # 5. address 상호명 오탐 필터 (다온편의점 등)
                if part.label == "address" and _STORE_SUFFIX.search(part.text):
                    continue
                result.append(part)
        return result

    def _extend_compound_name(self, text: str, span: Span) -> Span:
        """복합 성씨 + 1글자 이름 패턴이면 다음 조사 글자를 이름에 포함시켜 경계 보정."""
        for surname in _COMPOUND_SURNAMES:
            if not span.text.startswith(surname):
                continue
            if len(span.text) != len(surname) + 1:
                continue
            if span.end >= len(text):
                break
            next_char = text[span.end]
            if next_char not in _JOSA_CHARS:
                break
            # 조사 다음이 공백·비한글이면 진짜 조사 → 이름에 포함
            after = text[span.end + 1: span.end + 2]
            if not after or not ('가' <= after <= '힣'):
                return Span(start=span.start, end=span.end + 1,
                            label=span.label, text=span.text + next_char)
        return span

    def _split_newline(self, text: str, span: Span) -> list[Span]:
        """줄바꿈이 포함된 스팬을 줄 단위로 분리."""
        if "\n" not in span.text:
            return [span]
        result = []
        cursor = span.start
        for line in span.text.split("\n"):
            line_end = cursor + len(line)
            stripped = line.strip()
            if stripped:
                lead = len(line) - len(line.lstrip())
                s = cursor + lead
                e = s + len(stripped)
                result.append(Span(start=s, end=e, label=span.label, text=stripped))
            cursor = line_end + 1  # +1 for '\n'
        return result

    def _trim_josa(self, span: Span) -> Span | None:
        """스팬 끝 조사(J*)를 Kiwi로 감지해 제거."""
        tokens = self._kiwi.tokenize(span.text)
        if not tokens:
            return span
        last = tokens[-1]
        if last.tag.startswith("J"):
            new_text = span.text[:last.start].rstrip()
            if not new_text:
                return None
            # 복합 성씨로 시작 시, 트리밍 후 이름 부분이 1글자 이하면 취소 (남궁다은 → 남궁다 방지)
            for surname in _COMPOUND_SURNAMES:
                if span.text.startswith(surname):
                    if len(new_text) - len(surname) <= 1:
                        return span
            return Span(
                start=span.start,
                end=span.start + len(new_text),
                label=span.label,
                text=new_text,
            )
        return span

    def _is_title_person(self, text: str) -> bool:
        """성씨 단독 1글자 또는 성씨+직함 패턴이면 True."""
        stripped = text.strip()
        if len(stripped) == 1:
            return True
        for title in _TITLES:
            if stripped.endswith(title):
                prefix = stripped[: -len(title)].strip()
                if len(prefix) <= 1:  # 단음절 성씨
                    return True
                if prefix in _COMPOUND_SURNAMES:  # 복합 성씨 (남궁, 선우 등)
                    return True
        return False
