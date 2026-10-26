"""LLM 탐지기 (OpenAI 호환 API).

환경변수 (.env)
    LLM_API_KEY   : 매니저가 제공하는 키 (HCX: CLOVA Studio API 키)
    LLM_BASE_URL  : OpenAI 호환 엔드포인트
                    HCX → https://clovastudio.stream.ntruss.com/v1/openai
    LLM_MODEL     : 모델 이름 (HCX → HCX-005 등)
    LLM_MAX_TOKENS: 답변 최대 길이 (기본 2048, HCX 기본값 512는 잘림 위험)
    LLM_MIN_INTERVAL: 호출 사이 최소 간격 초 (기본 1.0, 429가 계속 나면 늘리기)
    LLM_MAX_RETRIES : 실패 시 재시도 횟수 (기본 8, 속도 제한이면 2→4→8…초 대기)

설계
- LLM은 글자 위치를 정확히 못 세므로 위치는 묻지 않는다. "원문 그대로의 문자열 + 라벨"만 받고,
  코드가 원문에서 그 문자열을 찾아 위치를 계산한다 (같은 문자열이 여러 번 나오면 전부)
- temperature=0, 실패 시 1회 재시도. 일관성은 run_detect --runs 3 으로 측정
- 프롬프트는 팀 라벨 가이드(2·3장)를 요약해서 넣는다
- ⚠ 원문을 외부 API로 보낸다: 가상 데이터 탐지 실험에서만 허용. 운영 적용 가능성은 보고서에 따로 정리
"""
import json
import os
import re
import time

from detection.base import LABELS, BaseDetector, Span

SYSTEM_PROMPT = """너는 금융사 사내 문서에서 개인정보를 찾는 탐지기다.
아래 7가지 라벨만 찾는다.
- person: 특정 개인의 이름. 고객·직원·외부 업체 담당자 모두. 성+이름, 띄어 쓴 이름(윤 선영), 이름만(소율 씨의 '소율', 승현아의 '승현')
- phone_number: 개인 휴대전화·업무 직통번호 (010-1234-5678, 01012345678, 010.1234.5678, 02-123-4567 등)
- email: 이메일 주소
- address: 시·도부터 호수·층까지의 도로명 주소
- resident_registration_number: 주민등록번호 (900101-1234567)
- account_number: 계좌번호 (숫자 부분만, 은행명 제외)
- card_number: 카드번호 16자리

찾지 않는 것:
- 성+직함만 있는 표현(김 대리, 정 부장, 허 부장님), 회사·부서·은행·상품명(새봄카드, 고객보호팀, 하린 포인트 카드)
- 대표번호(1599-0000), 내선번호(내선 1234), 민원번호·접수번호, 날짜, 시각, 금액

규칙:
- text에는 문서에 적힌 문자열을 한 글자도 바꾸지 말고 그대로 쓴다 (띄어쓰기·하이픈 포함)
- 이름 뒤 조사·호칭(은, 이, 님, 씨, 고객, 팀장)과 괄호는 text에 넣지 않는다
- 같은 값이 여러 번 나오면 한 번만 적어도 된다
- 반드시 JSON만 출력한다: {"entities": [{"text": "...", "label": "..."}]}"""


class LLMDetector(BaseDetector):
    name = "llm"

    def __init__(self):
        from dotenv import load_dotenv
        from openai import OpenAI

        load_dotenv()
        key = os.getenv("LLM_API_KEY")
        if not key:
            raise SystemExit(".env에 LLM_API_KEY가 없습니다.")
        self.client = OpenAI(api_key=key, base_url=os.getenv("LLM_BASE_URL") or None)
        self.model = os.getenv("LLM_MODEL")
        if not self.model:
            raise SystemExit(".env에 LLM_MODEL(모델 이름)을 넣어 주세요.")
        self.min_interval = float(os.getenv("LLM_MIN_INTERVAL", "1.0"))   # 호출 사이 최소 초
        self.max_retries = int(os.getenv("LLM_MAX_RETRIES", "8"))
        self._last_call = 0.0
        self.failures = 0   # 끝까지 실패한 문서 수 (run_detect가 출력)
        self.waited = 0.0   # 대기·재시도로 쉰 시간(초). 처리 시간에서 빼서 속도를 공정하게 잼

    def _ask(self, text: str) -> list[dict]:
        # 호출 사이 최소 간격 (HCX 속도 제한 429 예방)
        wait = self.min_interval - (time.time() - self._last_call)
        if wait > 0:
            time.sleep(wait)
            self.waited += wait
        delay = 2.0
        for attempt in range(1, self.max_retries + 1):
            self._last_call = time.time()
            try:
                resp = self.client.chat.completions.create(
                    model=self.model,
                    temperature=0,
                    # HCX(CLOVA Studio) 기본값이 512라 개체가 많은 문서에서 JSON이 잘림
                    max_tokens=int(os.getenv("LLM_MAX_TOKENS", "2048")),
                    messages=[
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": f"문서:\n{text}"},
                    ],
                )
                return self._parse(resp.choices[0].message.content or "")
            except Exception as e:
                rate_limited = "429" in str(e) or "rate" in str(e).lower()
                if attempt == self.max_retries:
                    self.failures += 1
                    print(f"⚠ LLM 호출 최종 실패 ({attempt}회 시도): {str(e)[:120]}")
                    return []
                # 속도 제한이면 점점 길게 기다림 (2 → 4 → 8 … 최대 60초)
                sleep = min(delay, 60) if rate_limited else 2.0
                print(f"  … {'속도 제한' if rate_limited else '오류'}, {sleep:.0f}초 뒤 재시도 ({attempt}/{self.max_retries})")
                time.sleep(sleep)
                self.waited += sleep
                delay *= 2
        return []

    @staticmethod
    def _parse(content: str) -> list[dict]:
        m = re.search(r"\{.*\}", content, re.S)   # 코드블록 등 앞뒤 군더더기 제거
        if not m:
            return []
        try:
            data = json.loads(m.group())
        except json.JSONDecodeError:
            return []
        return [e for e in data.get("entities", []) if isinstance(e, dict)]

    def detect(self, text: str) -> list[Span]:
        self.waited = 0.0
        spans = []
        for e in self._ask(text):
            value, label = str(e.get("text", "")).strip(), e.get("label")
            if not value or label not in LABELS:
                continue
            # 원문에서 모든 위치 찾기. 못 찾으면(LLM이 문자열을 바꿈) 미탐으로 남는다
            for m in re.finditer(re.escape(value), text):
                spans.append(Span(m.start(), m.end(), label, value, "llm"))
        return spans
