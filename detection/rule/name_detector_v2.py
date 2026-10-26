"""이름 규칙 v2: 처음 보는 문서(자체 검증셋 A)에서 드러난 과적합을 줄인 버전.

v1 문제 (A셋 오탐: 배송지, 목요일, 주세요, 변경해, 기안자, 변호사 …)
- '같은 줄 어딘가에 항목 단서(고객:, 담당자: …)가 있으면 그 줄의 3글자는 다 이름' 규칙이
  대화 로그(줄마다 '고객:', '상담사:')에서 거의 모든 단어를 이름으로 만듦
- '줄 맨 앞 단어 + :'(화자 형식)도 '목요일:', '기안자:' 같은 항목명에 걸림

v2 변경 (일반 원칙만, 단어 목록 추가 최소화)
1. 단서를 두 등급으로 나눔
   - 확실(strong): 바로 뒤 직함·호칭 / 바로 뒤 '(' / 항목 단서 '바로 뒤' 이름 (사이에 이름 목록만 허용)
   - 약함(weak):   같은 줄 어딘가의 단서 / 화자 형식 / 앞 부서명 / 같은 문서 전파
   → 약한 후보는 혼자 쓰지 않고, 조합 탐지기에서 모델(Veil)도 이름이라고 할 때만 채택
2. 전문직 호칭 추가: 변호사·세무사·회계사·노무사·교수·박사·선생님 (일반 직함)
3. '요일'로 끝나는 3글자는 이름 아님 (월~일요일)

반환 Span.confidence: "rule:strong" / "rule:weak" / "rule:given_*"
"""
import re

from detection.base import Span
from detection.rule import name_detector as v1
from detection.rule.name_detector import NameDetector

TITLES = v1.TITLES + ("변호사", "세무사", "회계사", "노무사", "법무사", "교수", "박사", "선생님", "선생")
LIST_BETWEEN = re.compile(r"^\s*(?:[가-힣]{2,4}(?:\s?(?:님|씨))?\s*[,·/]\s*)*$")


class NameDetectorV2(NameDetector):
    name = "name_v2"

    @staticmethod
    def _title_after(text, e):
        k = e + 1 if text[e:e + 1] == " " else e
        for t in TITLES:
            if text.startswith(t, k) and text[k + len(t):k + len(t) + 1] in v1.TITLE_FOLLOW:
                return True
        return False

    @staticmethod
    def _adjacent_cue(text, s):
        """항목 단서(고객명: 등)가 이름 '바로 앞'에 있는가. 사이에는 이름 목록(쉼표 구분)만 허용."""
        line_start = text.rfind("\n", 0, s) + 1
        last = None
        for m in v1.LINE_CUES.finditer(text, line_start, s):
            last = m
        return bool(last) and bool(LIST_BETWEEN.match(text[last.end():s]))

    def detect(self, text: str) -> list[Span]:
        bounds = [(t.start, t.start + t.len) for t in self.kiwi.tokenize(text)]
        cands = []
        for m in v1.FULL.finditer(text):
            s, e = m.start(), m.end()
            key = text[s:e].replace(" ", "")
            given = text[e - 2:e]
            if (given in v1.GIVEN_STOP or key[:2] in v1.GIVEN_STOP or key.endswith(v1.ORG_SUFFIX)
                    or key in v1.COMMON_WORDS or key.endswith("요일") or key in TITLES):
                continue
            nxt = text[e:e + 1]
            title = self._title_after(text, e)
            strong = title or nxt == "(" or self._adjacent_cue(text, s)
            weak = (self._is_speaker(text, s, e) or self._line_has_cue(text, s)
                    or bool(v1.PREV_ORG.search(text[max(0, s - 20):s])))
            josa = nxt != "" and any(text[e:].startswith(j) for j in v1.JOSA)
            boundary_ok = nxt == "" or not re.match(r"[가-힣]", nxt) or josa or title
            if not boundary_ok or self._ends_with_particle(text[s:e]):
                continue
            if not title and self._crosses_word(bounds, s, e):
                continue
            cands.append({"s": s, "e": e, "key": key, "strong": strong, "weak": weak})

        strong_names = {c["key"] for c in cands if c["strong"]}
        spans, taken = [], []
        for c in cands:
            if c["strong"] or c["key"] in strong_names:
                level = "rule:strong"            # 확실한 이름과 같은 이름이면 확실로 취급
            else:
                level = "rule:weak"              # 약한 단서만 있거나 단서 없음 → 모델 동의 필요
            spans.append(Span(c["s"], c["e"], "person", text[c["s"]:c["e"]], level))
            taken.append((c["s"], c["e"]))

        def free(s, e):
            return all(e <= a or b <= s for a, b in taken)

        for m in v1.GIVEN_SSI.finditer(text):
            g = m.group(1) or m.group(2)
            s = m.start(1) if m.group(1) else m.start(2)
            if g not in v1.GIVEN_STOP and free(s, s + 2) and not self._crosses_word(bounds, s, s + 2):
                spans.append(Span(s, s + 2, "person", g, "rule:given_ssi"))
                taken.append((s, s + 2))
        for m in v1.VOCATIVE.finditer(text):
            s = m.start(1)
            if m.group(1) not in v1.GIVEN_STOP and free(s, s + 2):
                spans.append(Span(s, s + 2, "person", m.group(1), "rule:vocative"))
                taken.append((s, s + 2))
        givens = {k[-2:] for k in strong_names if len(k) >= 3}
        for g in givens:
            for m in re.finditer(rf"(?<![가-힣]){g}(?=(?:{'|'.join(v1.JOSA + TITLES)})|[\s,.!?]|$)", text):
                s = m.start()
                if free(s, s + 2):
                    spans.append(Span(s, s + 2, "person", g, "rule:given_doc"))
                    taken.append((s, s + 2))
        return spans
