"""도장곰 창구: 가명처리 기반 검색 챗봇 데모 서버.

    uvicorn service.app:app --reload          (레포 루트에서 실행)
    → http://127.0.0.1:8000

- 화면: service/static/index.html
- API : POST /api/chat {"query": "...", "k": 3}
        → {"masked_query", "query_tokens", "docs":[{"doc_id","snippet"}], "answer", "state"}
- 질문 원문은 서버 로그·응답 어디에도 남기지 않는다. 응답에는 가명처리된 질문과 문서만 담는다.
- state: found(아하!) / sorry(문서 없음·확인 불가·답변 보류) / nollm(LLM 키 없음, 검색 결과만)
"""
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from starter import chat, config, pii_pipeline

STATIC = Path(__file__).with_name("static")
SORRY_WORDS = ("확인되지 않", "찾지 못", "보류", "확인할 수 없", "알 수 없")


@asynccontextmanager
async def lifespan(_app):
    pii_pipeline._detector()          # 탐지 모델을 미리 불러 첫 질문이 느려지지 않게 한다
    yield


app = FastAPI(title="도장곰 창구", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC), name="static")


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
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/api/health")
def health():
    return {"ok": True, "llm": bool(config.LLM_API_KEY), "embed": config.EMBED_PROVIDER,
            "search_mode": (config.env("SEARCH_MODE", "and") or "and")}


@app.post("/api/chat")
def ask(body: Ask):                    # 동기 함수 → FastAPI가 스레드에서 실행 (탐지·임베딩·DB가 블로킹)
    try:
        res = chat.answer(body.query.strip(), body.k)
    except Exception as exc:           # 원문이 들어갈 수 있는 예외 메시지는 그대로 내보내지 않는다
        raise HTTPException(status_code=500, detail=f"처리 중 오류: {type(exc).__name__}") from None
    docs = [{"doc_id": d["doc_id"], "snippet": d["masked_text"][:180]} for d in res["docs"]]
    return {"masked_query": res["masked_query"], "query_tokens": res["query_tokens"],
            "docs": docs, "answer": res["answer"], "state": _state(res["docs"], res["answer"])}
