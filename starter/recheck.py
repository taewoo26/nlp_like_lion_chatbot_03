"""출력 재검사: 화면으로 나가는 글(LLM 답변·문서 조각)에 원문 개인정보가 남아 있는지 다시 확인한다.

기준: docs/permission_schema.md ⑥ 단계, 4.1절(recheck_*). chat.answer() 가 LLM 응답 직후(실명 복원 전)에 부른다.

- 대상: 화면으로 나가는 글 = LLM 답변(전체) + 문서 조각. state 와 상관없이 문서가 나가면 검사한다.
  2026-11-27 (UI/UX 3-5): 문서 팝업에서 찾은 조각 전체를 보여 주기 위해 조각(청크, 최대 600자) 전체를 검사한다.
  검사한 조각 전체는 docs[*].chunk_text, 그 앞 SNIPPET_CHARS(180)자는 docs[*].snippet 으로 내보낸다.
  (이전에는 앞 200자만 검사하고 snippet 만 내보냈다. 검사 범위가 넓어져 masked·blocked 가 전보다 자주 나올 수 있다)
  masked_text(검사 전 원본 조각)는 화면·응답으로 내보내지 않는다. 내보낼 때는 snippet·chunk_text 만 쓴다.
  문서 전체(여러 조각) 보기 경로가 생기면 그 경로에서도 재검사해야 한다.
    answered·withheld : 답변 + 문서 조각
    search_only       : 문서 조각 (LLM 답변 없음)
    clarify           : 문서 조각만 (되묻기 문구는 시스템 고정 문장). 인물 되묻기(ask_person)는 문서를 함께 돌려준다.
    no_result, 문서 없는 clarify : 재검사하지 않음 (query_log 의 recheck_* NULL)
- 탐지: RECHECK_DETECTOR = split_v3(기본, 적재와 같은 탐지기) | union(재현율 우선, 같은 모델의 결과를 모두 합침).
  행정구역 이름 오탐 제거(pii_pipeline.drop_region_names)는 적재와 같다.
- 이미 가린 구간(가명 토큰 <LABEL_hex>, [운전면허번호 비공개], [개인정보])과 겹치는 탐지는 버린다.
- 지역만 있는 주소('대구 수성구')는 적재 때도 원문으로 남기므로 적발로 보지 않는다.
- 탐지기에는 마크다운 굵은 글씨 기호 '**' 를 같은 길이의 공백으로 바꾼 글을 넣는다 (위치는 그대로).
  LLM 답변의 '- **방문처**: 협력사 사업장' 형식이 대화 기록의 말한 사람 이름처럼 보여 항목명을 이름으로 잡았기 때문이다
  (2026-11-19 실서버, experiments/recheck/diagnose_query 로 확인. '*'를 지우면 방문처는 빠지고 실제 이름은 남음).
  정확히 별표 두 개만 바꾼다 ('010-****-1234' 같은 가림 표기는 그대로).
- 이름 후보가 '이름이 아닌 단어'와 똑같으면 버린다: 적재 규칙의 단어 목록(detection/extra_rules.py 의 FORM_LABELS·
  ROLE_WORDS·KIN_EVENTS·EMPTY_NAME_VALUES)을 그대로 쓰고, 재검사에서만 나온 오탐은 NOT_NAMES 에 근거와 함께 추가한다.
  (적재 규칙 파일은 고치지 않는다. '줄 맨 앞 + 콜론' 같은 형태 규칙은 메신저 대화의 실제 이름 '김서연: …'까지 버려서 쓰지 않는다)
- 조치 (recheck_action)
    none    : 적발 없음
    masked  : 적발 구간을 pii_pipeline.pseudonymize(strict=False)로 적재와 같은 규칙으로 토큰화한다 (운전면허번호는 비공개 표시).
              vault 에는 쓰지 않는다. 같은 값이 적재 때 토큰화된 적이 있으면 같은 토큰이라 Lv2 가 복원할 수 있고,
              처음 보는 값이면 vault 에 없어 복원되지 않는다.
    blocked : RECHECK_BLOCK_LABELS(기본: 주민등록번호·카드번호·계좌번호, 임시값·팀 합의 필요)가 하나라도 나오면
              답변을 안내 문구로 바꾸고 문서 조각(snippet)과 본문을 모두 비운다. 비워 두면(RECHECK_BLOCK_LABELS=) 차단하지 않는다.
- recheck_labels 는 적발 라벨별 개수(답변 + 문서 합계). 원문은 담지 않는다.
  적발 = '재검사가 개인정보로 의심한 것'이다. 적재 누락뿐 아니라 LLM 이 만든 값과 재검사 탐지기의 오탐도 섞인다.
- 재검사 중 예외는 그대로 올린다. 검사하지 못한 글을 내보내지 않기 위함이다 (/api/chat 이 state=error 로 기록).
- 결과의 ms 는 재검사에 걸린 시간(탐지·토큰화, 밀리초)이다. 서버 터미널 로그와 starter.chat 출력에만 쓰고 query_log 에는 넣지 않는다.
- RECHECK=off 면 건너뛴다(결과 None). 테스트 기본값(tests/conftest.py)이며 비교 실험용이다.
"""
import re
import time
from collections import Counter
from functools import lru_cache

from detection.extra_rules import EMPTY_NAME_VALUES, FORM_LABELS, KIN_EVENTS, ROLE_WORDS

from . import config, pii_pipeline
from .address import region_bounds

SNIPPET_CHARS = 180      # 화면·API 로 나가는 문서 조각 길이 (service/app.py 가 그대로 내보냄)
WINDOW_CHARS = None      # 문서 재검사 범위 (None = 조각 전체. 숫자를 넣으면 앞 N자만 검사하고 chunk_text 도 그 길이까지만)
BLOCKED_MESSAGE = "보호가 필요한 개인정보가 포함되어 답변을 표시하지 않았습니다. 관리자에게 문의해 주세요."
DEFAULT_BLOCK_LABELS = "resident_registration_number,card_number,account_number"
POLICIES = ("split_v3", "union")

# 재검사에서만 이름으로 잡힌 일반 단어 (근거를 함께 적는다)
NOT_NAMES = frozenset({
    "방문처",      # 2026-11-19 실서버 오탐: LLM 답변 '- **방문처**: 협력사 사업장' (출장 보고서 항목명). '**' 처리와 함께 이중 안전장치
    "반송",        # holdout 조각 H0152 앞 200자에서 이름으로 잡힘 ('우편물 반송 처리 기록'). 원문에선 안 잡히고 가명처리 후 문맥에서 잡힘
    "인사팀",      # holdout 조각 H0005 앞 200자에서 이름으로 잡힘 (부서명). 원문에선 안 잡히고 가명처리 후 문맥에서 잡힘
})
_BOLD = re.compile(r"(?<!\*)\*\*(?!\*)")     # 정확히 별표 두 개 ('****' 같은 가림 표기는 제외)
_NOT_NAME_WORDS = frozenset(FORM_LABELS | ROLE_WORDS | KIN_EVENTS | EMPTY_NAME_VALUES) | NOT_NAMES

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
    scan = _BOLD.sub("  ", text)                  # 마크다운 굵은 글씨 기호만 공백으로 (길이 같음 → 위치 그대로)
    spans = [{"label": s.label, "start": s.start, "end": s.end} for s in _detector(_policy()).detect(scan)]
    spans = pii_pipeline.drop_region_names(scan, spans)
    protected = [m.span() for m in _PROTECTED.finditer(text)]
    return [s for s in spans
            if not any(s["start"] < e and b < s["end"] for b, e in protected)
            and not (s["label"] == "address" and _region_only(text, s))
            and not (s["label"] == "person" and text[s["start"]:s["end"]].strip() in _NOT_NAME_WORDS)]


def cut(text, n):
    """앞 n 자. 경계에 걸친 토큰·비공개 표시는 반으로 자르지 않고 끝까지 포함한다 (화면에 '<P' 같은 조각이 남지 않게)."""
    text = text or ""
    end = min(len(text), n)
    for m in _PROTECTED.finditer(text, 0, end + 64):
        if m.start() < end < m.end():
            end = m.end()
    return text[:end]


def window(text):
    """문서 재검사 범위: 조각 전체 (WINDOW_CHARS 를 정하면 앞 N자)."""
    if WINDOW_CHARS is None:                    # 기본: 조각 전체
        return text or ""
    return cut(text, WINDOW_CHARS)              # 숫자를 정했으면 앞부분만


def default_snippets(result):
    """재검사를 하지 않은 경우(RECHECK=off 등)의 문서 조각: 앞 SNIPPET_CHARS 자와 조각 전체."""
    for d in result["docs"]:
        d.setdefault("snippet", cut(d.get("masked_text"), SNIPPET_CHARS))   # 칩 미리보기용
        d.setdefault("chunk_text", window(d.get("masked_text")))            # 팝업용 (재검사 off 는 실험용 설정)


def check(result, check_answer=True):
    """chat.answer() 결과의 answer 를 고치고 docs[*].snippet 을 만든다. check_answer=False 면 답변(시스템 문구)은 보지 않는다.
    반환: {"action", "labels", "detector"}. RECHECK=off 면 None (아무것도 바꾸지 않음, 조각은 default_snippets)."""
    if not enabled():
        return None
    started = time.perf_counter()
    answer_spans = find(result.get("answer")) if check_answer else []
    windows = [window(d.get("masked_text")) for d in result["docs"]]
    doc_spans = [find(w) for w in windows]
    labels = Counter(s["label"] for s in answer_spans)
    for spans in doc_spans:
        labels.update(s["label"] for s in spans)

    if labels.keys() & block_labels():
        result["answer"] = BLOCKED_MESSAGE
        for d in result["docs"]:
            d["snippet"] = d["masked_text"] = d["chunk_text"] = ""     # 문서 ID 는 남긴다 (query_log.returned_doc_ids 유지)
        return _summary("blocked", labels, started)

    if labels:
        action = "masked"
        if answer_spans:
            result["answer"] = pii_pipeline.pseudonymize(result["answer"], answer_spans, strict=False)[0]
    else:
        action = "none"
    for d, w, spans in zip(result["docs"], windows, doc_spans):
        checked = pii_pipeline.pseudonymize(w, spans, strict=False)[0] if spans else w
        d["snippet"] = cut(checked, SNIPPET_CHARS)    # 칩 미리보기 (앞 180자)
        d["chunk_text"] = checked                     # 팝업에 보여 줄 조각 전체 (재검사를 거친 것)
    return _summary(action, labels, started)


def _summary(action, labels, started):
    return {"action": action, "labels": dict(labels), "detector": detector_name(),
            "ms": int((time.perf_counter() - started) * 1000)}
