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
        → {"masked_query", "query_tokens", "docs":[{"doc_id","snippet"}], "answer", "state"}
- 로그인 유지: 서명된 세션 쿠키(SESSION_SECRET). 쿠키에는 사번만 두고, 권한·계정 상태는 요청마다
  DB(policy.users)에서 다시 읽는다. 브라우저를 닫거나 로그아웃하면 끝난다.
  고도화 때 추가 예정: 미사용 시 자동 로그아웃(F-03), 로그인 실패 잠금(F-68).
- 질문 원문은 서버 로그·응답 어디에도 남기지 않는다. 응답에는 가명처리된 질문과 문서만 담는다.
- state: found(아하!) / sorry(문서 없음·확인 불가·답변 보류) / nollm(LLM 키 없음, 검색 결과만)
"""
import secrets
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.middleware.sessions import SessionMiddleware

from service import auth
from starter import chat, config, pii_pipeline

STATIC = Path(__file__).with_name("static")
SORRY_WORDS = ("확인되지 않", "찾지 못", "보류", "확인할 수 없", "알 수 없")
MIN_SECRET_LENGTH = 16


@asynccontextmanager
async def lifespan(_app):
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


def _state(docs, answer):
    if not docs:
        return "sorry"
    if answer is None:
        return "nollm"
    return "sorry" if any(w in answer for w in SORRY_WORDS) else "found"


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


@app.get("/api/health")
def health():
    return {"ok": True, "llm": bool(config.LLM_API_KEY), "embed": config.EMBED_PROVIDER,
            "search_mode": (config.env("SEARCH_MODE", "and") or "and")}


@app.post("/api/chat")
def ask(body: Ask, user: dict = Depends(login_required)):   # 로그인 필요. 동기 함수 → FastAPI가 스레드에서 실행 (탐지·임베딩·DB가 블로킹)
    try:
        res = chat.answer(body.query.strip(), body.k)
    except Exception as exc:           # 원문이 들어갈 수 있는 예외 메시지는 그대로 내보내지 않는다
        raise HTTPException(status_code=500, detail=f"처리 중 오류: {type(exc).__name__}") from None
    docs = [{"doc_id": d["doc_id"], "snippet": d["masked_text"][:180]} for d in res["docs"]]
    return {"masked_query": res["masked_query"], "query_tokens": res["query_tokens"],
            "docs": docs, "answer": res["answer"], "state": _state(res["docs"], res["answer"])}
