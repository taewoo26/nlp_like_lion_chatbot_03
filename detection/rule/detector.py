"""Regex + Kiwi baseline; no spaced-name merging or josa postprocessing.

Rules use public formats and generic context words, never gold PII lists.
Names are conservative NNP candidates, not a complete Korean NER system.
"""

import re
from datetime import date

from kiwipiepy import Kiwi

from detection.base import BaseDetector, Span


class RuleDetector(BaseDetector):
    name = "rules"

    # Lower ranks win overlaps; longer spans win within the same label.
    _PRIORITY = {
        "resident_registration_number": 0,
        "card_number": 1,
        "account_number": 2,
        "email": 3,
        "address": 4,
        "phone_number": 5,
        "person": 6,
    }

    def __init__(self):
        self.kiwi = Kiwi(num_workers=1)
        # Numeric boundaries also reject fragments of longer hyphenated IDs.
        left, right = r"(?<![0-9A-Za-z-])", r"(?![0-9-])"
        self._rrn = re.compile(
            left + r"[0-9]{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12][0-9]|3[01])"
            r"-[1-4][0-9]{6}" + right
        )
        self._card = re.compile(
            left + r"[0-9]{4}(?P<sep>[-. ])[0-9]{4}(?P=sep)"
            r"[0-9]{4}(?P=sep)[0-9]{4}" + right
        )
        self._account = re.compile(left + r"[0-9]{3}-[0-9]{6}-[0-9]{2}" + right)
        self._phone = re.compile(
            left + r"(?:01[016789](?P<msep>[-. ]?)[0-9]{3,4}(?P=msep)[0-9]{4}"
            r"|(?:02|0[3-6][1-5])(?P<asep>[-. ]?)[0-9]{3,4}(?P=asep)[0-9]{4})"
            + right
        )
        self._email = re.compile(
            r"(?<![A-Za-z0-9.!#$%&'*+/=?^_`{|}~-])"
            r"[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+"
            r"(?:\.[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+)*@"
            r"(?:[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?\.)+"
            r"[A-Za-z]{2,}(?![A-Za-z0-9_-])"
        )
        # Require a road, building number and a detailed floor/unit address.
        # Horizontal whitespace only: never bridge documents' text lines.
        self._address = re.compile(
            r"(?<![가-힣])(?:[가-힣]{2,}(?:특별자치시|특별자치도|특별시|광역시|도))"
            r"[ \t]+(?:[가-힣]+(?:시|군|구|읍|면|동)[ \t]+){1,3}"
            r"[가-힣A-Za-z0-9·]+(?:로|길)[ \t]+[0-9]+(?:-[0-9]+)?"
            r"[ \t]*,?[ \t]*(?:[0-9]+동[ \t]+)?"
            r"(?:[0-9]+층(?:[ \t]+[0-9]+호)?|[0-9]+호)"
        )
        self._account_context = re.compile(r"계좌|[가-힣]+은행|[가-힣]+저축은행")
        self._card_context = re.compile(r"카드")
        self._name_chars = re.compile(r"[가-힣]{2,4}\Z")
        self._name_before = re.compile(
            r"(?:고객명|성명|이름|담당자|담당|고객|수신자|발신자)"
            r"[ \t]*(?::[ \t]*)?\Z"
        )
        self._name_after = re.compile(
            r"[ \t]*(?:님|씨|고객|대리|과장|차장|부장|팀장|사원|주임|"
            r"대표|이사|상무|전무|본부장|팀원)"
        )
        self._nonperson_after = re.compile(
            r"[ \t]*(?:포인트|플러스|체크|신용|카드|상품|은행|저축은행|"
            r"주식회사|보안|부서|지점|센터|운영팀|관리팀)"
        )
        self._nonperson_suffix = re.compile(
            r"(?:회사|은행|보안|카드|센터|지점|부서|본부|팀|상품)\Z"
        )

    @staticmethod
    def _span(text: str, start: int, end: int, label: str) -> Span:
        return Span(start=start, end=end, label=label, text=text[start:end])

    @staticmethod
    def _left_context(text: str, start: int, width: int = 48) -> str:
        """Only inspect the current line, not unrelated previous fields."""
        return text[max(0, start - width):start].rsplit("\n", 1)[-1].rsplit("\r", 1)[-1]

    def _detect_structured(self, text: str) -> list[Span]:
        spans = []
        for match in self._rrn.finditer(text):
            value = match.group()
            year = (1900 if value[7] in "12" else 2000) + int(value[:2])
            try:
                date(year, int(value[2:4]), int(value[4:6]))
            except ValueError:
                continue
            # Synthetic values need not satisfy a real-world checksum.
            spans.append(self._span(text, *match.span(), "resident_registration_number"))
        for pattern, label, context in (
            (self._card, "card_number", self._card_context),
            (self._account, "account_number", self._account_context),
            (self._email, "email", None),
            (self._phone, "phone_number", None),
        ):
            for match in pattern.finditer(text):
                if context is not None and not context.search(self._left_context(text, match.start())):
                    continue
                spans.append(self._span(text, *match.span(), label))
        return spans

    def _detect_addresses(self, text: str) -> list[Span]:
        return [self._span(text, *m.span(), "address") for m in self._address.finditer(text)]

    def _detect_persons(self, text: str) -> list[Span]:
        spans = []
        for token in self.kiwi.tokenize(text):
            if token.tag != "NNP":
                continue
            start, end = token.start, token.start + token.len
            surface = text[start:end]
            if not self._name_chars.fullmatch(surface) or surface != token.form:
                continue
            # Do not accept a trailing fragment of a longer Hangul word.
            if start and "가" <= text[start - 1] <= "힣":
                continue
            before = self._left_context(text, start)
            after = text[end:end + 24].split("\n", 1)[0].split("\r", 1)[0]
            if self._nonperson_suffix.search(surface) or self._nonperson_after.match(after):
                continue
            if self._name_before.search(before) or self._name_after.match(after):
                # Keep Kiwi's original offsets. No token joining, suffix
                # stripping, josa dictionary or lost-name recovery here.
                spans.append(self._span(text, start, end, "person"))
        return spans

    def _resolve_overlaps(self, spans: list[Span]) -> list[Span]:
        unique = {(s.label, s.start, s.end): s for s in spans}
        ordered = sorted(
            unique.values(),
            key=lambda s: (self._PRIORITY[s.label], -(s.end - s.start), s.start, s.end),
        )
        accepted = []
        for candidate in ordered:
            if not any(candidate.start < s.end and s.start < candidate.end for s in accepted):
                accepted.append(candidate)
        return sorted(accepted, key=lambda s: (s.start, s.end, s.label))

    def detect(self, text: str) -> list[Span]:
        if not isinstance(text, str):
            raise TypeError("text must be a string")
        if not text:
            return []
        candidates = self._detect_structured(text)
        candidates.extend(self._detect_addresses(text))
        candidates.extend(self._detect_persons(text))
        spans = self._resolve_overlaps(candidates)
        for span in spans:
            if not (0 <= span.start < span.end <= len(text)) or span.text != text[span.start:span.end]:
                raise ValueError("Detector produced an invalid source span")
        return spans
