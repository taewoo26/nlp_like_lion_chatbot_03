"""LLM 탐지기. LLM에게는 문자열+라벨만 받고, 위치는 코드로 계산한다."""
import json
import os
import re
import warnings
from pathlib import Path

from dotenv import load_dotenv

from detection.base import BaseDetector, Span, LABELS

DEFAULT_MODEL = "HCX-005"
DEFAULT_BASE_URL = "https://clovastudio.stream.ntruss.com/v1/openai"
LLM_LABELS = frozenset(LABELS) - {"organization"}

SYSTEM_PROMPT = """너는 한국어 금융 사내 문서에서 개인정보를 찾아내는 탐지기다.
문서에 실제로 적힌 개인정보 문자열을 찾아 JSON 배열로만 답한다.

[라벨 7종]
- person: 사람 이름. 고객과 직원 이름을 모두 포함한다.
- phone_number: 개인 연락처 전화번호.
- email: 이메일 주소.
- address: 주소.
- resident_registration_number: 주민등록번호.
- account_number: 계좌번호.
- card_number: 카드번호.

[출력 규칙]
1. 형식: [{"text": "문서에 적힌 그대로의 문자열", "label": "라벨"}] 만 출력한다. 설명, 코드블록, 다른 글자를 쓰지 않는다.
2. text는 문서에서 한 글자도 바꾸지 말고 그대로 복사한다. 공백, 하이픈도 그대로 둔다.
3. 개인정보가 없으면 [] 를 출력한다.
4. 완전히 같은 문자열과 라벨의 조합만 한 번 적는다. 같은 사람이어도 표기가 다르면 각각 적는다.
5. 모든 항목의 키는 반드시 text와 label이다. address, person 같은 라벨명을 text 대신 키로 쓰지 않는다.

[누락 확인]
- 문서 처음부터 끝까지 확인한다. 머리말의 상담원·담당자, 본문의 고객·직원, 끝부분의 검토자·서명에 나온 이름도 추출한다.
- 한 사람을 찾은 뒤에도 다른 표기를 계속 찾는다. 붙여 쓴 풀네임, 띄어 쓴 풀네임, 성 없이 부르는 이름을 각각 확인한다.
- 예: 원문이 "오민재 담당자가 말했다. 오 민재님, 민재야 확인해 줘."이면 person 문자열은 "오민재", "오 민재", "민재" 세 가지다.
- 변형은 원문에 실제로 등장하고 문맥상 사람을 가리킬 때만 추출한다. 풀네임만 있다고 성 없는 이름을 추가하거나, 띄어쓰기를 새로 만들지 않는다.
- 같은 이름이 상품명이나 일반 단어의 일부로 쓰이면 사람 이름으로 추출하지 않는다.
- 최종 배열을 내기 전에 7종 라벨별로 빠진 항목과 잘못 포함한 호칭·설명어를 점검한다. 점검 과정은 출력하지 않는다.

[범위 규칙]
- 이름 뒤에 붙은 조사(은, 는, 이, 가, 을, 를, 과, 와, 랑, 아, 야 등)와 호칭(님, 씨, 고객, 팀장, 부장 등)은 text에서 제외한다.
  예) "윤선영은" → "윤선영", "전서윤님이" → "전서윤", "최지영 팀장" → "최지영"
- 띄어 쓴 이름은 띄어쓰기를 포함해 한 덩어리로 적는다. 예) "윤 선영"
- 성 없이 이름만 나온 경우도 사람 이름이면 포함한다. 예) "승현아" → "승현"
- 주소는 번지·호수까지 포함한다. 예) "서울시 중구 세종대로 110, 302호"
- 이메일, 번호 뒤에 붙은 조사는 제외한다. 예) "a@b.com로" → "a@b.com"
- 이름 앞의 부서명·역할과 뒤의 직함은 제외한다. 예) "운영팀 오민재 매니저" → "오민재"
- 계좌·카드번호는 번호 문자열만 추출한다. 은행명, "계좌" 같은 설명어, 번호를 감싼 괄호는 제외한다.

[개인정보가 아닌 것 (찾지 않는다)]
- 기관·회사명
- 회사·고객센터 대표번호 (예: 1599-0000)
- 내선번호, 민원번호, 접수번호, 문서번호, 주문번호
- 날짜, 시간, 금액, 숫자 통계
- 상품명, 서비스명 (예: "○○ 플러스 카드")
- 성만 있고 이름이 없는 직함 표현 (예: "허 부장님", "김 대리")
- 이름처럼 생겼지만 일반 단어인 것 (예: "한결같이", "전 임직원")
"""

USER_TEMPLATE = "다음 문서에서 개인정보를 찾아라.\n\n<문서>\n{text}\n</문서>"


def parse_llm_json(raw: str) -> list[dict]:
    """잘못된 응답을 개인정보 없음([])으로 처리하지 않고 오류로 알린다."""
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError("LLM 응답이 비어 있습니다.")
    raw = re.sub(r"^```(?:json)?|```$", "", raw.strip(), flags=re.M).strip()
    try:
        items = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("LLM 응답이 올바른 JSON이 아닙니다.") from exc
    if not isinstance(items, list):
        raise ValueError("LLM 응답은 JSON 배열이어야 합니다.")
    for index, item in enumerate(items):
        # text 대신 라벨명을 키로 쓴 경우만 복구한다. 값 자체는 바꾸지 않는다.
        if isinstance(item, dict) and isinstance(item.get("label"), str):
            label = item["label"]
            if (label in LLM_LABELS and set(item) == {"label", label}
                    and isinstance(item[label], str) and item[label].strip()):
                item = {"text": item[label], "label": label}
                items[index] = item
                warnings.warn(
                    f"LLM 응답 {index + 1}번째 항목: {label!r} 키를 'text'로 정규화했습니다.",
                    UserWarning,
                    stacklevel=2,
                )
        if (not isinstance(item, dict)
                or not isinstance(item.get("label"), str)
                or item["label"] not in LLM_LABELS
                or not isinstance(item.get("text"), str)
                or not item["text"].strip()):
            raise ValueError(f"LLM 응답 {index + 1}번째 항목에는 7종 라벨과 비어 있지 않은 문자열이 필요합니다. 문제 항목: {item!r}")
    return items


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
        load_dotenv(Path(__file__).resolve().parents[2] / ".env", override=False)
        if client is None:
            from openai import OpenAI   # OpenAI 호환 API
            api_key = os.environ.get("LLM_API_KEY", "").strip()
            if not api_key:
                raise ValueError(".env에 LLM_API_KEY를 설정해 주세요.")
            client = OpenAI(
                api_key=api_key,
                base_url=os.environ.get("LLM_BASE_URL", "").strip() or DEFAULT_BASE_URL,
                timeout=60.0,
                max_retries=0,
            )
        self.client = client
        self.model = model or os.environ.get("LLM_MODEL", "").strip() or DEFAULT_MODEL

    def detect(self, text: str) -> list[Span]:
        self.last_response = None
        resp = self.client.chat.completions.create(
            model=self.model, temperature=0, max_tokens=4096,
            messages=[{"role": "system", "content": SYSTEM_PROMPT},
                      {"role": "user", "content": USER_TEMPLATE.format(text=text)}])
        self.last_response = {
            "model": self.model,
            "finish_reason": resp.choices[0].finish_reason if resp.choices else None,
            "content": resp.choices[0].message.content if resp.choices else None,
        }
        if not resp.choices or resp.choices[0].finish_reason != "stop":
            raise ValueError("LLM 응답이 정상 완료되지 않았습니다. 출력 제한·차단 여부를 확인하세요.")
        return to_spans(text, parse_llm_json(resp.choices[0].message.content))
