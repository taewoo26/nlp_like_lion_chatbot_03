"""LLM 탐지기. LLM에게는 문자열+라벨만 받고, 위치는 코드로 계산한다."""
import json
import os
import re

from detection.base import BaseDetector, Span, LABELS

SYSTEM_PROMPT = """너는 한국어 금융 사내 문서에서 개인정보를 찾아내는 탐지기다.
문서에 실제로 적힌 개인정보 문자열을 찾아 JSON 배열로만 답한다.

[라벨 8종]
- person: 사람 이름. 고객과 직원 이름을 모두 포함한다.
- phone_number: 개인 연락처 전화번호.
- email: 이메일 주소.
- address: 주소.
- resident_registration_number: 주민등록번호.
- account_number: 계좌번호.
- card_number: 카드번호.
- organization: 기관·회사명.

[출력 규칙]
1. 형식: [{"text": "문서에 적힌 그대로의 문자열", "label": "라벨"}] 만 출력한다. 설명, 코드블록, 다른 글자를 쓰지 않는다.
2. text는 문서에서 한 글자도 바꾸지 말고 그대로 복사한다. 공백, 하이픈도 그대로 둔다.
3. 개인정보가 없으면 [] 를 출력한다.
4. 같은 문자열이 여러 번 나와도 한 번만 적는다.

[범위 규칙]
- 이름 뒤에 붙은 조사(은, 는, 이, 가, 을, 를, 과, 와, 랑, 아, 야 등)와 호칭(님, 씨, 고객, 팀장, 부장 등)은 text에서 제외한다.
  예) "윤선영은" → "윤선영", "전서윤님이" → "전서윤", "최지영 팀장" → "최지영"
- 띄어 쓴 이름은 띄어쓰기를 포함해 한 덩어리로 적는다. 예) "윤 선영"
- 성 없이 이름만 나온 경우도 사람 이름이면 포함한다. 예) "승현아" → "승현"
- 주소는 번지·호수까지 포함한다. 예) "서울시 중구 세종대로 110, 302호"
- 이메일, 번호 뒤에 붙은 조사는 제외한다. 예) "a@b.com로" → "a@b.com"

[개인정보가 아닌 것 (찾지 않는다)]
- 회사·고객센터 대표번호 (예: 1599-0000)
- 내선번호, 민원번호, 접수번호, 문서번호, 주문번호
- 날짜, 시간, 금액, 숫자 통계
- 상품명, 서비스명 (예: "○○ 플러스 카드")
- 성만 있고 이름이 없는 직함 표현 (예: "허 부장님", "김 대리")
- 이름처럼 생겼지만 일반 단어인 것 (예: "한결같이", "전 임직원")
"""

USER_TEMPLATE = "다음 문서에서 개인정보를 찾아라.\n\n<문서>\n{text}\n</문서>"


def parse_llm_json(raw: str) -> list[dict]:
    """LLM 응답에서 JSON 배열을 꺼내고, 형식이 틀린 항목은 버린다."""
    raw = re.sub(r"^```(?:json)?|```$", "", raw.strip(), flags=re.M).strip()
    m = re.search(r"\[.*\]", raw, re.S)
    try:
        items = json.loads(m.group()) if m else []
    except json.JSONDecodeError:
        items = []
    return [i for i in items
            if isinstance(i, dict) and i.get("label") in LABELS and i.get("text")]


def to_spans(text: str, items: list[dict]) -> list[Span]:
    """문자열이 원문 어디에 있는지 찾아 Span으로 바꾼다 (text == 원문[start:end] 보장)."""
    spans, seen = [], set()
    for it in items:
        for m in re.finditer(re.escape(it["text"]), text):
            key = (m.start(), m.end(), it["label"])
            if key not in seen:
                seen.add(key)
                spans.append(Span(m.start(), m.end(), it["label"], text[m.start():m.end()]))
    return sorted(spans, key=lambda s: s.start)


class LLMDetector(BaseDetector):
    name = "llm"

    def __init__(self, client=None, model: str | None = None):
        if client is None:
            from openai import OpenAI   # OpenAI 호환 API
            client = OpenAI(api_key=os.environ["LLM_API_KEY"],      # ← .env 변수명에 맞추기
                            base_url=os.environ.get("LLM_BASE_URL"))
        self.client = client
        self.model = model or os.environ.get("LLM_MODEL", "모델이름")

    def detect(self, text: str) -> list[Span]:
        resp = self.client.chat.completions.create(
            model=self.model, temperature=0,
            messages=[{"role": "system", "content": SYSTEM_PROMPT},
                      {"role": "user", "content": USER_TEMPLATE.format(text=text)}])
        return to_spans(text, parse_llm_json(resp.choices[0].message.content))