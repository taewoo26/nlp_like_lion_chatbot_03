"""도장곰 창구: 가명처리 기반 검색 챗봇 데모 서버.

    uvicorn service.app:app --reload          (레포 루트에서 실행)
    → http://127.0.0.1:8000

- 화면: service/static/index.html (챗봇, 로그인 필요), service/static/login.html (로그인)
       로그인하지 않고 / 에 들어오면 /login 으로, 로그인한 상태로 /login 에 들어오면 / 로 보낸다.
- API : POST /api/login {"user_id": "SB260001", "password": "..."} → {"user": {...}} + 세션 쿠키
        실패: 401 invalid(사번 없음·비밀번호 틀림) · 403 inactive(비활성 계정)
        POST /api/logout → {"ok": true}
        GET  /api/me → 로그인한 사용자. 로그인하지 않았으면 401
        POST /api/chat {"query": "...", "k": 3}   (로그인 필요, 아니면 401)
        → {"request_id", "masked_query", "query_tokens", "docs":[{"doc_id","snippet"}], "answer", "state"}
- 로그인 유지: 서명된 세션 쿠키(SESSION_SECRET). 쿠키에는 사번만 두고, 권한·계정 상태는 요청마다
  DB(policy.users)에서 다시 읽는다. 브라우저를 닫거나 로그아웃하면 끝난다.
  고도화 때 추가 예정: 미사용 시 자동 로그아웃(F-03), 로그인 실패 잠금(F-68).
- 질문 원문은 서버 로그·응답 어디에도 남기지 않는다. 응답에는 가명처리된 질문과 문서만 담는다.
- state(화면용): found(아하!) / sorry(되물음·문서 없음·답변 보류) / nollm(LLM 키 없음, 검색 결과만)
- 재질문(starter/clarify.py): 응답의 need_more=True 면 화면이 '조건 확인 필요'로 표시하고, 바로 다음 질문에
  {"follow_up_of": 이 응답의 request_id} 를 붙여 보낸다. 본인 질문 id 일 때만 query_log.follow_up_of 에 남는다.
  직전 재질문이 MERGE_WINDOW_MIN 분 안이고 지금 질문이 조각(번호만·지역만·역할만)이면 직전 가명 질문과 합쳐 검색한다
  (응답 merged=True). 원문은 다시 쓰지 않고 감사 로그의 가명 질문·토큰만 쓴다.
  chat.answer()의 state(감사 로그용 6개 값)를 UI_STATE 로 바꿔 보낸다. answered→found, search_only→nollm, 나머지→sorry
- 감사 로그: 질문마다 audit.query_log 1행 (service/audit.py). 처리 중 예외도 state=error 로 남긴다.
- 출력 재검사(starter/recheck.py): 결과는 query_log.recheck_*, 걸린 시간은 서버 터미널 한 줄(recheck_ms·total_ms).
  문서는 재검사를 거친 snippet 만 내보낸다.
  기록에 실패해도 답변은 그대로 돌려준다 (outputs/audit_fallback.jsonl, outputs/server_error.log 에 남김).
- 로그인 만료: 로그인할 때 세션에 expires_at(SESSION_MINUTES, 기본 30분)을 두고 요청마다 서버가 확인한다.
        POST /api/session/extend → 남은 시간을 다시 30분으로 (화면의 '연장' 버튼·만료 2분 전 안내 창)
        GET  /api/me 는 session_remaining(남은 초)과 reveal(원복 상태)도 돌려준다.
- 원복·부분 공개 유지 시간: POST /api/verify-password {"password", "minutes": 0|5|15|30, "reason"}
        세션 reveal = {mode, until, reason}. 시간이 지나면 서버가 끈다. POST /api/reveal-off 로 바로 끈다.
        {"purpose": "confirm"} 이면 비밀번호 확인만 한다 (관리실 레벨 변경 등, 원복 상태는 그대로).
- 관리실 문서 복원: POST /api/admin/restore-doc-tokens {"doc_id", "tokens"} → vault.restore_doc_token()
        restore_log 에 reason(복원 사유)·doc_id 를 함께 남긴다.
- 실명 복원(Lv2 + 2차 인증): POST /api/verify-password {"password"} → 세션 reveal(mode=full)
        POST /api/restore-tokens {"request_id", "tokens": [...]} → {"map": {토큰: 원문}}
  복원은 vault.restore_token()으로만 한다. 토큰마다 audit.restore_log 1행이 남고 request_id 로 query_log 와 이어진다.
  기록에 실패하면 복원도 실패한다(화면에는 토큰 그대로). query_log 와 달리 실명이 나가는 쪽이라 기록 없는 복원은 하지 않는다.
  DB 접속은 db.restore_conn() (app_search 계정. SEARCH_DSN 이 없으면 관리자 계정으로 대체).
- 이름 부분 공개(Lv1 + 2차 인증): POST /api/verify-password {"password"} → 세션 reveal(mode=partial)
        POST /api/partial-tokens {"request_id", "tokens": [...]} → {"map": {토큰: "윤*영"}}
  vault.partial_token()으로만 한다. DB 함수가 Lv1 확인 → 본인 질문(query_log) 확인 → 이름 토큰이고 그 답변에 나온
  토큰인지 확인 → 마스킹 → audit.restore_log(mode=partial) 기록을 한다. 원문은 DB 밖으로 나오지 않는다.
"""
import logging
import re
import secrets
import time
from contextlib import asynccontextmanager
from pathlib import Path

import psycopg
from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.middleware.sessions import SessionMiddleware

from policy.access_policy import load_policy
from service import audit, auth
from starter import clarify, chat, config, db, pii_pipeline

STATIC = Path(__file__).with_name("static")
UI_STATE = {"answered": "found", "search_only": "nollm"}   # 그 밖의 state 는 화면에서 sorry
MIN_SECRET_LENGTH = 16
_SESSION_CACHE_TTL = 60   # 세션 유저 정보 DB 재조회 주기(초). 이 시간 안에는 캐시 사용
SESSION_SECONDS = int(config.env("SESSION_MINUTES", "30") or "30") * 60   # 로그인 유지 시간(초). 연장 버튼을 누를 때만 다시 채운다
REVEAL_MINUTES = (0, 5, 15, 30)   # 원복·부분 공개 유지 시간 선택지(분). 0 = 로그아웃할 때까지


@asynccontextmanager
async def lifespan(_app):
    fmt = logging.Formatter("%(levelname)-7s\t%(asctime)s\t%(message)s", datefmt="%Y-%m-%d %H:%M:%S")
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        for h in logging.getLogger(name).handlers:
            h.setFormatter(fmt)
    if len(config.SESSION_SECRET) < MIN_SECRET_LENGTH:
        raise RuntimeError(
            ".env 의 SESSION_SECRET 이 없거나 너무 짧습니다(%d자 이상). 아래 명령으로 만들어 넣어 주세요:\n"
            '  python -c "import secrets; print(secrets.token_urlsafe(32))"' % MIN_SECRET_LENGTH)
    pii_pipeline._detector()          # 탐지 모델을 미리 불러 첫 질문이 느려지지 않게 한다
    yield


app = FastAPI(title="도장곰 창구", lifespan=lifespan)
# 쿠키는 브라우저를 닫으면 사라지는 세션 쿠키(max_age=None)이고 JavaScript에서 읽을 수 없다(HttpOnly).
# 로컬은 http 라서 https_only=False. https 로 배포하면 True 로 바꾼다.
app.add_middleware(SessionMiddleware, secret_key=config.SESSION_SECRET or secrets.token_urlsafe(32),
                   session_cookie="dojang_session", max_age=None, same_site="lax", https_only=False)
app.mount("/static", StaticFiles(directory=STATIC), name="static")

LOGIN_MESSAGES = {
    auth.INVALID: "사번 또는 비밀번호가 올바르지 않습니다.",
    auth.INACTIVE: "사용할 수 없는 계정입니다. 관리자에게 문의해 주세요.",
}
LOGIN_STATUS = {auth.INVALID: 401, auth.INACTIVE: 403}


def _session_user(request: Request):
    """세션의 로그인 사용자를 돌려준다. 로그인하지 않았거나 계정이 비활성이면 세션을 비우고 None.
    _SESSION_CACHE_TTL 이내에는 DB를 다시 읽지 않고 세션에 캐싱된 정보를 쓴다."""
    user_id = request.session.get("user_id")
    if not isinstance(user_id, str) or not user_id:
        request.session.clear()
        return None
    expires_at = request.session.get("expires_at")              # 로그인 만료 시각 (로그인·연장 때 정함)
    if not isinstance(expires_at, (int, float)) or expires_at <= time.time():   # 만료 시각이 없거나 지났으면
        request.session.clear()                                 # 세션을 비우고 (원복 상태도 함께 사라짐)
        return None                                             # 로그인 안 한 것으로 본다 → 401
    cached = request.session.get("_user_cache")
    cached_at = request.session.get("_user_cached_at", 0)
    if cached and (time.time() - cached_at) < _SESSION_CACHE_TTL:
        return cached
    user = auth.get_active_user(user_id)
    if user is None:
        request.session.clear()
        return None
    request.session["_user_cache"] = user
    request.session["_user_cached_at"] = time.time()
    return user


def login_required(request: Request):
    user = _session_user(request)
    if user is None:
        raise HTTPException(status_code=401, detail="로그인이 필요합니다.")
    return user


class Login(BaseModel):
    user_id: str = Field(min_length=1, max_length=20)
    password: str = Field(min_length=1, max_length=200)    # 공백도 비밀번호의 일부라 자르지 않는다


class Ask(BaseModel):
    query: str = Field(min_length=1, max_length=500)
    k: int = Field(default=3, ge=1, le=10)
    restore: bool = False
    follow_up_of: str | None = Field(default=None, pattern=r"^req_[0-9]{8}_[0-9a-f]{12}$")   # 직전 재질문의 request_id (보충 질문일 때만)


class VerifyPassword(BaseModel):
    password: str = Field(min_length=1, max_length=200)
    purpose: str = Field(default="reveal", pattern=r"^(reveal|confirm)$")   # reveal: 원복·부분 공개 켜기 / confirm: 비밀번호 확인만 (관리실 레벨 변경 등)
    minutes: int = 30                                                       # 원복 유지 시간 (REVEAL_MINUTES 중 하나, 0 = 로그아웃까지)
    reason: str | None = Field(default=None, max_length=200)               # 복원 사유 (관리실 문서 복원에 필수)


class DocRestoreTokens(BaseModel):
    doc_id: str = Field(min_length=1, max_length=64)                        # 복원할 문서
    tokens: list[str] = Field(min_length=1, max_length=500)                 # 그 문서의 토큰들


class FeedbackBody(BaseModel):
    request_id: str = Field(pattern=r"^req_[0-9]{8}_[0-9a-f]{12}$")
    kind: str = Field(pattern=r"^(good|bad)$")
    comment: str | None = Field(default=None, max_length=500)


class RestoreTokens(BaseModel):
    request_id: str = Field(pattern=r"^req_[0-9]{8}_[0-9a-f]{12}$")   # /api/chat 응답의 request_id. 없거나 형식이 틀리면 422
    tokens: list[str] = Field(min_length=1, max_length=200)


def _ui_state(state):
    return UI_STATE.get(state, "sorry")


def _elapsed_ms(started):
    return int((time.perf_counter() - started) * 1000)


@app.get("/")
def index(request: Request):
    if _session_user(request) is None:
        return RedirectResponse("/login", status_code=303)
    return FileResponse(STATIC / "index.html")


@app.get("/login")
def login_page(request: Request):
    if _session_user(request) is not None:
        return RedirectResponse("/", status_code=303)
    return FileResponse(STATIC / "login.html")


@app.post("/api/login")
def login(body: Login, request: Request):      # 동기 함수: bcrypt 계산과 DB 접근이 블로킹
    try:
        result = auth.authenticate(body.user_id, body.password)
    except Exception as exc:                   # 비밀번호가 들어갈 수 있는 예외 메시지는 내보내지 않는다
        raise HTTPException(status_code=503, detail=f"로그인 처리 중 오류: {type(exc).__name__}") from None
    if not result.ok:
        return JSONResponse(status_code=LOGIN_STATUS[result.reason], content={
            "reason": result.reason, "message": LOGIN_MESSAGES[result.reason]})
    request.session.clear()                    # 이전 세션 값을 버리고 새로 시작
    request.session["expires_at"] = time.time() + SESSION_SECONDS   # 로그인 만료 시각
    request.session["user_id"] = result.user["user_id"]
    request.session["_user_cache"] = result.user
    request.session["_user_cached_at"] = time.time()
    return {"user": result.user}


@app.post("/api/logout")
def logout(request: Request):
    request.session.clear()
    return {"ok": True}


def _session_remaining(request: Request) -> int:
    """로그인이 끝나기까지 남은 초."""
    return max(0, int(request.session.get("expires_at", 0) - time.time()))   # 음수면 0


def _reveal_state(request: Request):
    """지금 켜져 있는 원복·부분 공개 상태. 꺼져 있거나 시간이 지났으면 None (지난 것은 세션에서 지운다).
    반환: {"mode": "full"|"partial", "remaining": 남은 초 또는 None(로그아웃까지), "reason": 사유}"""
    reveal = request.session.get("reveal")                      # verify-password 가 저장한 상태
    if not isinstance(reveal, dict):                            # 켠 적 없으면
        return None
    until = reveal.get("until")                                 # 끝나는 시각 (None = 로그아웃까지)
    if until is not None and until <= time.time():              # 유지 시간이 지났으면
        request.session.pop("reveal", None)                     # 서버에서도 끈다
        return None
    remaining = None if until is None else int(until - time.time())   # 남은 초
    return {"mode": reveal.get("mode"), "remaining": remaining, "reason": reveal.get("reason")}


def _reveal_active(request: Request, mode: str) -> bool:
    """이 세션에서 원복(full) 또는 부분 공개(partial)가 켜져 있는지."""
    state = _reveal_state(request)                              # 시간 지난 상태는 여기서 정리됨
    return bool(state) and state["mode"] == mode                # 모드까지 같아야 허용


@app.get("/api/me")
def me(request: Request, user: dict = Depends(login_required)):
    return {"user": user,
            "session_remaining": _session_remaining(request),  # 화면 타이머가 이 값으로 시작한다 (새로고침해도 이어짐)
            "reveal": _reveal_state(request)}                   # 원복·부분 공개 상태 (새로고침 뒤에도 화면에 그대로 표시)


@app.post("/api/session/extend")
def extend_session(request: Request, user: dict = Depends(login_required)):
    """세션 연장 버튼: 남은 시간을 다시 SESSION_SECONDS 로 채운다. 이미 만료됐으면 login_required 가 401."""
    request.session["expires_at"] = time.time() + SESSION_SECONDS   # 지금부터 다시 30분
    return {"session_remaining": _session_remaining(request)}


@app.post("/api/verify-password")
def verify_password(body: VerifyPassword, request: Request, user: dict = Depends(login_required)):
    """2차 인증.
    purpose=reveal  : Lv2 → 원복(full), Lv1 → 부분 공개(partial)를 minutes 분 동안 켠다 (0 = 로그아웃까지).
                      reason 을 주면 세션에 두고 관리실 문서 복원 기록에 남긴다.
    purpose=confirm : 비밀번호 확인만 한다 (관리실 레벨 변경 등). 원복 상태는 바꾸지 않는다."""
    level = user.get("access_level")
    if body.purpose == "reveal" and level not in (1, 2):       # 원복·부분 공개는 Lv1 이상만
        raise HTTPException(status_code=403, detail="Lv1 이상 권한이 필요합니다.")
    if body.purpose == "reveal" and body.minutes not in REVEAL_MINUTES:   # 정해진 선택지만 허용
        raise HTTPException(status_code=422, detail=f"유지 시간은 {REVEAL_MINUTES} 중 하나여야 합니다.")
    result = auth.authenticate(user["user_id"], body.password)
    if not result.ok:
        raise HTTPException(status_code=401, detail="비밀번호가 올바르지 않습니다.")
    if body.purpose == "confirm":                               # 확인만 하는 경우
        return {"ok": True}
    reason = (body.reason or "").strip() or None                # 빈 사유는 None
    request.session["reveal"] = {                               # 원복 상태를 서버 세션에 둔다 (화면 타이머만으로는 우회 가능하므로)
        "mode": "full" if level == 2 else "partial",            # Lv2 실명 복원 / Lv1 이름 부분 공개
        "until": (time.time() + body.minutes * 60) if body.minutes else None,   # 끝나는 시각 (0 이면 로그아웃까지)
        "reason": reason,                                       # 복원 사유
    }
    return {"ok": True, "reveal": _reveal_state(request)}


@app.post("/api/reveal-off")
def reveal_off(request: Request, user: dict = Depends(login_required)):
    """원복·부분 공개 끄기. 화면 버튼을 끌 때 서버 상태도 같이 지운다 (3-11)."""
    request.session.pop("reveal", None)                         # 없으면 그냥 지나감
    return {"ok": True}


@app.post("/api/restore-tokens")
def restore_tokens(body: RestoreTokens, request: Request, user: dict = Depends(login_required)):
    if user.get("access_level") != 2:
        raise HTTPException(status_code=403, detail="Lv2 권한이 필요합니다.")
    if not _reveal_active(request, "full"):                     # 원복이 꺼져 있거나 유지 시간이 지났으면
        raise HTTPException(status_code=403, detail="2차 인증이 필요합니다.")
    try:
        token_map = _fetch_originals(body.tokens, body.request_id, user["user_id"])
    except psycopg.errors.InsufficientPrivilege as exc:     # DB 함수가 레벨 2 아님으로 거절 (42501)
        _log_restore_error(body.request_id, exc)
        raise HTTPException(status_code=403, detail="복원 권한이 없습니다.") from None
    except Exception as exc:                                # 기록 실패 포함: 기록 없는 복원은 하지 않는다
        _log_restore_error(body.request_id, exc)
        raise HTTPException(status_code=503, detail=f"복원 처리 중 오류: {type(exc).__name__}") from None
    return {"map": token_map}


@app.post("/api/partial-tokens")
def partial_tokens(body: RestoreTokens, request: Request, user: dict = Depends(login_required)):
    if user.get("access_level") != 1:           # Lv1 전용. Lv2 도 쓰게 하려면 < 1 로 (DB 함수·화면 버튼·verify_password 도 함께)
        raise HTTPException(status_code=403, detail="Lv1 전용 기능입니다.")
    if not _reveal_active(request, "partial"):                  # 부분 공개가 꺼져 있거나 유지 시간이 지났으면
        raise HTTPException(status_code=403, detail="2차 인증이 필요합니다.")
    try:
        token_map = _fetch_partials(body.tokens, body.request_id, user["user_id"])
    except psycopg.errors.InsufficientPrivilege as exc:     # Lv1 아님·본인 질문 아님 (42501)
        _log_restore_error(body.request_id, exc)
        raise HTTPException(status_code=403, detail="부분 공개 권한이 없습니다.") from None
    except Exception as exc:                                # 기록 실패 포함: 기록 없는 공개는 하지 않는다
        _log_restore_error(body.request_id, exc)
        raise HTTPException(status_code=503, detail=f"부분 공개 처리 중 오류: {type(exc).__name__}") from None
    return {"map": token_map}


@app.get("/api/health")
def health():
    return {"ok": True, "llm": bool(config.LLM_API_KEY), "embed": config.EMBED_PROVIDER,
            "search_mode": (config.env("SEARCH_MODE", "and") or "and")}


_TOKEN_RE = re.compile(r"<[A-Z_]+_[0-9A-Za-z]+>")


def _restore_all(texts: list[str], token_map: dict[str, str]) -> list[str]:
    """텍스트 목록에서 토큰을 원문으로 치환한다."""
    def _replace(m):
        return token_map.get(m.group(), m.group())
    return [_TOKEN_RE.sub(_replace, t) if t else t for t in texts]


# 복원 대상 토큰 형식. audit.restore_log 의 CHECK(token ~ '^<[A-Z_]+_[0-9a-f]+>$')와 같다.
# 화면 치환용 _TOKEN_RE(대소문자 hex 허용)와 다르며, 형식이 다른 토큰은 DB를 부르기 전에 뺀다.
_RESTORE_TOKEN_RE = re.compile(r"<[A-Z_]+_[0-9a-f]+>")
_RESTORE_SQL = "SELECT t, vault.restore_token(%s, %s, t) FROM unnest(%s::text[]) AS t"


def _fetch_originals(tokens, request_id: str, user_id: str) -> dict[str, str]:
    """vault.restore_token()으로 토큰 → 원문(첫 번째 표기) 매핑을 가져온다.

    DB 함수가 토큰마다 레벨 2 확인 → audit.restore_log 기록 → 원문 배열 반환을 한다.
    vault.token_map 에 없는 토큰은 NULL(기록 없음)이라 결과에서 빠진다.
    한 트랜잭션이라 하나라도 실패하면(권한 없음 42501, 기록 실패 등) 전부 롤백되고 예외가 올라간다.
    """
    valid = sorted({t for t in tokens if _RESTORE_TOKEN_RE.fullmatch(t)})
    if not valid:
        return {}
    with db.restore_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(_RESTORE_SQL, (request_id, user_id, valid))
            rows = cur.fetchall()
        conn.commit()
    return {t: orig[0] for t, orig in rows if orig and orig[0]}


_PARTIAL_TOKEN_RE = re.compile(r"<PERSON_[0-9a-f]+>")      # 부분 공개는 이름 토큰만
_PARTIAL_SQL = "SELECT t, vault.partial_token(%s, %s, t) FROM unnest(%s::text[]) AS t"


def _fetch_partials(tokens, request_id: str, user_id: str) -> dict[str, str]:
    """vault.partial_token()으로 이름 토큰 → 마스킹 값(윤*영) 매핑을 가져온다.

    이름 토큰이 아니면 DB 를 부르기 전에 뺀다 (DB 함수도 person 이 아니면 NULL).
    NULL(이름 아님·답변에 없던 토큰·token_map 에 없음)은 결과에서 빠진다.
    한 트랜잭션이라 하나라도 실패하면(Lv1 아님·본인 질문 아님 42501, 기록 실패 등) 전부 롤백되고 예외가 올라간다.
    """
    valid = sorted({t for t in tokens if _PARTIAL_TOKEN_RE.fullmatch(t)})
    if not valid:
        return {}
    with db.restore_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(_PARTIAL_SQL, (request_id, user_id, valid))
            rows = cur.fetchall()
        conn.commit()
    return {t: masked for t, masked in rows if masked}


def _log_recheck(request_id, rc, latency_ms):
    """질문마다 출력 재검사 결과·시간을 서버 터미널에 한 줄 남긴다 (질문 원문·토큰·원문 값은 남기지 않음)."""
    logging.getLogger("uvicorn.error").info(
        "recheck request_id=%s action=%s labels=%s recheck_ms=%d total_ms=%d",
        request_id, rc["action"], rc["labels"] or "{}", rc["ms"], latency_ms)


def _log_restore_error(request_id, exc):
    """복원 실패 사실만 남긴다 (outputs/server_error.log + 터미널). 예외 메시지·토큰·원문은 남기지 않는다."""
    audit._log_error("실명 복원 실패 request_id=%s error=%s", request_id, type(exc).__name__)


POLICY = load_policy()          # 문서 열람 판정 (policy/access_policy.yaml). 바꾸면 서버 재시작


def _doc_scope(user):
    """검색 권한 범위: 이 사용자가 열람할 수 있는 doc_id 목록. 개인정보 담당자는 None(제한 없음).
    rag.doc_meta 에 없는 문서(메타데이터 미생성)는 볼 수 없다 (fail-closed)."""
    if user.get("is_admin"):
        return None
    with db.search_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT doc_id, doc_type, teams FROM rag.doc_meta")
        rows = cur.fetchall()
    return [doc_id for doc_id, doc_type, teams in rows if POLICY.can_view_doc(user, doc_type, teams)]


@app.post("/api/chat")
def ask(body: Ask, request: Request, user: dict = Depends(login_required)):
    request_id = audit.new_request_id()
    started = time.perf_counter()
    prev = _previous_request(body.follow_up_of, user["user_id"])       # 직전 재질문 (본인 질문일 때만)
    context = _merge_context(prev)                                     # 이어받을 수 있으면 직전 가명 질문·토큰
    try:
        scope = _doc_scope(user)                                       # 권한 범위 (검색 전에 정한다)
        res = chat.answer(body.query.strip(), body.k, scope=scope, **({"context": context} if context else {}))
    except Exception as exc:
        audit.record(audit.build_row(request_id, user, error=exc, latency_ms=_elapsed_ms(started)))
        raise HTTPException(status_code=500, detail=f"처리 중 오류: {type(exc).__name__}") from None
    latency_ms = _elapsed_ms(started)
    follow_up_of = prev["request_id"] if prev else None               # 본인 질문의 id 일 때만 연결 (아니면 None)
    audit.record(audit.build_row(request_id, user, result=res, latency_ms=latency_ms, follow_up_of=follow_up_of))
    if res.get("recheck"):
        _log_recheck(request_id, res["recheck"], latency_ms)
    docs = [{"doc_id": d["doc_id"], "snippet": d["snippet"],      # 출력 재검사를 거친 조각만 (starter/recheck.py)
             "text": d.get("chunk_text", "")} for d in res["docs"]]   # 찾은 조각 전체 (재검사를 거친 것, 문서 팝업용)
    answer_text = res["answer"]
    masked_query = res["masked_query"]
    restored = False

    should_restore = (
        body.restore
        and user.get("access_level") == 2
        and _reveal_active(request, "full")                    # 원복이 켜져 있고 유지 시간 안일 때만
    )
    if should_restore and (answer_text or docs):
        all_text = [answer_text or "", masked_query or ""] + [d["snippet"] for d in docs] + [d["text"] for d in docs]
        tokens_found = set()
        for t in all_text:
            tokens_found.update(_TOKEN_RE.findall(t))
        try:
            token_map = _fetch_originals(tokens_found, request_id, user["user_id"])
        except Exception as exc:         # 복원·기록 실패 → 토큰 그대로 돌려준다 (답변은 막지 않음)
            _log_restore_error(request_id, exc)
            token_map = {}
        if token_map:
            results = _restore_all(all_text, token_map)
            answer_text = results[0] or answer_text
            masked_query = results[1] or masked_query
            for i, d in enumerate(docs):
                d["snippet"] = results[2 + i]
                d["text"] = results[2 + len(docs) + i]          # 조각 전체도 같은 규칙으로 복원
            restored = True

    return {"request_id": request_id, "masked_query": masked_query, "query_tokens": res["query_tokens"],
            "docs": docs, "answer": answer_text, "state": _ui_state(res["state"]),
            "restored": restored,
            "blocked": (res.get("recheck") or {}).get("action") == "blocked",   # 출력 재검사로 차단됨 → 화면이 '못 찾음' 안내를 붙이지 않는다 (3-3)
            "clarify_code": res.get("clarify_code"),            # 재질문 원인 (starter/clarify.py)
            "need_more": bool(res.get("clarify_code")),         # True 면 화면이 '조건 확인 필요'로 표시하고 다음 질문에 이 request_id 를 붙인다
            "merged": bool(res.get("merged"))}                  # True 면 이전 질문과 합쳐서 찾았다는 안내를 보여 준다


@app.post("/api/feedback")
def submit_feedback(body: FeedbackBody, user: dict = Depends(login_required)):
    from starter import db
    with db.rag_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO audit.feedback (request_id, user_id, kind, comment) VALUES (%s,%s,%s,%s)",
            (body.request_id, user["user_id"], body.kind, body.comment),
        )
        conn.commit()
    return {"ok": True}


def _previous_request(request_id, user_id):
    """follow_up_of 로 받은 request_id 의 감사 로그 행 (이 사용자의 질문일 때만). 아니면 None.

    남의 질문 id 를 붙여 기록을 섞거나 남의 질문을 이어받지 못하게 한다. 확인에 실패해도 답변은 막지 않는다.
    반환: {'request_id', 'masked_query', 'query_tokens', 'clarify_code', 'age_min'}
    """
    if not request_id:                                          # 보충 질문이 아니면
        return None                                             # 연결 없음
    try:
        with db.rag_conn() as conn, conn.cursor() as cur:       # 감사 로그와 같은 계정으로 조회
            cur.execute("""SELECT request_id, masked_query, query_tokens, clarify_code,
                                  EXTRACT(EPOCH FROM now() - created_at) / 60
                           FROM audit.query_log WHERE request_id = %s AND user_id = %s""",
                        (request_id, user_id))                  # 같은 사용자의 질문인지 + 몇 분 전인지
            row = cur.fetchone()
    except Exception as exc:                                    # DB 문제로 확인 못 하면
        audit._log_error("follow_up_of 확인 실패 request_id=%s error=%s", request_id, type(exc).__name__)   # 기록만 남기고
        return None                                             # 연결은 생략
    if not row:                                                 # 남의 질문이거나 없는 id
        return None
    return {"request_id": row[0], "masked_query": row[1] or "", "query_tokens": list(row[2] or []),
            "clarify_code": row[3], "age_min": float(row[4] or 0)}


def _merge_context(prev):
    """직전 재질문을 이어받을 수 있으면 chat.answer 에 넘길 context, 아니면 None.
    조건: 이어받을 수 있는 재질문 원인(clarify.MERGE_CODES)이고 MERGE_WINDOW_MIN 분 안."""
    if not prev or prev["clarify_code"] not in clarify.MERGE_CODES:   # 재질문이 아니었거나 이어받을 원인이 아니면
        return None
    if prev["age_min"] > clarify.MERGE_WINDOW_MIN:              # 너무 오래됐으면
        return None
    return {k: prev[k] for k in ("masked_query", "query_tokens", "clarify_code")}   # 가명 질문·토큰·원인만


def admin_required(request: Request):
    user = _session_user(request)
    if user is None:
        raise HTTPException(status_code=401, detail="로그인이 필요합니다.")
    if user.get("access_level") != 2:
        raise HTTPException(status_code=403, detail="Lv2 권한이 필요합니다.")
    return user


@app.get("/admin")
def admin(request: Request):
    user = _session_user(request)
    if user is None:
        return RedirectResponse("/login", status_code=303)
    if user.get("access_level") != 2:
        return RedirectResponse("/", status_code=303)
    return FileResponse(STATIC / "admin.html")


@app.get("/api/doc-count")
def doc_count(user: dict = Depends(login_required)):
    """상담 창 헤더의 '사내 문서 N건'. 이 사용자가 검색할 수 있는 문서 수만 센다 (권한 밖 문서 수가 드러나지 않게)."""
    scope = _doc_scope(user)                                    # 열람 가능한 doc_id 목록 (None = 제한 없음)
    with db.rag_conn() as conn, conn.cursor() as cur:
        if scope is None:                                       # 개인정보 담당자: 전체
            cur.execute("SELECT COUNT(DISTINCT doc_id) FROM rag.chunks")
        else:                                                   # 그 밖에는 권한 범위 안의 문서만
            cur.execute("SELECT COUNT(DISTINCT doc_id) FROM rag.chunks WHERE doc_id = ANY(%s::text[])", (list(scope),))
        return {"count": cur.fetchone()[0]}


@app.get("/api/doc-types")
def doc_types(user: dict = Depends(login_required)):
    """'찾을 수 있는 문서' 안내 패널 (3-4 ①): 이 사용자가 검색할 수 있는 문서 종류와 건수."""
    scope = _doc_scope(user)                                    # 열람 가능한 doc_id 목록
    with db.search_conn() as conn, conn.cursor() as cur:
        if scope is None:                                       # 제한 없음
            cur.execute("SELECT doc_type, COUNT(*) FROM rag.doc_meta GROUP BY doc_type ORDER BY 2 DESC, 1")
        else:                                                   # 권한 범위 안에서만 센다
            cur.execute("""SELECT doc_type, COUNT(*) FROM rag.doc_meta WHERE doc_id = ANY(%s::text[])
                           GROUP BY doc_type ORDER BY 2 DESC, 1""", (list(scope),))
        return {"items": [{"doc_type": t or "미지정", "count": n} for t, n in cur.fetchall()]}   # 문서 종류별 건수


@app.get("/api/admin/stats")
def admin_stats(user: dict = Depends(admin_required)):
    from starter import db
    stats = {}
    with db.admin_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT COUNT(DISTINCT doc_id) FROM rag.chunks")
        stats["total_docs"] = cur.fetchone()[0]
        cur.execute("SELECT COUNT(DISTINCT doc_id) FROM rag.chunks WHERE created_at::date = CURRENT_DATE")
        stats["today_docs"] = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM rag.chunks")
        stats["total_chunks"] = cur.fetchone()[0]
    with db.admin_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM vault.token_map")
        stats["total_tokens"] = cur.fetchone()[0]
        cur.execute("""
            SELECT resolution_status, COUNT(*)
            FROM vault.person_info
            GROUP BY resolution_status
        """)
        review = {row[0]: row[1] for row in cur.fetchall()}
        stats["review_pending"] = review.get("pending", 0)
        stats["review_resolved"] = review.get("resolved", 0)
        stats["review_unresolved"] = review.get("unresolved", 0)
    with db.rag_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT COALESCE(SUM(doc_count), 0) FROM audit.upload_log WHERE status = 'failed'")   # 업로드 실패 작업의 문서 수 (4-2)
        stats["fail_docs"] = int(cur.fetchone()[0])
        cur.execute("""
            WITH doc_tokens AS (
                SELECT doc_id, COUNT(DISTINCT tok) AS total
                FROM rag.chunks, unnest(tokens) AS tok GROUP BY doc_id
            ), reviewed AS (
                SELECT doc_id, COUNT(*) AS cnt
                FROM audit.detection_review GROUP BY doc_id
            )
            SELECT COALESCE(SUM(dt.total - COALESCE(r.cnt, 0)), 0)
            FROM doc_tokens dt LEFT JOIN reviewed r ON r.doc_id = dt.doc_id
        """)
        stats["detection_unreviewed"] = cur.fetchone()[0]
        cur.execute("SELECT kind, COUNT(*) FROM audit.feedback GROUP BY kind")
        fb = {row[0]: row[1] for row in cur.fetchall()}
        stats["feedback_good"] = fb.get("good", 0)
        stats["feedback_bad"] = fb.get("bad", 0)
    return stats


_LABEL_RE = re.compile(r"<([A-Z_]+)_[0-9A-Za-z]+>")
_TOKEN_LABEL_NAMES = {
    "PERSON": "PERSON 이름", "PHONE_NUMBER": "PHONE 전화", "EMAIL": "EMAIL 이메일",
    "ACCOUNT": "ACCOUNT 계좌", "ADDRESS": "ADDRESS 주소", "CARD_NUMBER": "CARD 카드",
}


_DOCS_SORT_COLS = {
    "doc_id": "doc_id",
    "doc_type": "doc_type",
    "created_at": "MIN(created_at)",
    "chunk_count": "COUNT(*)",
}


@app.get("/api/admin/doc-types")
def admin_doc_types(user: dict = Depends(admin_required)):
    from starter import db
    with db.rag_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT DISTINCT doc_type FROM rag.chunks WHERE doc_type IS NOT NULL ORDER BY doc_type")
        types = [r[0] for r in cur.fetchall()]
    return {"doc_types": types}


@app.get("/api/admin/docs")
def admin_docs(
    doc_type: str = Query(None),
    date_from: str = Query(None),
    date_to: str = Query(None),
    sort: str = Query(None),
    order: str = Query(None),
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    user: dict = Depends(admin_required),
):
    from starter import db
    conditions, params = [], {}
    if doc_type:
        conditions.append("doc_type = %(doc_type)s")
        params["doc_type"] = doc_type
    if date_from:
        conditions.append("created_at >= %(date_from)s::date")
        params["date_from"] = date_from
    if date_to:
        conditions.append("created_at < (%(date_to)s::date + 1)")
        params["date_to"] = date_to
    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    sort_col = _DOCS_SORT_COLS.get(sort, "MIN(created_at)")
    sort_dir = "ASC" if order == "asc" else "DESC"
    with db.admin_conn() as conn, conn.cursor() as cur:
        cur.execute(f"SELECT COUNT(DISTINCT doc_id) FROM rag.chunks {where}", params)
        total = cur.fetchone()[0]
        cur.execute(f"""
            SELECT doc_id, doc_type, MIN(created_at)::date, COUNT(*)
              FROM rag.chunks {where}
             GROUP BY doc_id, doc_type
             ORDER BY {sort_col} {sort_dir}
             LIMIT %(size)s OFFSET %(offset)s
        """, {**params, "size": size, "offset": (page - 1) * size})
        docs = [{"doc_id": r[0], "doc_type": r[1] or "미지정",
                 "created_at": str(r[2]), "chunk_count": r[3]} for r in cur.fetchall()]
        cur.execute("SELECT DISTINCT doc_type FROM rag.chunks WHERE doc_type IS NOT NULL ORDER BY doc_type")
        doc_types = [r[0] for r in cur.fetchall()]
    return {"docs": docs, "total": total, "page": page, "size": size, "doc_types": doc_types}


@app.get("/api/admin/docs/{doc_id}")
def admin_doc_detail(doc_id: str, user: dict = Depends(admin_required)):
    from starter import db
    with db.admin_conn() as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT doc_type, chunk_idx, masked_text, tokens
              FROM rag.chunks WHERE doc_id = %s ORDER BY chunk_idx
        """, (doc_id,))
        rows = cur.fetchall()
    if not rows:
        raise HTTPException(status_code=404, detail="문서를 찾을 수 없습니다.")
    doc_type = rows[0][0] or "미지정"
    all_tokens, chunks = set(), []
    for r in rows:
        toks = r[3] or []
        all_tokens.update(toks)
        chunks.append({"chunk_idx": r[1], "masked_text": r[2], "tokens": toks})
    labels = set()
    for tok in all_tokens:
        m = _LABEL_RE.match(tok)
        if m:
            labels.add(_TOKEN_LABEL_NAMES.get(m.group(1), m.group(1)))
    return {"doc_id": doc_id, "doc_type": doc_type,
            "token_labels": sorted(labels), "chunks": chunks}


_DOC_RESTORE_SQL = "SELECT t, vault.restore_doc_token(%s, %s, %s, t, %s) FROM unnest(%s::text[]) AS t"


@app.post("/api/admin/restore-doc-tokens")
def admin_restore_doc_tokens(body: DocRestoreTokens, request: Request, user: dict = Depends(admin_required)):
    """관리실 문서 상세의 원문 복원 (4-1).
    질문이 없는 경로라 vault.restore_token(본인 질문 확인)을 쓸 수 없어 vault.restore_doc_token 을 쓴다.
    DB 함수가 Lv2 확인 → 그 문서의 토큰인지 확인 → restore_log 에 사유·문서 번호와 함께 기록 → 원문 반환을 한다."""
    state = _reveal_state(request)                              # 원복 상태 (유지 시간 확인 포함)
    if not state or state["mode"] != "full":                    # 원복이 꺼져 있으면
        raise HTTPException(status_code=403, detail="2차 인증이 필요합니다.")
    if not state.get("reason"):                                 # 관리실 복원은 사유가 있어야 한다
        raise HTTPException(status_code=400, detail="복원 사유를 입력해 주세요.")
    valid = sorted({t for t in body.tokens if _RESTORE_TOKEN_RE.fullmatch(t)})   # 형식이 맞는 토큰만
    if not valid:
        return {"map": {}}
    request_id = audit.new_request_id()                         # 이번 복원 묶음의 번호 (서버가 만든다)
    try:
        with db.restore_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(_DOC_RESTORE_SQL, (request_id, user["user_id"], body.doc_id, state["reason"], valid))
                rows = cur.fetchall()
            conn.commit()                                       # 기록과 복원을 한 번에 확정
    except psycopg.errors.InsufficientPrivilege as exc:         # Lv2 아님 (42501)
        _log_restore_error(request_id, exc)
        raise HTTPException(status_code=403, detail="복원 권한이 없습니다.") from None
    except Exception as exc:                                    # 기록 실패 포함: 기록 없는 복원은 하지 않는다
        _log_restore_error(request_id, exc)
        raise HTTPException(status_code=503, detail=f"복원 처리 중 오류: {type(exc).__name__}") from None
    return {"map": {t: orig[0] for t, orig in rows if orig and orig[0]}}   # 토큰 → 원문(첫 표기)


@app.get("/api/admin/review")
def admin_review_list(
    q: str = Query(""),
    status: str = Query(None),
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    user: dict = Depends(admin_required),
):
    from starter import db
    conditions, params = [], {}
    if q.strip():
        conditions.append("(t.normalized ILIKE %(q)s OR %(q_raw)s = ANY(p.doc_id))")
        params["q"] = f"%{q.strip()}%"
        params["q_raw"] = q.strip()
    if status:
        conditions.append("p.resolution_status = %(status)s")
        params["status"] = status
    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    with db.vault_conn() as conn, conn.cursor() as cur:
        cur.execute(f"""
            SELECT COUNT(*) FROM vault.person_info p
            LEFT JOIN vault.token_map t ON p.token = t.token
            {where}
        """, params)
        total = cur.fetchone()[0]
        cur.execute(f"""
            SELECT p.person_id, p.token, t.normalized, p.role, p.doc_id,
                   p.resolution_status, p.info_label, p.evidence, p.reg_id
              FROM vault.person_info p
              LEFT JOIN vault.token_map t ON p.token = t.token
              {where}
             ORDER BY CASE p.resolution_status
                        WHEN 'pending' THEN 0
                        WHEN 'unresolved' THEN 1
                        ELSE 2 END,
                      p.created_at DESC NULLS LAST
             LIMIT %(size)s OFFSET %(offset)s
        """, {**params, "size": size, "offset": (page - 1) * size})
        rows = cur.fetchall()
        clue_tokens = set()
        for r in rows:
            ev = r[7] or {}
            for c in ev.get("clues", []):
                clue_tokens.add(c["token"])
            for tok in (r[6] or []):
                clue_tokens.add(tok)
        token_labels = {}
        if clue_tokens:
            cur.execute(
                "SELECT token, label, normalized FROM vault.token_map WHERE token = ANY(%s)",
                (list(clue_tokens),),
            )
            token_labels = {row[0]: {"label": row[1], "normalized": row[2]} for row in cur.fetchall()}
    items = []
    for r in rows:
        ev = r[7] or {}
        clues_resolved = []
        for c in ev.get("clues", []):
            info = token_labels.get(c["token"], {})
            clues_resolved.append({
                "label": info.get("label", ""),
                "normalized": info.get("normalized", ""),
            })
        info_resolved = []
        for tok in (r[6] or []):
            info = token_labels.get(tok, {})
            info_resolved.append({
                "token": tok,
                "label": info.get("label", ""),
                "normalized": info.get("normalized", ""),
            })
        items.append({
            "person_id": r[0], "token": r[1], "normalized": r[2] or "",
            "role": r[3] or "", "doc_ids": r[4] or [],
            "status": r[5], "reg_id": r[8] or "",
            "evidence": {
                "level": ev.get("level", ""),
                "rule": ev.get("rule"),
                "dept": ev.get("dept"),
                "matched": ev.get("matched", []),
                "mid_matched": ev.get("mid_matched", {}),
                "chunk_ids": ev.get("chunk_ids", []),
            },
            "clues": clues_resolved,
            "info_label": info_resolved,
        })
    return {"items": items, "total": total, "page": page, "size": size}


class _ReviewStatusBody(BaseModel):
    status: str


@app.patch("/api/admin/review/{person_id}")
def admin_review_update(person_id: str, body: _ReviewStatusBody, user: dict = Depends(admin_required)):
    import uuid as _uuid
    if body.status not in ("resolved", "unresolved"):
        raise HTTPException(status_code=400, detail="status는 resolved 또는 unresolved만 가능합니다.")
    from starter import db
    import json as _json
    with db.vault_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT reg_id, resolution_status FROM vault.person_info WHERE person_id = %s", (person_id,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="인물 정보를 찾을 수 없습니다.")
        old_reg_id, old_status = row
        reg_id = old_reg_id
        if body.status == "resolved" and not old_reg_id:
            reg_id = f"reg_{_uuid.uuid4()}"
            cur.execute(
                "INSERT INTO vault.person_registry (reg_id, reason) VALUES (%s, %s::jsonb)",
                (reg_id, _json.dumps({"method": "manual"})),
            )
        if body.status == "unresolved" and old_reg_id:
            cur.execute(
                "SELECT COUNT(*) FROM vault.person_info WHERE reg_id = %s AND person_id != %s",
                (old_reg_id, person_id),
            )
            other_count = cur.fetchone()[0]
            reg_id = None
            if other_count == 0:
                cur.execute("DELETE FROM vault.person_registry WHERE reg_id = %s", (old_reg_id,))
        cur.execute(
            "UPDATE vault.person_info SET resolution_status = %s, reg_id = %s WHERE person_id = %s",
            (body.status, reg_id, person_id),
        )
        conn.commit()
    return {"ok": True, "person_id": person_id, "status": body.status, "reg_id": reg_id or ""}


@app.get("/api/admin/tokens")
def admin_token_list(
    q: str = Query(""),
    label: str = Query(""),
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    user: dict = Depends(admin_required),
):
    from starter import db
    conditions, params = [], {}
    if q.strip():
        conditions.append("(t.token ILIKE %(q)s OR t.normalized ILIKE %(q)s)")
        params["q"] = f"%{q.strip()}%"
    if label.strip():
        conditions.append("t.label = %(label)s")
        params["label"] = label.strip()
    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    with db.vault_conn() as conn, conn.cursor() as cur:
        cur.execute(f"SELECT COUNT(*) FROM vault.token_map t {where}", params)
        total = cur.fetchone()[0]
        cur.execute(f"""
            SELECT t.token, t.label, t.normalized, t.originals, t.created_at::date
              FROM vault.token_map t
              {where}
             ORDER BY t.created_at DESC NULLS LAST
             LIMIT %(size)s OFFSET %(offset)s
        """, {**params, "size": size, "offset": (page - 1) * size})
        rows = cur.fetchall()
    items = []
    for r in rows:
        items.append({
            "token": r[0], "label": r[1], "normalized": r[2],
            "originals": r[3] or [], "created_at": str(r[4]) if r[4] else "",
        })
    return {"items": items, "total": total, "page": page, "size": size}


@app.get("/api/admin/persons")
def admin_person_list(
    q: str = Query(""),
    status: str = Query(""),
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    user: dict = Depends(admin_required),
):
    from starter import db
    conditions, params = [], {}
    if q.strip():
        conditions.append("(p.person_id ILIKE %(q)s OR t.normalized ILIKE %(q)s)")
        params["q"] = f"%{q.strip()}%"
    if status.strip():
        if status == "pending":
            conditions.append("p.resolution_status IN ('pending', 'unresolved')")
        else:
            conditions.append("p.resolution_status = %(status)s")
            params["status"] = status.strip()
    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    with db.vault_conn() as conn, conn.cursor() as cur:
        cur.execute(f"""
            SELECT COUNT(*) FROM vault.person_info p
            LEFT JOIN vault.token_map t ON p.token = t.token
            {where}
        """, params)
        total = cur.fetchone()[0]
        cur.execute(f"""
            SELECT p.person_id, p.token, t.normalized, p.resolution_status,
                   p.doc_id, p.role, p.created_at::date
              FROM vault.person_info p
              LEFT JOIN vault.token_map t ON p.token = t.token
              {where}
             ORDER BY p.created_at DESC NULLS LAST
             LIMIT %(size)s OFFSET %(offset)s
        """, {**params, "size": size, "offset": (page - 1) * size})
        rows = cur.fetchall()
    items = []
    for r in rows:
        items.append({
            "person_id": r[0], "token": r[1], "normalized": r[2] or "",
            "status": r[3], "doc_ids": r[4] or [], "role": r[5] or "",
            "created_at": str(r[6]) if r[6] else "",
        })
    return {"items": items, "total": total, "page": page, "size": size}


# --- 인물 병합 ---

class _PersonMergeBody(BaseModel):
    target_person_id: str
    source_person_ids: list[str]
    note: str = None


@app.get("/api/admin/persons/merge-preview")
def admin_merge_preview(
    ids: str = Query(..., description="comma-separated person_ids"),
    user: dict = Depends(admin_required),
):
    pid_list = [p.strip() for p in ids.split(",") if p.strip()]
    if len(pid_list) < 2:
        raise HTTPException(status_code=400, detail="2건 이상의 person_id가 필요합니다.")
    from starter import db
    placeholders = ",".join(["%s"] * len(pid_list))
    with db.vault_conn() as conn, conn.cursor() as cur:
        cur.execute(f"""
            SELECT p.person_id, p.token, t.normalized, p.resolution_status,
                   p.doc_id, p.role, p.reg_id, p.info_label
              FROM vault.person_info p
              LEFT JOIN vault.token_map t ON p.token = t.token
             WHERE p.person_id IN ({placeholders})
             ORDER BY p.created_at
        """, pid_list)
        rows = cur.fetchall()
    items = []
    for r in rows:
        items.append({
            "person_id": r[0], "token": r[1], "normalized": r[2] or "",
            "status": r[3], "doc_ids": r[4] or [], "role": r[5] or "",
            "reg_id": r[6] or "", "info_label": r[7] or [],
        })
    return {"items": items}


@app.post("/api/admin/persons/merge")
def admin_person_merge(body: _PersonMergeBody, user: dict = Depends(admin_required)):
    import uuid as _uuid
    import json as _json
    if not body.source_person_ids:
        raise HTTPException(status_code=400, detail="source_person_ids가 비어 있습니다.")
    if body.target_person_id in body.source_person_ids:
        raise HTTPException(status_code=400, detail="target이 source에 포함될 수 없습니다.")
    from starter import db

    with db.vault_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT reg_id, resolution_status FROM vault.person_info WHERE person_id = %s",
                    (body.target_person_id,))
        target = cur.fetchone()
        if not target:
            raise HTTPException(status_code=404, detail="대상 인물을 찾을 수 없습니다.")
        reg_id = target[0]
        if not reg_id:
            reg_id = f"reg_{_uuid.uuid4()}"
            cur.execute(
                "INSERT INTO vault.person_registry (reg_id, reason) VALUES (%s, %s::jsonb)",
                (reg_id, _json.dumps({"method": "merge", "reviewer": user["user_id"]})),
            )
            cur.execute(
                "UPDATE vault.person_info SET reg_id = %s, resolution_status = 'resolved' WHERE person_id = %s",
                (reg_id, body.target_person_id),
            )

        placeholders = ",".join(["%s"] * len(body.source_person_ids))
        cur.execute(
            f"UPDATE vault.person_info SET reg_id = %s, resolution_status = 'resolved' WHERE person_id IN ({placeholders})",
            [reg_id] + body.source_person_ids,
        )
        updated = cur.rowcount
        conn.commit()

    with db.rag_conn() as conn, conn.cursor() as cur:
        placeholders = ",".join(["%s"] * len(body.source_person_ids))
        cur.execute(
            f"UPDATE rag.look_up SET reg_id = %s WHERE person_id IN ({placeholders})",
            [reg_id] + body.source_person_ids,
        )
        cur.execute(
            "UPDATE rag.look_up SET reg_id = %s WHERE person_id = %s AND reg_id IS DISTINCT FROM %s",
            (reg_id, body.target_person_id, reg_id),
        )
        cur.execute(
            "INSERT INTO audit.person_merge_log (reviewer, target_reg_id, target_person_id, source_person_ids, note) VALUES (%s,%s,%s,%s,%s)",
            (user["user_id"], reg_id, body.target_person_id, body.source_person_ids, body.note),
        )
        conn.commit()

    return {"ok": True, "reg_id": reg_id, "merged_count": updated}


# --- 인물 분리 ---

class _PersonSplitBody(BaseModel):
    reg_id: str
    detach_person_ids: list[str]
    note: str = None


@app.get("/api/admin/persons/split-preview")
def admin_split_preview(
    person_id: str = Query(...),
    user: dict = Depends(admin_required),
):
    from starter import db
    with db.vault_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT reg_id FROM vault.person_info WHERE person_id = %s", (person_id,))
        row = cur.fetchone()
        if not row or not row[0]:
            raise HTTPException(status_code=400, detail="확정(reg_id 연결) 상태가 아닙니다.")
        reg_id = row[0]
        cur.execute("""
            SELECT p.person_id, p.token, t.normalized, p.resolution_status,
                   p.doc_id, p.role, p.reg_id
              FROM vault.person_info p
              LEFT JOIN vault.token_map t ON p.token = t.token
             WHERE p.reg_id = %s
             ORDER BY p.created_at
        """, (reg_id,))
        rows = cur.fetchall()
    if len(rows) < 2:
        raise HTTPException(status_code=400, detail="이 reg_id에 속한 인물이 1건뿐이라 분리할 수 없습니다.")
    items = []
    for r in rows:
        items.append({
            "person_id": r[0], "token": r[1], "normalized": r[2] or "",
            "status": r[3], "doc_ids": r[4] or [], "role": r[5] or "",
            "reg_id": r[6] or "",
        })
    return {"reg_id": reg_id, "items": items}


@app.post("/api/admin/persons/split")
def admin_person_split(body: _PersonSplitBody, user: dict = Depends(admin_required)):
    if not body.detach_person_ids:
        raise HTTPException(status_code=400, detail="detach_person_ids가 비어 있습니다.")
    from starter import db

    with db.vault_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM vault.person_info WHERE reg_id = %s", (body.reg_id,))
        total = cur.fetchone()[0]
        if len(body.detach_person_ids) >= total:
            raise HTTPException(status_code=400, detail="전부 분리할 수 없습니다. 최소 1건은 남겨야 합니다.")

        placeholders = ",".join(["%s"] * len(body.detach_person_ids))
        cur.execute(
            f"UPDATE vault.person_info SET reg_id = NULL, resolution_status = 'unresolved' WHERE person_id IN ({placeholders}) AND reg_id = %s",
            body.detach_person_ids + [body.reg_id],
        )
        detached = cur.rowcount

        cur.execute("SELECT COUNT(*) FROM vault.person_info WHERE reg_id = %s", (body.reg_id,))
        remaining = cur.fetchone()[0]
        if remaining == 0:
            cur.execute("DELETE FROM vault.person_registry WHERE reg_id = %s", (body.reg_id,))
        conn.commit()

    with db.rag_conn() as conn, conn.cursor() as cur:
        placeholders = ",".join(["%s"] * len(body.detach_person_ids))
        cur.execute(
            f"UPDATE rag.look_up SET reg_id = NULL WHERE person_id IN ({placeholders})",
            body.detach_person_ids,
        )
        cur.execute(
            "INSERT INTO audit.person_split_log (reviewer, source_reg_id, detach_person_ids, note) VALUES (%s,%s,%s,%s)",
            (user["user_id"], body.reg_id, body.detach_person_ids, body.note),
        )
        conn.commit()

    return {"ok": True, "detached_count": detached}


# --- 탐지 검수 ---

class _DetectionVerdictBody(BaseModel):
    doc_id: str
    token: str
    verdict: str
    new_label: str = None
    note: str = None


@app.get("/api/admin/detection-review")
def admin_detection_review_list(
    q: str = Query(""),
    status: str = Query(""),
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    user: dict = Depends(admin_required),
):
    from starter import db
    params: dict = {"size": size, "offset": (page - 1) * size}
    q_cond = ""
    if q.strip():
        q_cond = "WHERE c.doc_id ILIKE %(q)s"
        params["q"] = f"%{q.strip()}%"
    status_cond = ""
    if status == "unreviewed":
        status_cond = "WHERE dt.total_tokens > COALESCE(rc.reviewed_count, 0)"
    elif status == "reviewed":
        status_cond = "WHERE dt.total_tokens = COALESCE(rc.reviewed_count, 0) AND dt.total_tokens > 0"
    base_cte = f"""
        WITH doc_tokens AS (
            SELECT c.doc_id,
                   COUNT(DISTINCT tok) AS total_tokens,
                   MIN(c.doc_type) AS doc_type,
                   MIN(c.created_at)::date AS created_at
            FROM rag.chunks c, unnest(c.tokens) AS tok
            {q_cond}
            GROUP BY c.doc_id
        ),
        review_counts AS (
            SELECT doc_id, COUNT(*) AS reviewed_count
            FROM audit.detection_review GROUP BY doc_id
        )
    """
    with db.rag_conn() as conn, conn.cursor() as cur:
        cur.execute(f"""
            {base_cte}
            SELECT COUNT(*) FROM doc_tokens dt
            LEFT JOIN review_counts rc ON rc.doc_id = dt.doc_id
            {status_cond}
        """, params)
        total = cur.fetchone()[0]
        cur.execute(f"""
            {base_cte}
            SELECT dt.doc_id, dt.doc_type, dt.created_at,
                   dt.total_tokens, COALESCE(rc.reviewed_count, 0)
            FROM doc_tokens dt
            LEFT JOIN review_counts rc ON rc.doc_id = dt.doc_id
            {status_cond}
            ORDER BY (dt.total_tokens - COALESCE(rc.reviewed_count, 0)) DESC, dt.doc_id
            LIMIT %(size)s OFFSET %(offset)s
        """, params)
        docs = [{
            "doc_id": r[0], "doc_type": r[1] or "미지정",
            "created_at": str(r[2]) if r[2] else "",
            "total_tokens": r[3], "reviewed_count": r[4],
        } for r in cur.fetchall()]
    return {"docs": docs, "total": total, "page": page, "size": size}


@app.get("/api/admin/detection-review/{doc_id}")
def admin_detection_review_detail(doc_id: str, user: dict = Depends(admin_required)):
    from starter import db
    with db.rag_conn() as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT chunk_id, chunk_idx, masked_text, tokens, doc_type
            FROM rag.chunks WHERE doc_id = %s ORDER BY chunk_idx
        """, (doc_id,))
        rows = cur.fetchall()
    if not rows:
        raise HTTPException(status_code=404, detail="문서를 찾을 수 없습니다.")
    doc_type = rows[0][4] or "미지정"
    chunks, all_tokens = [], set()
    for r in rows:
        toks = r[3] or []
        all_tokens.update(toks)
        chunks.append({"chunk_id": r[0], "chunk_idx": r[1], "masked_text": r[2], "tokens": toks})
    token_info = {}
    if all_tokens:
        with db.vault_conn() as conn, conn.cursor() as cur:
            cur.execute("SELECT token, label, normalized FROM vault.token_map WHERE token = ANY(%s)",
                        (list(all_tokens),))
            for tok, label, norm in cur.fetchall():
                token_info[tok] = {"label": label, "normalized": norm}
    verdicts = {}
    if all_tokens:
        with db.rag_conn() as conn, conn.cursor() as cur:
            cur.execute("SELECT token, verdict, new_label, reviewer, created_at FROM audit.detection_review WHERE doc_id = %s",
                        (doc_id,))
            for tok, v, nl, rev, ca in cur.fetchall():
                verdicts[tok] = {"verdict": v, "new_label": nl, "reviewer": rev, "reviewed_at": str(ca)}
    tokens = []
    for tok in sorted(all_tokens):
        info = token_info.get(tok, {})
        vd = verdicts.get(tok)
        tokens.append({
            "token": tok, "label": info.get("label", "unknown"),
            "normalized": info.get("normalized", ""),
            "verdict": vd["verdict"] if vd else None,
            "new_label": vd.get("new_label") if vd else None,
            "reviewer": vd.get("reviewer") if vd else None,
            "reviewed_at": vd.get("reviewed_at") if vd else None,
        })
    return {"doc_id": doc_id, "doc_type": doc_type, "chunks": chunks,
            "tokens": tokens, "total_tokens": len(all_tokens), "reviewed_count": len(verdicts)}


@app.patch("/api/admin/detection-review/verdict")
def admin_detection_review_verdict(body: _DetectionVerdictBody, user: dict = Depends(admin_required)):
    if body.verdict not in ("confirmed", "rejected", "relabeled"):
        raise HTTPException(400, "verdict는 confirmed, rejected, relabeled만 가능합니다.")
    if body.verdict == "relabeled" and not body.new_label:
        raise HTTPException(400, "relabeled 판정에는 new_label이 필요합니다.")
    if body.verdict != "relabeled":
        body.new_label = None
    from starter import db
    with db.rag_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT 1 FROM rag.chunks WHERE doc_id = %s AND %s = ANY(tokens) LIMIT 1",
                    (body.doc_id, body.token))
        if not cur.fetchone():
            raise HTTPException(404, "해당 문서에 토큰이 없습니다.")
        cur.execute("""
            INSERT INTO audit.detection_review (doc_id, token, verdict, new_label, reviewer, note)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (doc_id, token) DO UPDATE
            SET verdict = EXCLUDED.verdict, new_label = EXCLUDED.new_label,
                reviewer = EXCLUDED.reviewer, note = EXCLUDED.note, created_at = now()
        """, (body.doc_id, body.token, body.verdict, body.new_label, user["user_id"], body.note))
        conn.commit()
    return {"ok": True, "doc_id": body.doc_id, "token": body.token, "verdict": body.verdict}


@app.get("/api/admin/logs/query")
def admin_logs_query(
    user_id: str = Query(None),
    state: str = Query(None),
    date_from: str = Query(None),
    date_to: str = Query(None),
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    user: dict = Depends(admin_required),
):
    from starter import db
    conditions, params = [], {}
    if user_id:
        conditions.append("user_id = %(user_id)s")
        params["user_id"] = user_id
    if state:
        conditions.append("state = %(state)s")
        params["state"] = state
    if date_from:
        conditions.append("created_at >= %(date_from)s::date")
        params["date_from"] = date_from
    if date_to:
        conditions.append("created_at < (%(date_to)s::date + 1)")
        params["date_to"] = date_to
    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    with db.admin_conn() as conn, conn.cursor() as cur:
        cur.execute(f"SELECT COUNT(*) FROM audit.query_log {where}", params)
        total = cur.fetchone()[0]
        cur.execute(f"""
            SELECT request_id, user_id, level, masked_query, query_tokens,
                   returned_doc_ids, state, latency_ms, created_at
              FROM audit.query_log {where}
             ORDER BY created_at DESC
             LIMIT %(size)s OFFSET %(offset)s
        """, {**params, "size": size, "offset": (page - 1) * size})
        rows = [{"request_id": r[0], "user_id": r[1], "level": r[2],
                 "masked_query": r[3], "query_tokens": r[4] or [],
                 "returned_doc_ids": r[5] or [], "state": r[6],
                 "latency_ms": r[7], "created_at": r[8].isoformat() if r[8] else ""}
                for r in cur.fetchall()]
    return {"items": rows, "total": total, "page": page, "size": size}


@app.get("/api/admin/logs/restore")
def admin_logs_restore(
    user_id: str = Query(None),
    date_from: str = Query(None),
    date_to: str = Query(None),
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    user: dict = Depends(admin_required),
):
    from starter import db
    conditions, params = [], {}
    if user_id:
        conditions.append("user_id = %(user_id)s")
        params["user_id"] = user_id
    if date_from:
        conditions.append("created_at >= %(date_from)s::date")
        params["date_from"] = date_from
    if date_to:
        conditions.append("created_at < (%(date_to)s::date + 1)")
        params["date_to"] = date_to
    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    with db.admin_conn() as conn, conn.cursor() as cur:
        cur.execute(f"SELECT COUNT(*) FROM audit.restore_log {where}", params)
        total = cur.fetchone()[0]
        cur.execute(f"""
            SELECT request_id, user_id, level, token, label, mode, created_at, reason, doc_id
              FROM audit.restore_log {where}
             ORDER BY created_at DESC
             LIMIT %(size)s OFFSET %(offset)s
        """, {**params, "size": size, "offset": (page - 1) * size})
        rows = [{"request_id": r[0], "user_id": r[1], "level": r[2],
                 "token": r[3], "label": r[4], "mode": r[5],
                 "created_at": r[6].isoformat() if r[6] else "",
                 "reason": r[7], "doc_id": r[8]}                 # 복원 사유·문서 번호 (관리실 문서 복원만 채워짐)
                for r in cur.fetchall()]
    return {"items": rows, "total": total, "page": page, "size": size}


@app.get("/api/admin/logs/policy")
def admin_logs_policy(
    date_from: str = Query(None),
    date_to: str = Query(None),
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    user: dict = Depends(admin_required),
):
    from starter import db
    conditions, params = [], {}
    if date_from:
        conditions.append("created_at >= %(date_from)s::date")
        params["date_from"] = date_from
    if date_to:
        conditions.append("created_at < (%(date_to)s::date + 1)")
        params["date_to"] = date_to
    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    with db.admin_conn() as conn, conn.cursor() as cur:
        cur.execute(f"SELECT COUNT(*) FROM audit.policy_change_log {where}", params)
        total = cur.fetchone()[0]
        cur.execute(f"""
            SELECT user_id, entity, action, old_value, new_value,
                   policy_version, created_at
              FROM audit.policy_change_log {where}
             ORDER BY created_at DESC
             LIMIT %(size)s OFFSET %(offset)s
        """, {**params, "size": size, "offset": (page - 1) * size})
        rows = [{"user_id": r[0], "entity": r[1], "action": r[2],
                 "old_value": r[3], "new_value": r[4],
                 "policy_version": r[5],
                 "created_at": r[6].isoformat() if r[6] else ""}
                for r in cur.fetchall()]
    return {"items": rows, "total": total, "page": page, "size": size}


@app.get("/api/admin/feedback")
def admin_feedback_list(
    kind: str = Query(None),
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    user: dict = Depends(admin_required),
):
    from starter import db
    conditions, params = [], {}
    if kind and kind in ("good", "bad"):
        conditions.append("f.kind = %(kind)s")
        params["kind"] = kind
    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    with db.rag_conn() as conn, conn.cursor() as cur:
        cur.execute(f"SELECT COUNT(*) FROM audit.feedback f {where}", params)
        total = cur.fetchone()[0]
        cur.execute(f"""
            SELECT f.request_id, f.user_id, f.kind, f.comment, f.created_at,
                   q.masked_query
              FROM audit.feedback f
              LEFT JOIN audit.query_log q ON f.request_id = q.request_id
              {where}
             ORDER BY f.created_at DESC
             LIMIT %(size)s OFFSET %(offset)s
        """, {**params, "size": size, "offset": (page - 1) * size})
        rows = [{"request_id": r[0], "user_id": r[1], "kind": r[2],
                 "comment": r[3] or "", "created_at": r[4].isoformat() if r[4] else "",
                 "masked_query": r[5] or ""}
                for r in cur.fetchall()]
    return {"items": rows, "total": total, "page": page, "size": size}


@app.get("/api/admin/users")
def admin_users(
    team_cd: str = Query(None),
    level: int = Query(None, ge=0, le=2),
    active: str = Query(None),
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    user: dict = Depends(admin_required),
):
    from starter import db
    conditions, params = [], {}
    if team_cd:
        conditions.append("u.team_cd = %(team_cd)s")
        params["team_cd"] = team_cd
    if level is not None:
        conditions.append("u.access_level = %(level)s")
        params["level"] = level
    if active == "true":
        conditions.append("u.is_active")
    elif active == "false":
        conditions.append("NOT u.is_active")
    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    with db.admin_conn() as conn, conn.cursor() as cur:
        cur.execute(f"SELECT COUNT(*) FROM policy.users u {where}", params)
        total = cur.fetchone()[0]
        cur.execute(f"""
            SELECT u.user_id, u.user_name, u.team_cd, t.team_name,
                   u.position, u.grade, u.access_level, u.is_admin, u.is_active
              FROM policy.users u
              JOIN policy.teams t USING (team_cd)
              {where}
             ORDER BY u.access_level DESC, u.user_id
             LIMIT %(size)s OFFSET %(offset)s
        """, {**params, "size": size, "offset": (page - 1) * size})
        items = [{"user_id": r[0], "user_name": r[1], "team_cd": r[2],
                  "team_name": r[3], "position": r[4], "grade": r[5],
                  "access_level": r[6], "is_admin": r[7], "is_active": r[8]}
                 for r in cur.fetchall()]
        cur.execute("SELECT DISTINCT team_cd, team_name FROM policy.teams ORDER BY team_cd")
        teams = [{"team_cd": r[0], "team_name": r[1]} for r in cur.fetchall()]
    return {"items": items, "total": total, "page": page, "size": size, "teams": teams}


class _UserUpdateBody(BaseModel):
    access_level: int | None = None
    is_active: bool | None = None


@app.patch("/api/admin/users/{user_id}")
def admin_user_update(user_id: str, body: _UserUpdateBody, user: dict = Depends(admin_required)):
    from starter import db
    sets, params = [], {"uid": user_id}
    if body.access_level is not None:
        if body.access_level not in (0, 1, 2):
            raise HTTPException(status_code=400, detail="access_level은 0, 1, 2만 가능합니다.")
        sets.append("access_level = %(level)s")
        params["level"] = body.access_level
    if body.is_active is not None:
        sets.append("is_active = %(active)s")
        params["active"] = body.is_active
    if not sets:
        raise HTTPException(status_code=400, detail="변경할 항목이 없습니다.")
    with db.admin_conn() as conn, conn.cursor() as cur:
        cur.execute(
            f"UPDATE policy.users SET {', '.join(sets)} WHERE user_id = %(uid)s RETURNING user_id",
            params,
        )
        if not cur.fetchone():
            raise HTTPException(status_code=404, detail="사용자를 찾을 수 없습니다.")
        conn.commit()
    return {"ok": True, "user_id": user_id}


# ═══════════════════════════════════════════════════════════════
# 문서 업로드 → data/upload_YYYYMMDD_HHmmss.jsonl 저장 → ingest 실행
# ═══════════════════════════════════════════════════════════════

import json as _json
import subprocess
import threading
from datetime import datetime

_upload_jobs: dict = {}
DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "uploads"


def _run_ingest_subprocess(job_id: str, data_path: str):
    job = _upload_jobs[job_id]
    try:
        job["current_step"] = "ingest 실행 중"
        venv_python = Path(__file__).resolve().parent.parent / ".venv" / "Scripts" / "python.exe"
        if not venv_python.exists():
            venv_python = Path(__file__).resolve().parent.parent / ".venv" / "bin" / "python"
        proc = subprocess.Popen(
            [str(venv_python), "-m", "starter.ingest", "--data", data_path, "--source", "upload"],
            cwd=str(Path(__file__).resolve().parent.parent),
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace",
        )
        output_lines = []
        for line in proc.stdout:
            line = line.rstrip()
            output_lines.append(line)
            job["current_step"] = line[-80:] if len(line) > 80 else line
        proc.wait()
        job["output"] = "\n".join(output_lines[-50:])
        if proc.returncode == 0:
            job["status"] = "done"
            job["current_step"] = "적재 완료"
        else:
            job["status"] = "failed"
            job["current_step"] = f"적재 실패 (exit {proc.returncode})"
    except Exception as exc:
        logging.exception("업로드 ingest 실패: %s", job_id)
        job["status"] = "failed"
        job["current_step"] = f"오류: {exc}"
    _update_upload_log(job_id, job["status"], job.get("current_step", ""))


def _update_upload_log(job_id: str, status: str, error_summary: str):
    try:
        with db.rag_conn() as conn, conn.cursor() as cur:
            cur.execute("""
                UPDATE audit.upload_log
                SET status = %s, finished_at = now(),
                    error_summary = CASE WHEN %s = 'failed' THEN %s ELSE NULL END
                WHERE job_id = %s
            """, (status, status, error_summary, job_id))
            conn.commit()
    except Exception:
        logging.exception("업로드 감사 로그 갱신 실패 (job_id=%s)", job_id)


@app.post("/api/admin/upload")
async def admin_upload(
    files: list[UploadFile] = File(...),
    doc_types: str = Form(""),
    doc_ids_str: str = Form(""),
    user: dict = Depends(admin_required),
):
    type_list = [t.strip() for t in doc_types.split(",") if t.strip()]
    doc_ids = [d.strip() for d in (doc_ids_str or "").split(",")]
    documents: list[dict] = []
    errors: list[str] = []

    for i, f in enumerate(files):
        doc_type = type_list[i] if i < len(type_list) else ""
        doc_id = doc_ids[i] if i < len(doc_ids) else ""
        fname = f.filename or f"unknown_{i}"
        ext = Path(fname).suffix.lower()
        if ext not in (".txt", ".json", ".jsonl"):
            errors.append(f"{fname}: 지원하지 않는 형식 ({ext})")
            continue
        raw = await f.read()
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            errors.append(f"{fname}: UTF-8로 읽을 수 없습니다")
            continue

        if ext == ".txt":
            if not doc_id:
                errors.append(f"{fname}: id가 필요합니다")
            if not doc_type:
                errors.append(f"{fname}: doc_type이 필요합니다")
            if not text.strip():
                errors.append(f"{fname}: 파일 내용이 비어 있습니다")
            if doc_id and doc_type and text.strip():
                documents.append({"id": doc_id, "text": text, "doc_type": doc_type})
        else:
            for ln, line in enumerate(text.strip().splitlines(), 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = _json.loads(line)
                except _json.JSONDecodeError:
                    errors.append(f"{fname}:{ln}: JSON 파싱 실패")
                    continue
                missing = [k for k in ("id", "text", "doc_type") if not obj.get(k)]
                if missing:
                    errors.append(f"{fname}:{ln}: {', '.join(missing)} 누락")
                    continue
                documents.append({
                    "id": obj["id"], "text": obj["text"],
                    "doc_type": obj["doc_type"],
                })

    if not documents:
        detail = "처리할 문서가 없습니다."
        if errors:
            detail += f" ({'; '.join(errors)})"
        raise HTTPException(status_code=400, detail=detail)

    doc_id_list = [d["id"] for d in documents]
    seen = set()
    dup_internal = set()
    for did in doc_id_list:
        if did in seen:
            dup_internal.add(did)
        seen.add(did)

    from starter import db
    with db.rag_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT DISTINCT doc_id FROM rag.chunks WHERE doc_id = ANY(%s)",
            (doc_id_list,),
        )
        dup_db = {row[0] for row in cur.fetchall()}

    skip_ids = dup_internal | dup_db
    if skip_ids:
        for did in dup_internal:
            errors.append(f"{did}: 파일 내 중복 ID (제외)")
        for did in dup_db:
            errors.append(f"{did}: 이미 적재된 문서 ID (제외)")
        documents = [d for d in documents if d["id"] not in skip_ids]

    if not documents:
        detail = "중복을 제외하면 적재할 문서가 없습니다."
        if errors:
            detail += f" ({'; '.join(errors)})"
        raise HTTPException(status_code=409, detail=detail)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    data_path = DATA_DIR / f"upload_{ts}.jsonl"
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(data_path, "w", encoding="utf-8") as fp:
        for d in documents:
            fp.write(_json.dumps(d, ensure_ascii=False) + "\n")

    job_id = secrets.token_hex(4)
    _upload_jobs[job_id] = {
        "job_id": job_id, "status": "processing",
        "current_step": "파일 저장 완료",
        "user_id": user["user_id"],
        "created_at": time.time(),
        "total_docs": len(documents),
        "data_file": data_path.name,
        "errors": errors, "output": "",
    }
    doc_id_list = [d["id"] for d in documents]
    try:
        with db.rag_conn() as conn, conn.cursor() as cur:
            cur.execute("""
                INSERT INTO audit.upload_log (user_id, job_id, doc_ids, doc_count, data_file)
                VALUES (%s, %s, %s, %s, %s)
            """, (user["user_id"], job_id, doc_id_list, len(documents), data_path.name))
            conn.commit()
    except Exception:
        logging.exception("업로드 감사 로그 기록 실패 (job_id=%s)", job_id)
    threading.Thread(
        target=_run_ingest_subprocess,
        args=(job_id, str(data_path)), daemon=True,
    ).start()
    return {"job_id": job_id, "total_docs": len(documents),
            "data_file": data_path.name, "errors": errors}


@app.post("/api/admin/upload/check-ids")
async def admin_upload_check_ids(request: Request, user: dict = Depends(admin_required)):
    body = await request.json()
    ids = body.get("ids", [])
    if not ids:
        return {"duplicates": []}
    from starter import db
    with db.rag_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT DISTINCT doc_id FROM rag.chunks WHERE doc_id = ANY(%s)",
            (ids,),
        )
        existing = [row[0] for row in cur.fetchall()]
    return {"duplicates": existing}


@app.get("/api/admin/upload/jobs")
def admin_upload_jobs(user: dict = Depends(admin_required)):
    jobs = sorted(_upload_jobs.values(), key=lambda j: j["created_at"], reverse=True)
    return {"jobs": jobs[:20]}


@app.get("/api/admin/upload/jobs/{job_id}")
def admin_upload_job_detail(job_id: str, user: dict = Depends(admin_required)):
    job = _upload_jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="작업을 찾을 수 없습니다.")
    return job


@app.get("/api/admin/upload/history")
def admin_upload_history(
    page: int = Query(1, ge=1),
    size: int = Query(10, ge=1, le=50),
    user: dict = Depends(admin_required),
):
    with db.rag_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM audit.upload_log")
        total = cur.fetchone()[0]
        cur.execute("""
            SELECT user_id, job_id, doc_ids, doc_count, data_file,
                   status, created_at, finished_at, error_summary
            FROM audit.upload_log
            ORDER BY created_at DESC
            LIMIT %s OFFSET %s
        """, (size, (page - 1) * size))
        items = [{
            "user_id": r[0], "job_id": r[1], "doc_ids": r[2] or [],
            "doc_count": r[3], "data_file": r[4], "status": r[5],
            "created_at": str(r[6]), "finished_at": str(r[7]) if r[7] else None,
            "error_summary": r[8],
        } for r in cur.fetchall()]
    return {"items": items, "total": total, "page": page, "size": size}
