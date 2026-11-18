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
  chat.answer()의 state(감사 로그용 6개 값)를 UI_STATE 로 바꿔 보낸다. answered→found, search_only→nollm, 나머지→sorry
- 감사 로그: 질문마다 audit.query_log 1행 (service/audit.py). 처리 중 예외도 state=error 로 남긴다.
  기록에 실패해도 답변은 그대로 돌려준다 (outputs/audit_fallback.jsonl, outputs/server_error.log 에 남김).
- 실명 복원(Lv2 + 2차 인증): POST /api/verify-password {"password"} → 세션에 restore_verified
        POST /api/restore-tokens {"request_id", "tokens": [...]} → {"map": {토큰: 원문}}
  복원은 vault.restore_token()으로만 한다. 토큰마다 audit.restore_log 1행이 남고 request_id 로 query_log 와 이어진다.
  기록에 실패하면 복원도 실패한다(화면에는 토큰 그대로). query_log 와 달리 실명이 나가는 쪽이라 기록 없는 복원은 하지 않는다.
  DB 접속은 db.restore_conn() (지금 관리자 계정, 권한 정리 때 pii_search 로 교체).
"""
import logging
import re
import secrets
import time
from contextlib import asynccontextmanager
from pathlib import Path

import psycopg
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.middleware.sessions import SessionMiddleware

from service import audit, auth
from starter import chat, config, db, pii_pipeline

STATIC = Path(__file__).with_name("static")
UI_STATE = {"answered": "found", "search_only": "nollm"}   # 그 밖의 state 는 화면에서 sorry
MIN_SECRET_LENGTH = 16


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
    """세션의 로그인 사용자를 돌려준다. 로그인하지 않았거나 계정이 비활성이면 세션을 비우고 None."""
    user_id = request.session.get("user_id")
    user = auth.get_active_user(user_id) if isinstance(user_id, str) and user_id else None
    if user is None:
        request.session.clear()
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


class VerifyPassword(BaseModel):
    password: str = Field(min_length=1, max_length=200)


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
    request.session["user_id"] = result.user["user_id"]
    return {"user": result.user}


@app.post("/api/logout")
def logout(request: Request):
    request.session.clear()
    return {"ok": True}


@app.get("/api/me")
def me(user: dict = Depends(login_required)):
    return {"user": user}


@app.post("/api/verify-password")
def verify_password(body: VerifyPassword, request: Request, user: dict = Depends(login_required)):
    if user.get("access_level") != 2:
        raise HTTPException(status_code=403, detail="Lv2 권한이 필요합니다.")
    result = auth.authenticate(user["user_id"], body.password)
    if not result.ok:
        raise HTTPException(status_code=401, detail="비밀번호가 올바르지 않습니다.")
    request.session["restore_verified"] = True
    return {"ok": True}


@app.post("/api/restore-tokens")
def restore_tokens(body: RestoreTokens, request: Request, user: dict = Depends(login_required)):
    if user.get("access_level") != 2:
        raise HTTPException(status_code=403, detail="Lv2 권한이 필요합니다.")
    if not request.session.get("restore_verified"):
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


def _log_restore_error(request_id, exc):
    """복원 실패 사실만 남긴다 (outputs/server_error.log + 터미널). 예외 메시지·토큰·원문은 남기지 않는다."""
    audit._log_error("실명 복원 실패 request_id=%s error=%s", request_id, type(exc).__name__)


@app.post("/api/chat")
def ask(body: Ask, request: Request, user: dict = Depends(login_required)):
    request_id = audit.new_request_id()
    started = time.perf_counter()
    try:
        res = chat.answer(body.query.strip(), body.k)
    except Exception as exc:
        audit.record(audit.build_row(request_id, user, error=exc, latency_ms=_elapsed_ms(started)))
        raise HTTPException(status_code=500, detail=f"처리 중 오류: {type(exc).__name__}") from None
    audit.record(audit.build_row(request_id, user, result=res, latency_ms=_elapsed_ms(started)))
    docs = [{"doc_id": d["doc_id"], "snippet": d["masked_text"][:180]} for d in res["docs"]]
    answer_text = res["answer"]
    masked_query = res["masked_query"]
    restored = False

    should_restore = (
        body.restore
        and user.get("access_level") == 2
        and request.session.get("restore_verified")
    )
    if should_restore and (answer_text or docs):
        all_text = [answer_text or "", masked_query or ""] + [d["snippet"] for d in docs]
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
            restored = True

    return {"request_id": request_id, "masked_query": masked_query, "query_tokens": res["query_tokens"],
            "docs": docs, "answer": answer_text, "state": _ui_state(res["state"]),
            "restored": restored}


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


@app.get("/api/admin/stats")
def admin_stats(user: dict = Depends(admin_required)):
    from starter import db
    stats = {}
    with db.rag_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT COUNT(DISTINCT doc_id) FROM rag.chunks")
        stats["total_docs"] = cur.fetchone()[0]
        cur.execute("SELECT COUNT(DISTINCT doc_id) FROM rag.chunks WHERE created_at::date = CURRENT_DATE")
        stats["today_docs"] = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM rag.chunks")
        stats["total_chunks"] = cur.fetchone()[0]
    with db.vault_conn() as conn, conn.cursor() as cur:
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
    stats["fail_docs"] = 0
    return stats
