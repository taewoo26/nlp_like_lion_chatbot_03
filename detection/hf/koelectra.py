"""atonlee/koelectra-ko-pii-ner 기반 PII 탐지기 (Kiwi 후처리 포함)."""
import re
from pathlib import Path

from kiwipiepy import Kiwi
from huggingface_hub import snapshot_download
from transformers import pipeline

from detection.base import BaseDetector, Span

MODEL_ID = "atonlee/koelectra-ko-pii-ner"

_LABEL_MAP = {
    "NAME":           "person",
    "PHONE":          "phone_number",
    "EMAIL":          "email",
    "ADDRESS":        "address",
    "PLACE":          "address",
    "RRN":            "resident_registration_number",
    "ACCOUNT_NUMBER": "account_number",
    "CARD_NUMBER":    "card_number",
    "B-NAME":   "person",       "I-NAME":   "person",
    "B-PHONE":  "phone_number", "I-PHONE":  "phone_number",
    "B-EMAIL":  "email",        "I-EMAIL":  "email",
    "B-ADDRESS":"address",      "I-ADDRESS":"address",
    "B-PLACE":  "address",      "I-PLACE":  "address",
    "B-RRN":    "resident_registration_number", "I-RRN": "resident_registration_number",
    "B-ACCOUNT_NUMBER": "account_number", "I-ACCOUNT_NUMBER": "account_number",
    "B-CARD_NUMBER":    "card_number",    "I-CARD_NUMBER":    "card_number",
}

# 고객센터 대표번호 (1588-XXXX 등)
_REPR_PHONE = re.compile(r'^1[0-9]{3}-[0-9]{4}$')

# 서울 지역번호 02
_PHONE_02 = re.compile(r'(?<!\d)02[-. ]?\d{3,4}[-. ]?\d{4}(?!\d)')

# 하이픈 없는 휴대전화 번호 (01001443268 등)
_PHONE_NOHYPHEN = re.compile(r'(?<!\d)(01[016789]\d{7,8})(?!\d)')

# 내선번호
_INTERCOM = re.compile(r'내선\s*(\d{3,5})')

# 조사 단일 문자
_JOSA_CHARS = set('은이을의에도만가를로와과')

# 복합 성씨
_COMPOUND_SURNAMES = {
    "남궁", "선우", "황보", "독고", "제갈", "서문", "동방", "사공", "어금", "장곡",
}

# 직함 목록
_TITLES = {
    "부장", "차장", "과장", "대리", "사원", "팀장", "본부장",
    "이사", "상무", "전무", "사장", "실장", "수석", "선임", "주임", "매니저",
}

# 이름 뒤 붙는 호칭 — person 스팬에서 제거
_HONORIFICS = {"책임", "드림", "대상", "귀중", "올림", "배상"}

# 카드 상품명 접미어
_CARD_PRODUCT = re.compile(r'\s*(포인트\s*카드|체크\s*카드|신용\s*카드|직불\s*카드|선불\s*카드)')

# 법인명 접미어
_CORP_SUFFIX = re.compile(r'(보안|은행|증권|캐피탈|파이낸셜|카드사|금융|투자|자산|생명|손해보험|화재)')

# 상호명 접미어
_STORE_SUFFIX = re.compile(r'(편의점|마트|슈퍼마켓?|카페|식당|음식점|주유소|약국)$')

# 단일 성씨 (spaced name 확장용)
_COMMON_SURNAMES = {
    "김","이","박","최","정","강","조","윤","장","임","한","오","서","신","권",
    "황","안","송","유","홍","전","고","문","손","양","배","백","허","남","심",
    "노","하","곽","성","차","주","우","구","나","민","류","변","엄","채","천",
    "방","공","현","함","염","여","추","도","소",
}

# 'X 고객' 보완 탐지 패턴
_CUSTOMER_PAT = re.compile(r'([가-힣]{2,4})\s+고객')


class KoelectraDetector(BaseDetector):
    name = "hf_koelectra"

    def __init__(self):
        model_dir = str(Path(snapshot_download(repo_id=MODEL_ID)))
        self._pipe = pipeline(
            "ner",
            model=model_dir,
            tokenizer=model_dir,
            aggregation_strategy="simple",
            device=-1,
        )
        self._kiwi = Kiwi()

    def detect(self, text: str) -> list[Span]:
        results = self._pipe(text)
        spans = []
        for ent in results:
            raw_label = ent.get("entity_group") or ent.get("entity", "")
            base_label = _LABEL_MAP.get(raw_label)
            if base_label is None:
                continue
            start, end = ent["start"], ent["end"]
            spans.append(Span(start=start, end=end, label=base_label, text=text[start:end]))

        spans = self._augment_phone_02(text, spans)
        spans = self._augment_phone_nohyphen(text, spans)
        spans = self._augment_person_customer(text, spans)
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

    def _augment_phone_nohyphen(self, text: str, spans: list[Span]) -> list[Span]:
        """하이픈 없는 휴대전화 번호를 정규식으로 보완 (01001443268 등).
        phone_number 스팬과만 중복 체크 (다른 라벨이 겹쳐도 추가)."""
        phone_covered = {(s.start, s.end) for s in spans if s.label == "phone_number"}
        new_spans = []
        for m in _PHONE_NOHYPHEN.finditer(text):
            s, e = m.start(1), m.end(1)
            if not any(cs <= s and e <= ce for cs, ce in phone_covered):
                new_spans.append(Span(start=s, end=e, label="phone_number", text=m.group(1)))
                phone_covered.add((s, e))
        return spans + new_spans

    def _augment_person_customer(self, text: str, spans: list[Span]) -> list[Span]:
        """'X 고객' 패턴에서 탐지 안 된 이름 보완 (정하은 고객 등)."""
        person_covered = {(s.start, s.end) for s in spans if s.label == "person"}
        new_spans = []
        for m in _CUSTOMER_PAT.finditer(text):
            s, e = m.start(1), m.end(1)
            if any(cs <= s and e <= ce for cs, ce in person_covered):
                continue
            ctx_s = max(0, s - 5)
            ctx = text[ctx_s:min(len(text), e + 5)]
            toks = self._kiwi.tokenize(ctx)
            rel_s, rel_e = s - ctx_s, e - ctx_s
            name_toks = [t for t in toks if t.start >= rel_s and t.start + t.len <= rel_e]
            if name_toks and name_toks[0].tag == "NNP":
                new_spans.append(Span(start=s, end=e, label="person", text=text[s:e]))
                person_covered.add((s, e))
        return spans + new_spans

    # ── 후처리 ──────────────────────────────────────────────────

    def _postprocess(self, text: str, spans: list[Span]) -> list[Span]:
        intercom_ranges = [(m.start(1), m.end(1)) for m in _INTERCOM.finditer(text)]
        result = []
        for span in spans:
            parts = self._split_newline(text, span)
            for part in parts:
                # 내선번호 필터
                if part.label in ("address", "phone_number"):
                    if any(s <= part.start and part.end <= e for s, e in intercom_ranges):
                        continue
                # 전화번호 후처리
                if part.label == "phone_number":
                    if _REPR_PHONE.match(part.text):
                        continue
                    # 7자리 미만 숫자는 오탐 (1599-0000 분리 등)
                    if len(re.sub(r'\D', '', part.text)) < 7:
                        continue
                # account_number 경계 복원 (앞 잘린 숫자 포함)
                if part.label == "account_number":
                    part = self._extend_account_boundary(text, part)
                # address 후처리
                if part.label == "address":
                    # 지점명 오탐 필터 (여의도지점, 해운대지점 등)
                    if text[part.end:part.end + 2] == "지점":
                        continue
                    if _STORE_SUFFIX.search(part.text):
                        continue
                    # 끝 조사 제거 (103호로 → 103호)
                    trimmed_addr = self._trim_josa(part)
                    if trimmed_addr is not None and len(trimmed_addr.text) >= 5:
                        part = trimmed_addr
                # person 후처리
                if part.label == "person":
                    part = self._extend_compound_name(text, part)
                    part = self._trim_josa(part)
                    if part is None:
                        continue
                    part = self._trim_honorific(part)
                    if part is None:
                        continue
                    part = self._trim_company_prefix(part)
                    if part is None:
                        continue
                    part = self._extend_spaced_surname(text, part)
                    if len(part.text.strip()) < 2:
                        continue
                    if self._is_title_person(part.text):
                        continue
                    # 2글자 이하 오탐 필터 — suffix NNP+조사이면 이름 확정, 아니면 prefix로 재확인
                    if len(part.text.strip()) <= 2:
                        name_len = len(part.text.strip())
                        suf = text[part.start:min(len(text), part.end + 3)]
                        suf_toks = self._kiwi.tokenize(suf)
                        suf_span = [t for t in suf_toks if t.start < name_len]
                        suf_after = [t for t in suf_toks if t.start >= name_len]
                        is_name = (suf_span and suf_span[0].tag == 'NNP'
                                   and suf_after and suf_after[0].tag.startswith('J'))
                        if not is_name:
                            pre_s = max(0, part.start - 5)
                            pre = text[pre_s:part.end]
                            pre_toks = self._kiwi.tokenize(pre)
                            rel_s = part.start - pre_s
                            span_toks = [t for t in pre_toks
                                         if t.start >= rel_s and t.start + t.len <= rel_s + name_len]
                            if not span_toks:
                                span_toks = self._kiwi.tokenize(part.text.strip())
                            if span_toks and all(t.tag in ('NNG', 'VV', 'VA', 'EF', 'EP', 'EC') for t in span_toks):
                                continue
                    after = text[part.end:part.end + 10]
                    if _CARD_PRODUCT.match(after):
                        continue
                    if _CORP_SUFFIX.match(text[part.end:part.end + 8]):
                        continue
                result.append(part)
        return result

    def _split_newline(self, text: str, span: Span) -> list[Span]:
        """줄바꿈 포함 스팬을 줄 단위로 분리."""
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
            cursor = line_end + 1
        return result

    def _extend_compound_name(self, text: str, span: Span) -> Span:
        """복합 성씨 + 1글자 이름 패턴이면 다음 조사 글자를 포함시켜 경계 보정."""
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
            after = text[span.end + 1:span.end + 2]
            if not after or not ('가' <= after <= '힣'):
                return Span(start=span.start, end=span.end + 1,
                            label=span.label, text=span.text + next_char)
        return span

    def _extend_spaced_surname(self, text: str, span: Span) -> Span:
        """'성씨(1글자) + 공백' 앞에 있는 이름을 spaced name으로 확장 (서윤 → 전 서윤).
        앞 글자가 한글이면 단어의 일부(보안→안)이므로 확장하지 않음."""
        if ' ' in span.text or span.start < 2:
            return span
        if text[span.start - 1] != ' ':
            return span
        if text[span.start - 2] not in _COMMON_SURNAMES:
            return span
        # 성씨가 단독 토큰인지 확인: 바로 앞 글자가 한글이면 단어 일부 → 확장 안 함
        if span.start >= 3 and '가' <= text[span.start - 3] <= '힣':
            return span
        new_start = span.start - 2
        return Span(start=new_start, end=span.end, label=span.label, text=text[new_start:span.end])

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
            for surname in _COMPOUND_SURNAMES:
                if span.text.startswith(surname) and len(new_text) - len(surname) <= 1:
                    return span
            return Span(start=span.start, end=span.start + len(new_text),
                        label=span.label, text=new_text)
        return span

    def _trim_honorific(self, span: Span) -> Span | None:
        """끝에 붙은 호칭 suffix(책임, 드림 등)를 Kiwi로 제거."""
        tokens = self._kiwi.tokenize(span.text)
        if not tokens:
            return span
        last = tokens[-1]
        if last.form in _HONORIFICS:
            new_text = span.text[:last.start].rstrip()
            if not new_text:
                return None
            return Span(start=span.start, end=span.start + len(new_text),
                        label=span.label, text=new_text)
        return span

    def _trim_company_prefix(self, span: Span) -> Span | None:
        """'회사명 이름' 형태에서 회사명 prefix 제거 (예: 미르보안 권선영 → 권선영).
        성씨는 1-2글자이므로 prefix가 2글자 이하이면 건드리지 않음 (김 수빈, 윤 선영 등)."""
        text = span.text.strip()
        if ' ' not in text:
            return span
        parts = text.rsplit(' ', 1)
        prefix = parts[0].strip()
        last_part = parts[-1].strip()
        # 성씨 구분: prefix ≤ 2글자는 성씨로 간주 → 제거 안 함
        if len(prefix) <= 2:
            return span
        if not (2 <= len(last_part) <= 4):
            return span
        if not all('가' <= c <= '힣' for c in last_part):
            return span
        tokens = self._kiwi.tokenize(last_part)
        # NNP 인식 OR prefix에 회사명 접미어가 있으면 trim (미르보안 강민재 등)
        if (tokens and tokens[0].tag == 'NNP') or _CORP_SUFFIX.search(prefix):
            offset = span.text.rindex(last_part)
            new_start = span.start + offset
            return Span(start=new_start, end=span.end, label=span.label, text=last_part)
        return span

    def _extend_account_boundary(self, text: str, span: Span) -> Span:
        """계좌번호 앞뒤 잘린 숫자 복원 (예: 6-409571 → 816-409571-12).
        앞: 최대 3자리 backward 확장 / 뒤: -NN 형태가 이어지면 forward 확장."""
        s, e = span.start, span.end
        # 앞 확장 (최대 3자리)
        limit = max(0, s - 3)
        while s > limit and text[s - 1].isdigit():
            s -= 1
        # 뒤 확장 (하이픈 + 숫자 1~4자리)
        if e < len(text) and text[e] == '-':
            j = e + 1
            while j < len(text) and text[j].isdigit():
                j += 1
            if j > e + 1:
                e = j
        if s != span.start or e != span.end:
            return Span(start=s, end=e, label=span.label, text=text[s:e])
        return span

    def _is_title_person(self, text: str) -> bool:
        """성씨 단독 1글자 또는 성씨+직함 패턴이면 True."""
        stripped = text.strip()
        if len(stripped) == 1:
            return True
        for title in _TITLES:
            if stripped.endswith(title):
                prefix = stripped[:-len(title)].strip()
                if len(prefix) <= 1:
                    return True
                if prefix in _COMPOUND_SURNAMES:
                    return True
        return False
