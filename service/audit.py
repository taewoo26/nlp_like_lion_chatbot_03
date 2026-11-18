"""감사 로그: audit.query_log 기록 (질문마다 1행, 응답 직전).

    request_id = audit.new_request_id()               # 요청 시작 시 발급
    row = audit.build_row(request_id, user, result=res, latency_ms=ms)      # 정상 처리
    row = audit.build_row(request_id, user, error=exc, latency_ms=ms)       # 처리 중 예외
    audit.record(row)                                  # → "db" / "fallback" / "lost"

기록 규칙 (docs/permission_schema.md ⑧ 단계)
- request_id: req_<KST 날짜>_<uuid 앞 12자리>  예) req_20261117_3f9a1c07b2e4
- state: chat.answer()가 정한 값. 예외면 error.
- masked_query: 원문 금지. 가명처리 전에 실패해 가명 질의가 없으면 [MASK_FAILED].
- policy_version: INSERT 안에서 policy.policy_state.version 을 함께 읽는다 (조회를 따로 하지 않음).
- recheck_*: chat.answer() 결과의 recheck(starter/recheck.py)를 옮긴다. 재검사를 하지 않았으면(no_result·clarify·
  RECHECK=off·오류) NULL / {}. labels 는 라벨별 개수뿐이며 원문은 없다.
- 기록에 실패해도 답변은 막지 않는다. 대신
    1) DB 기록 실패 → 같은 행을 outputs/audit_fallback.jsonl 에 한 줄 추가 (policy_version 은 null)
    2) 실패 사실(시각·request_id·단계·예외 종류)은 outputs/server_error.log 와 터미널에 남긴다
    3) 대체 파일도 실패하면 server_error.log 에만 남는다 (행 내용 없음)
  예외 메시지 전체와 질문 원문은 어디에도 남기지 않는다.
- 기록 계정: 지금은 rag_user(db.rag_conn, audit 스키마 소유자). 고도화 때 app_search 로 바꾼다.
"""
import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path

from psycopg.types.json import Jsonb

from starter import db

KST = timezone(timedelta(hours=9))
MASK_FAILED = "[MASK_FAILED]"
OUTPUTS = Path(__file__).resolve().parent.parent / "outputs"     # .gitignore 로 Git 제외
FALLBACK_PATH = OUTPUTS / "audit_fallback.jsonl"
ERROR_LOG_PATH = OUTPUTS / "server_error.log"

_INSERT = """
INSERT INTO audit.query_log (
    request_id, user_id, level, team_cd, position,
    masked_query, query_tokens, returned_doc_ids,
    recheck_action, recheck_labels, recheck_detector,
    state, latency_ms, policy_version)
VALUES (
    %(request_id)s, %(user_id)s, %(level)s, %(team_cd)s, %(position)s,
    %(masked_query)s, %(query_tokens)s::text[], %(returned_doc_ids)s::text[],
    %(recheck_action)s, %(recheck_labels)s, %(recheck_detector)s,
    %(state)s, %(latency_ms)s,
    (SELECT version FROM policy.policy_state WHERE policy_state_id = 1))
"""

_logger = logging.getLogger("dojang.audit")


def _log():
    """터미널 + outputs/server_error.log (1MB × 3개 순환). 처음 쓸 때 한 번만 설정한다."""
    if not _logger.handlers:
        _logger.setLevel(logging.INFO)
        _logger.propagate = False
        fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%Y-%m-%d %H:%M:%S")
        console = logging.StreamHandler()
        console.setFormatter(fmt)
        _logger.addHandler(console)
        try:
            ERROR_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
            fh = RotatingFileHandler(ERROR_LOG_PATH, maxBytes=1_000_000, backupCount=3,
                                     encoding="utf-8", delay=True)   # 처음 기록할 때 파일 생성
            fh.setFormatter(fmt)
            _logger.addHandler(fh)
        except OSError:                     # 파일을 못 만들면 터미널에만 남긴다
            pass
    return _logger


def _log_error(msg, *args):
    try:
        _log().error(msg, *args)
    except Exception:                       # 로그 때문에 응답이 실패하면 안 된다
        pass


def new_request_id(now=None):
    now = now or datetime.now(KST)
    return f"req_{now:%Y%m%d}_{uuid.uuid4().hex[:12]}"


def build_row(request_id, user, *, result=None, error=None, latency_ms=None):
    """query_log 한 행(dict). result(chat.answer 결과)나 error(예외) 중 하나를 넘긴다."""
    if result is not None:
        masked, tokens = result["masked_query"], result["query_tokens"]
        doc_ids = [d["doc_id"] for d in result["docs"]]
        state = result["state"]
        rc = result.get("recheck") or {}
    else:
        masked = getattr(error, "masked_query", None)
        tokens = getattr(error, "query_tokens", None) or []
        if masked is None:                  # 가명처리 전에 실패: 원문은 남기지 않는다
            masked, tokens = MASK_FAILED, []
        doc_ids, state = [], "error"
        rc = {}
    return {
        "request_id": request_id,
        "user_id": user["user_id"],
        "level": user["access_level"],
        "team_cd": user.get("team_cd"),
        "position": user.get("position"),
        "masked_query": masked,
        "query_tokens": list(tokens),
        "returned_doc_ids": doc_ids,
        "recheck_action": rc.get("action"),
        "recheck_labels": dict(rc.get("labels") or {}),
        "recheck_detector": rc.get("detector"),
        "state": state,
        "latency_ms": latency_ms,
    }


def write_query_log(row, conn=None):
    """DB에 1행 INSERT. conn 을 넘기면 커밋하지 않는다 (호출한 쪽이 정함, 테스트는 롤백)."""
    params = dict(row, recheck_labels=Jsonb(row["recheck_labels"]))
    own = conn is None
    conn = conn or db.rag_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(_INSERT, params)
        if own:
            conn.commit()
    except Exception:
        if own:
            conn.rollback()
        raise
    finally:
        if own:
            conn.close()


def _append_fallback(row, db_error):
    line = dict(row, created_at=datetime.now(KST).isoformat(timespec="seconds"),
                policy_version=None, db_error=db_error)
    FALLBACK_PATH.parent.mkdir(parents=True, exist_ok=True)
    with FALLBACK_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(line, ensure_ascii=False) + "\n")


def record(row, conn=None):
    """query_log 를 남긴다. 예외를 올리지 않는다. 반환: "db" / "fallback" / "lost"."""
    rid = row.get("request_id")
    try:
        write_query_log(row, conn)
        return "db"
    except Exception as exc:
        db_error = type(exc).__name__
    try:
        _append_fallback(row, db_error)
    except Exception as exc:
        _log_error("query_log 기록 실패, fallback 저장도 실패 request_id=%s db_error=%s fallback_error=%s",
                   rid, db_error, type(exc).__name__)
        return "lost"
    _log_error("query_log 기록 실패 → fallback 저장 request_id=%s error=%s", rid, db_error)
    return "fallback"
