"""출력 재검사: 화면으로 나가는 글(LLM 답변·문서 조각)에 원문 개인정보가 남아 있는지 다시 확인한다.

기준: docs/permission_schema.md ⑥ 단계, 4.1절(recheck_*). chat.answer() 가 LLM 응답 직후(실명 복원 전)에 부른다.

- 대상: 결과의 answer 와 docs[*].masked_text. search_only(LLM 없음)도 포함한다 (문서 조각이 화면에 나가므로).
  no_result·clarify 는 내보내는 문서가 없어 재검사하지 않는다 (query_log 의 recheck_* NULL).
- 탐지: RECHECK_DETECTOR = split_v3(기본, 적재와 같은 탐지기) | union(재현율 우선, 같은 모델의 결과를 모두 합침).
  행정구역 이름 오탐 제거(pii_pipeline.drop_region_names)는 적재와 같다.
- 이미 가린 구간(가명 토큰 <LABEL_hex>, [운전면허번호 비공개], [개인정보])과 겹치는 탐지는 버린다.
- 지역만 있는 주소('대구 수성구')는 적재 때도 원문으로 남기므로 적발로 보지 않는다.
- 조치 (recheck_action)
    none    : 적발 없음
    masked  : pii_pipeline.pseudonymize(strict=False)로 적재와 같은 규칙으로 토큰화한다 (운전면허번호는 비공개 표시).
              vault 에는 쓰지 않는다. 같은 값이 적재 때 토큰화된 적이 있으면 같은 토큰이라 Lv2 가 복원할 수 있고,
              처음 보는 값이면 vault 에 없어 복원되지 않는다.
    blocked : RECHECK_BLOCK_LABELS(기본: 주민등록번호·카드번호·계좌번호, 임시값·팀 합의 필요)가 하나라도 나오면
              답변을 안내 문구로 바꾸고 문서 조각을 모두 비운다. 비워 두면(RECHECK_BLOCK_LABELS=) 차단하지 않는다.
- recheck_labels 는 적발 라벨별 개수(답변 + 문서 합계). 원문은 담지 않는다.
  적발 = '재검사가 개인정보로 의심한 것'이다. 적재 누락뿐 아니라 LLM 이 만든 값과 재검사 탐지기의 오탐도 섞인다.
- 재검사 중 예외는 그대로 올린다. 검사하지 못한 글을 내보내지 않기 위함이다 (/api/chat 이 state=error 로 기록).
- RECHECK=off 면 건너뛴다(결과 None). 테스트 기본값(tests/conftest.py)이며 비교 실험용이다.
"""
import re
from collections import Counter
from functools import lru_cache

from . import config, pii_pipeline
from .address import region_bounds

BLOCKED_MESSAGE = "보호가 필요한 개인정보가 포함되어 답변을 표시하지 않았습니다. 관리자에게 문의해 주세요."
DEFAULT_BLOCK_LABELS = "resident_registration_number,card_number,account_number"
POLICIES = ("split_v3", "union")

_PROTECTED = re.compile("|".join([
    pii_pipeline.TOKEN_PATTERN.pattern,
    re.escape(pii_pipeline.DELETED_TOKEN),
    re.escape(pii_pipeline.UNSUPPORTED_MARK),
]))


def enabled():
    mode = (config.env("RECHECK", "on") or "on").strip().lower()
    if mode not in ("on", "off"):
        raise ValueError(f"RECHECK 는 on 또는 off 여야 합니다: {mode}")
    return mode == "on"


def block_labels():
    raw = config.env("RECHECK_BLOCK_LABELS", DEFAULT_BLOCK_LABELS) or ""
    return frozenset(x.strip() for x in raw.split(",") if x.strip())


def _policy():
    policy = (config.env("RECHECK_DETECTOR", "split_v3") or "split_v3").strip()
    if policy not in POLICIES:
        raise ValueError(f"RECHECK_DETECTOR 는 {' 또는 '.join(POLICIES)} 여야 합니다: {policy}")
    return policy


@lru_cache(maxsize=None)
def _detector(policy):
    if policy == "split_v3":
        return pii_pipeline._detector()          # 적재와 같은 인스턴스 (모델을 두 번 올리지 않음)
    from detection.combo import ComboDetector
    return ComboDetector("koelectra_post", policy)


def detector_name():
    return _detector(_policy()).name


def _region_only(text, span):
    original = text[span["start"]:span["end"]]
    bounds = region_bounds(original)
    return bool(bounds) and not original[bounds[1]:].strip(" ,")


def find(text):
    """원문 개인정보로 보이는 구간 [{"label", "start", "end"}]. 이미 가린 구간·지역만 있는 주소는 뺀다."""
    if not text:
        return []
    spans = [{"label": s.label, "start": s.start, "end": s.end} for s in _detector(_policy()).detect(text)]
    spans = pii_pipeline.drop_region_names(text, spans)
    protected = [m.span() for m in _PROTECTED.finditer(text)]
    return [s for s in spans
            if not any(s["start"] < e and b < s["end"] for b, e in protected)
            and not (s["label"] == "address" and _region_only(text, s))]


def check(result):
    """chat.answer() 결과의 answer·docs 를 제자리에서 고친다.
    반환: {"action", "labels", "detector"}. RECHECK=off 면 None (아무것도 바꾸지 않음)."""
    if not enabled():
        return None
    answer_spans = find(result.get("answer"))
    doc_spans = [find(d.get("masked_text")) for d in result["docs"]]
    labels = Counter(s["label"] for s in answer_spans)
    for spans in doc_spans:
        labels.update(s["label"] for s in spans)

    if labels.keys() & block_labels():
        action = "blocked"
        result["answer"] = BLOCKED_MESSAGE
        for d in result["docs"]:
            d["masked_text"] = ""                    # 문서 ID 는 남기고 조각만 비운다 (query_log.returned_doc_ids 유지)
    elif labels:
        action = "masked"
        if answer_spans:
            result["answer"] = pii_pipeline.pseudonymize(result["answer"], answer_spans, strict=False)[0]
        for d, spans in zip(result["docs"], doc_spans):
            if spans:
                d["masked_text"] = pii_pipeline.pseudonymize(d["masked_text"], spans, strict=False)[0]
    else:
        action = "none"
    return {"action": action, "labels": dict(labels), "detector": detector_name()}
