"""탐지기 공통 인터페이스.

모든 탐지기(규칙 기반 / 토큰 분류 / LLM)는 이 형식으로 결과를 반환한다.
채점기, 가명처리, 출력 재검사가 모두 이 형식을 입력으로 사용한다.
"""
import json
import time
from dataclasses import dataclass, asdict

LABELS = [
    "person",
    "phone_number",
    # "organization",  # 채점기(run_compare.py) LABELS 7종에 없고 dev 데이터에도 0건 → 탐지 대상 제외
    "email",
    "address",
    "resident_registration_number",
    "account_number",
    "card_number",
]


@dataclass
class Span:
    start: int   # 원문 기준 시작 위치 (포함)
    end: int     # 원문 기준 끝 위치 (미포함)
    label: str   # LABELS 중 하나
    text: str    # text == 원문[start:end]

    def to_dict(self) -> dict:
        return asdict(self)


class BaseDetector:
    name: str = "base"

    def detect(self, text: str) -> list[Span]:
        raise NotImplementedError

    def predict_file(self, input_path: str, output_path: str) -> None:
        """jsonl 파일을 한 줄씩 읽어 detect()를 호출하고 결과를 run_compare.py 입력 형식으로 저장."""
        with open(input_path, encoding="utf-8") as fin, \
             open(output_path, "w", encoding="utf-8") as fout:
            for line in fin:
                line = line.strip()
                if not line:
                    continue
                record = json.loads(line)
                doc_id = record["id"]
                doc_text = record["text"]

                t0 = time.perf_counter()
                spans = self.detect(doc_text)
                ms = (time.perf_counter() - t0) * 1000  # 문서 1건 처리 시간(밀리초)

                entities = [
                    {"label": s.label, "start": s.start, "end": s.end}
                    for s in spans
                ]
                fout.write(json.dumps({"id": doc_id, "entities": entities, "ms": round(ms, 1)}, ensure_ascii=False) + "\n")

    def postprocess(self, text: str, spans: list[Span]) -> list[Span]:
        """탐지 후 경계 후처리. 적용 순서: B6 → B1·B2·B3·B4(person) → B5(비인명).

        B6 : 앞뒤 괄호·공백·슬래시 제거 (전체 라벨)
        B1 : person — 님·씨 제거
        B2 : person — 은·는·이·가·을·를 제거
        B3 : person — 아·야(호격) 제거
        B4 : person — 이랑·랑·입니다·께서 제거
        B5 : person 제외 — 로·으로·에서 제거
             (phone 등도 포함 — "(010…)로" 패턴 대응)
        B6·B5는 반복 루프로 통합: 조사 제거 후 드러나는 괄호를 연속 처리
        """
        # person 후처리 접미 목록 — 긴 패턴 먼저 (부분 매칭 방지)
        _P_TAIL = [
            "님", "씨",                          # B1
            "이", "가", "을", "를",               # B2 ("은"/"는"은 이름 끝글자로 자주 쓰여 제외)
            # B3 "아"/"야" 제거: 이름 자체가 "아"로 끝나는 케이스(허수아 등)를 보존하기 위해 제외
            "이랑", "입니다", "께서", "랑",        # B4 (이랑 > 랑)
        ]
        # 조사 제거 목록 — person 제외 전체 라벨 (긴 패턴 먼저)
        _JOSA = ["에서", "으로", "로"]
        # B6 대상 문자
        _WRAP = frozenset("() /")

        result = []
        for span in spans:
            s, e = span.start, span.end

            # ── B6 + B5 통합 루프 ──────────────────────────────────────────
            # "(010-0132-9355)로" 처럼 조사 제거 후 괄호가 드러나는 경우를 반복 처리
            changed = True
            while changed:
                changed = False
                # 앞뒤 괄호·공백·슬래시 제거
                while s < e and text[s] in _WRAP:
                    s += 1; changed = True
                while e > s and text[e - 1] in _WRAP:
                    e -= 1; changed = True
                # person 이름은 "로/에서" 등으로 끝날 수 있어 제외 (박서로 등)
                if span.label != "person":
                    for tail in _JOSA:
                        if (e - s) > len(tail) and text[s:e].endswith(tail):
                            e -= len(tail); changed = True; break

            # ── B1~B4: person 뒤 호칭·조사 반복 제거 ──────────────────────
            if span.label == "person":
                changed = True
                while changed:
                    changed = False
                    for tail in _P_TAIL:
                        if (e - s) > len(tail) and text[s:e].endswith(tail):
                            e -= len(tail); changed = True; break

            # ── 검증 assert ────────────────────────────────────────────────
            # 채점기가 start·end 위치 기준으로 원문을 잘라 비교하므로 위치 정합 필수
            assert s < e, (
                f"postprocess: 빈 span [{s}:{e}] label={span.label} "
                f"origin='{span.text}'"
            )
            assert s >= span.start and e <= span.end, (
                f"postprocess: 범위 확장 불가 "
                f"[{span.start}:{span.end}] → [{s}:{e}]"
            )
            assert text[s] not in _WRAP and text[e - 1] not in _WRAP, (
                f"postprocess B6 미완 (앞뒤 괄호·공백 잔류): '{text[s:e]}'"
            )

            if s == span.start and e == span.end:
                result.append(span)              # 변경 없으면 원본 재사용
            else:
                result.append(
                    Span(start=s, end=e, label=span.label, text=text[s:e])
                )

        return result
