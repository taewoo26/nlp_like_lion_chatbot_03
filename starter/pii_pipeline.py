"""탐지 + 가명처리 모듈.  ★ 실전 2의 핵심 과제: 이 파일을 여러분의 모듈로 교체하세요. ★

지금 들어 있는 것은 '데모용 마스킹'입니다.
  - detect()       : 전화번호·이메일만 정규식으로 찾습니다. 이름, 주소, 계좌 등은 못 찾습니다.
  - pseudonymize() : 문서 안에서만 번호를 매긴 토큰(<PHONE_1>, <PHONE_2> ...)을 붙입니다.
                     그래서 같은 사람이라도 문서가 바뀌면 다른 토큰이 되고, 질의와 문서의 토큰도 맞지 않습니다.
                     → 토큰 필터 검색이 동작하지 않고, check_token_consistency.py 에서 '쪼개짐'이 많이 나옵니다.

교체할 때 지켜야 할 약속 (다른 코드가 이 형식에 의존합니다)
  1. detect(text) -> [{"label": str, "start": int, "end": int}, ...]
       - 실전 1에서 선정한 탐지기를 연결합니다. 위치는 원문 기준 [start, end).
  2. pseudonymize(text, spans) -> (masked_text, records)
       - masked_text : 탐지 구간을 토큰으로 바꾼 텍스트
       - records     : [{"label", "start", "end", "original", "normalized", "token"}, ...]
                       start/end 는 '원문' 기준 위치 (토큰 일관성 채점에 사용)
  3. 토큰은 TOKEN_PATTERN 에 맞아야 합니다. (적재 시 청크별 토큰 목록을 이 패턴으로 뽑습니다)
  4. 같은 값은 문서와 질의를 가리지 않고 항상 같은 토큰이 되어야 합니다 (결정적 토큰).
       - 토큰을 만드는 방법은 팀에서 설계합니다. 비밀키는 config.HMAC_KEY (.env) 에서 읽고 코드에 쓰지 마세요.
       - '윤 선영'과 '윤선영'처럼 표기만 다른 값을 어떻게 같은 토큰으로 묶을지(정규화)도 팀에서 정합니다.
"""
import re

TOKEN_PATTERN = re.compile(r"<[A-Z_]+_[0-9A-Za-z]+>")

# ---------------------------------------------------------------- 데모용 탐지 (교체 대상)
_DEMO_RULES = [
    ("email", re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")),
    ("phone_number", re.compile(r"(?<!\d)01[016789][-. ]?\d{3,4}[-. ]?\d{4}(?!\d)")),
]


def detect(text):
    """TODO: 실전 1에서 선정한 탐지기로 교체하세요."""
    spans = []
    for label, pat in _DEMO_RULES:
        spans += [{"label": label, "start": m.start(), "end": m.end()} for m in pat.finditer(text)]
    return sorted(spans, key=lambda s: s["start"])


# ---------------------------------------------------------------- 데모용 가명처리 (교체 대상)
def pseudonymize(text, spans):
    """TODO: 결정적 토큰을 만드는 가명처리 모듈로 교체하세요.

    데모 구현은 문서 안에서만 번호를 매기므로 결정적이지 않습니다.
    """
    counters, seen, records = {}, {}, []
    out, last = [], 0
    for s in _drop_overlaps(spans):
        original = text[s["start"]:s["end"]]
        normalized = original  # TODO: 정규화 규칙
        key = (s["label"], normalized)
        if key not in seen:
            counters[s["label"]] = counters.get(s["label"], 0) + 1
            seen[key] = f"<{s['label'].upper()}_{counters[s['label']]}>"
        token = seen[key]
        out += [text[last:s["start"]], token]
        last = s["end"]
        records.append({"label": s["label"], "start": s["start"], "end": s["end"],
                        "original": original, "normalized": normalized, "token": token})
    out.append(text[last:])
    return "".join(out), records


def _drop_overlaps(spans):
    """겹치는 구간은 먼저 시작하고 더 긴 것을 남깁니다."""
    kept, end = [], -1
    for s in sorted(spans, key=lambda s: (s["start"], -(s["end"] - s["start"]))):
        if s["start"] >= end:
            kept.append(s)
            end = s["end"]
    return kept


IS_DEMO = True  # 여러분의 모듈로 교체했다면 False 로 바꾸세요.
