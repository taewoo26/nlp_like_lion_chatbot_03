"""역할 LLM 보조 추론 (메타데이터 규칙 정의서 v0.2 ROLE-20~23). 기본은 꺼짐.

    .env  ROLE_LLM=off (기본) | on

  ROLE-20 규칙 결과가 unknown인 언급만 묻는다 (호출 쪽 person_role_mapping 이 거른다)
  ROLE-21 입력은 가명 텍스트의 해당 줄과 앞뒤 1줄만. 원문·다른 문서는 넣지 않는다
  ROLE-22 답은 역할 6종 중 하나만 받고, 그 밖의 답·확신 낮음·호출 실패·시간 초과는 unknown
  ROLE-23 LLM으로 정한 역할은 source=llm 으로 기록 (호출 쪽)
꺼져 있으면 make_classifier()가 None을 돌려주고, 규칙 결과만으로 그대로 동작한다 (COM-09).
"""
import re

from . import config
from .role_master import ROLE_CODES

PROMPT = """아래는 개인정보가 가명 토큰으로 바뀐 금융 문서의 일부입니다.
토큰 {token} 인 사람이 이 문서에서 맡은 역할을 아래 중 하나로만 답하세요.
customer(고객), employee(상담원이 아닌 사내 직원), agent(고객 응대 상담원),
staff_contact(문서에 연락 창구로 적힌 담당자), guardian(보호자·대리인), unknown(판단 불가)
확실하지 않으면 unknown 이라고 답하세요. 역할 코드 한 단어만 쓰세요.

문서:
{context}"""


def enabled():
    mode = (config.env("ROLE_LLM", "off") or "off").strip().lower()
    if mode not in ("off", "on"):
        raise ValueError(f"ROLE_LLM 은 off 또는 on 이어야 합니다: {mode}")
    return mode == "on"


def context_lines(masked, start, end):
    """가명 텍스트에서 토큰이 있는 줄과 앞뒤 1줄 (ROLE-21)."""
    lines = masked.split("\n")
    row = masked.count("\n", 0, start)
    return "\n".join(lines[max(0, row - 1): row + 2])


def parse_role(answer):
    words = re.findall(r"[a-z_]+", (answer or "").lower())
    roles = [w for w in words if w in ROLE_CODES]
    return roles[0] if len(set(roles)) == 1 else "unknown"


def _post(prompt):
    import requests
    url = config.LLM_API_BASE.rstrip("/") + "/chat/completions"
    r = requests.post(url, headers={"Authorization": f"Bearer {config.LLM_API_KEY}"}, timeout=30,
                      json={"model": config.LLM_MODEL, "temperature": 0,
                            "messages": [{"role": "user", "content": prompt}]})
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]


def make_classifier(post=None):
    """켜져 있으면 (context, token) -> role 함수, 꺼져 있으면 None."""
    if not enabled():
        return None
    post = post or _post

    def classify(context, token):
        try:
            return parse_role(post(PROMPT.format(token=token, context=context)))
        except Exception:
            return "unknown"
    return classify
