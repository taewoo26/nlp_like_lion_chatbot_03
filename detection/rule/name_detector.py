"""이름(person) 규칙 탐지기: 성씨 목록 + 문맥 단서 + Kiwi 단어 경계 검사.

dev 관찰 (라벨 가이드 2.1)
- Kiwi 품사 태그는 이름마다 제각각(같은 이름도 NNP / NNG+NNG / XPN+NNG+MM)이라 쓰지 않는다
- 대신 "후보가 Kiwi 단어 경계를 가로지르는가"는 정답 340개 중 1개만 걸려 오탐 필터로 쓸 만하다
  예) '한결같이'(한 단어)에서 '한결같'을 잘라 이름 후보로 보면 단어 경계를 가로지름 → 탈락

규칙 (특정 템플릿 문구가 아니라 일반적인 단서만 사용)
1. 후보: 성씨(1글자·복성) + (공백 0~1) + 이름 2글자
2. 채택 조건 중 하나 이상
   - 바로 뒤에 호칭·직함·서명 (님, 씨, 고객, 팀장, 드림, 입니다 …) 또는 '('
     단, 직함 뒤에 다른 글자가 이어지면 직함이 아님 ('고객센터')
   - 같은 줄 앞에 항목 단서 (고객명:, 담당:, 작성자:, 참석: …)
   - 같은 문서에서 이미 이름으로 채택된 것과 같은 이름 (띄어쓰기 무시)
   - 바로 앞에 부서·조직명 (예: '채권관리팀 이상훈')
   - 줄 맨 앞 화자 형식 '이름:' (메신저·주간회의)
3. 탈락: 이름 2글자가 직함·일반어(부장, 대리, 고객 …) / 조직명 꼬리(팀, 센터 …)
   / Kiwi가 끝을 조사로 분석('문의는') / 직함 단서 없이 Kiwi 단어 경계를 가로지름
4. 이름만(given): '○○ 씨', 대화 부름말 '○○아,', 같은 문서 성명에서 뗀 이름 + 조사·호칭
"""
import re

from detection.base import BaseDetector, Span

SINGLE_SURNAMES = (
    "김이박최정강조윤장임한오서신권황안송류유홍전고문양손배백허남심노하곽성차주우구민진지엄채원천방공현함변염여추도소석선설마길연위표명기반왕금옥육인맹제모탁국어은편용예봉경사부가복태목형피두감음빈동온호범좌라"
)
COMPOUND_SURNAMES = ("남궁", "황보", "제갈", "선우", "독고", "사공", "서문", "동방")

# 이름 바로 뒤(공백 0~1 허용)에 오면 강한 단서
TITLES = (
    "님", "씨", "고객", "상담원", "담당자", "담당", "팀장", "부장", "과장", "차장", "대리", "사원",
    "주임", "매니저", "책임", "선임", "수석", "이사", "대표", "본부장", "실장", "센터장", "원장",
    "드림", "입니다", "께서", "께", "에게", "올림",
)
# 이름 바로 앞에 오면 강한 단서: 부서·조직명 + 공백 (예: '채권관리팀 이상훈', '고객센터 정성호')
PREV_ORG = re.compile(r"[가-힣A-Za-z]+(?:팀|센터|본부|실|부서)\s$")
# 직함 뒤에 붙어도 되는 글자 (조사·호칭). '고객센터'처럼 다른 글자가 이어지면 직함이 아님
TITLE_FOLLOW = set("이가은는을를의께님에과와도로랑") | {" ", ",", ".", "\n", "(", ")", ":", "/", "|", ""}
# 성씨 글자로 시작하는 3글자 일반 명사 (항목명·업무 용어). 특정 문서가 아니라 업무 문서 일반 어휘
COMMON_WORDS = {
    "이메일", "연락처", "민원인", "유형별", "담당자", "처리자", "작성자", "참석자", "검토자", "보고자",
    "신청인", "대리인", "연회비", "서비스", "문의처", "주소지", "고객명", "상담원", "수신인", "발신인",
    "전화번호", "주민번호", "임직원", "정기적", "정보보호", "기간별", "월별", "지점명", "원인별",
}
# 같은 줄 앞쪽에 오면 강한 단서 (항목명)
LINE_CUES = re.compile(
    r"(성명|이름|고객명|고객|상담원|담당자|담당|처리자|처리 담당|검토자|작성자|보고자|참석자|참석|불참|"
    r"민원인|대상|점검자|문의|수신|발신|수신인|발신인|요청자|신청인|대리인|갑|을)\s*[:：]"
)
# 이름 2글자 자리에 오면 이름이 아님
GIVEN_STOP = {
    "부장", "과장", "대리", "차장", "팀장", "사원", "주임", "이사", "대표", "실장", "고객", "상담",
    "담당", "책임", "선임", "수석", "본부", "센터", "직원", "임직", "회의", "보안", "관리", "처리",
    "확인", "요청", "안내", "결과", "내용", "사항", "일정", "번호", "주소", "계좌", "카드", "은행",
    "분기", "정보", "시스", "서비", "고객님", "이번", "이후", "전체", "기존", "신규", "본인",
}
ORG_SUFFIX = ("팀", "센터", "본부", "은행", "카드", "회사", "시스템", "부서")
JOSA = ("은", "는", "이", "가", "을", "를", "의", "과", "와", "도", "랑", "이랑", "에게", "께서", "아", "야")

SUR = "(?:" + "|".join(COMPOUND_SURNAMES) + "|[" + SINGLE_SURNAMES + "])"
FULL = re.compile(rf"(?<![가-힣]){SUR} ?[가-힣]{{2}}")
GIVEN_SSI = re.compile(r"(?<![가-힣])([가-힣]{2}) ?씨(?![가-힣])|(?<![가-힣])([가-힣]{2}) ?씨(?=[가는를의도와])")
VOCATIVE = re.compile(r"(?:^|:\s)([가-힣]{2})(?:아|야)(?=[,!\s])", re.M)


def _kiwi():
    from kiwipiepy import Kiwi
    return Kiwi()


class NameDetector(BaseDetector):
    name = "name"

    def __init__(self):
        self.kiwi = _kiwi()

    # ---------------------------------------------------------- 보조
    @staticmethod
    def _after(text, e):
        """이름 뒤 문자열 (공백 하나는 건너뜀)."""
        rest = text[e:e + 6]
        return rest[1:] if rest.startswith(" ") else rest

    def _crosses_word(self, bounds, s, e):
        """Kiwi 단어가 후보 시작·끝을 가로지르면 True."""
        for ts, te in bounds:
            if ts < s < te or ts < e < te:
                return True
        return False

    @staticmethod
    def _line_has_cue(text, s):
        line_start = text.rfind("\n", 0, s) + 1
        return bool(LINE_CUES.search(text[line_start:s]))

    @staticmethod
    def _title_after(text, e):
        """이름 뒤(공백 0~1)에 직함·호칭이 오고, 그 직함 뒤가 경계인가."""
        k = e + 1 if text[e:e + 1] == " " else e
        for t in TITLES:
            if text.startswith(t, k) and text[k + len(t):k + len(t) + 1] in TITLE_FOLLOW:
                return True
        return False

    @staticmethod
    def _is_speaker(text, s, e):
        """대화·목록 화자 형식: 줄 맨 앞(또는 '- ' 뒤) 이름 + ':'"""
        line_start = text.rfind("\n", 0, s) + 1
        return text[e:e + 1] == ":" and text[line_start:s].strip() in ("", "-")

    def _ends_with_particle(self, cand):
        """Kiwi가 후보 끝을 조사(J*)로 분석하면 True (예: '문의는' → 문의 + 는)."""
        toks = self.kiwi.tokenize(cand)
        return len(toks) >= 2 and toks[-1].tag.startswith("J")

    # ---------------------------------------------------------- 탐지
    def detect(self, text: str) -> list[Span]:
        bounds = [(t.start, t.start + t.len) for t in self.kiwi.tokenize(text)]
        cands = []
        for m in FULL.finditer(text):
            s, e = m.start(), m.end()
            key = text[s:e].replace(" ", "")
            given = text[e - 2:e]
            if (given in GIVEN_STOP or key[:2] in GIVEN_STOP or key.endswith(ORG_SUFFIX)
                    or key in COMMON_WORDS):
                continue
            nxt = text[e:e + 1]
            title = self._title_after(text, e)
            prev = text[max(0, s - 20):s]
            strong = (
                title
                or nxt == "("
                or self._is_speaker(text, s, e)
                or self._line_has_cue(text, s)
                or bool(PREV_ORG.search(prev))
            )
            josa = nxt != "" and any(text[e:].startswith(j) for j in JOSA)
            boundary_ok = nxt == "" or not re.match(r"[가-힣]", nxt) or josa or title
            if not boundary_ok or self._ends_with_particle(text[s:e]):
                continue
            # 직함·호칭 단서가 없으면 Kiwi 단어 경계를 가로지르는 후보는 버림 ('한결같이' 등)
            if not title and self._crosses_word(bounds, s, e):
                continue
            cands.append({"s": s, "e": e, "key": key, "strong": strong})

        # 1차: 강한 단서가 있는 후보
        names = {c["key"] for c in cands if c["strong"]}
        # 2차: 같은 문서의 같은 이름, 또는 목록 안에서 채택된 이름 옆
        accepted = []
        for c in cands:
            if c["strong"] or c["key"] in names:
                accepted.append(c)
                names.add(c["key"])

        spans = [Span(c["s"], c["e"], "person", text[c["s"]:c["e"]], "rule:full") for c in accepted]
        taken = [(sp.start, sp.end) for sp in spans]

        def free(s, e):
            return all(e <= a or b <= s for a, b in taken)

        # 이름만(given) — '○○ 씨'
        for m in GIVEN_SSI.finditer(text):
            g = m.group(1) or m.group(2)
            s = m.start(1) if m.group(1) else m.start(2)
            if g not in GIVEN_STOP and free(s, s + 2) and not self._crosses_word(bounds, s, s + 2):
                spans.append(Span(s, s + 2, "person", g, "rule:given_ssi"))
                taken.append((s, s + 2))
        # 이름만 — 대화 부름말 '○○아,'
        for m in VOCATIVE.finditer(text):
            s = m.start(1)
            if m.group(1) not in GIVEN_STOP and free(s, s + 2):
                spans.append(Span(s, s + 2, "person", m.group(1), "rule:vocative"))
                taken.append((s, s + 2))
        # 이름만 — 같은 문서 성명에서 뗀 이름 + 조사·호칭
        givens = {k[-2:] for k in names if len(k) >= 3}
        for g in givens:
            for m in re.finditer(rf"(?<![가-힣]){g}(?=(?:{'|'.join(JOSA + TITLES)})|[\s,.!?]|$)", text):
                s = m.start()
                if free(s, s + 2):
                    spans.append(Span(s, s + 2, "person", g, "rule:given_doc"))
                    taken.append((s, s + 2))
        return spans
