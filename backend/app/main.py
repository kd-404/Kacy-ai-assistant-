"""
FastAPI entry point — the one HTTP surface the frontend talks to.

Request flow matches the architecture diagram: guardrails -> agent ->
(RAG / tools / cost routing, inside the agent run) -> response, with every
step logged for the observability layer.
"""
from __future__ import annotations

import logging

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from app.agent.client import ContentFiltered, KacyAgent
from app.cost import semantic_cache
from app.guardrails.prompt_shields import PromptInjectionDetected, check_user_message
from app.identity.rbac import AccessDenied

load_dotenv()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("kacy")

app = FastAPI(title="Kacy API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

_agent = KacyAgent()


class ChatRequest(BaseModel):
    user_email: str
    message: str
    thread_id: str | None = None


class ChatResponse(BaseModel):
    reply: str
    thread_id: str
    cache_hit: bool = False
    access_denied: bool = False


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest) -> ChatResponse:
    logger.info("chat.request user=%s thread=%s", req.user_email, req.thread_id)

    try:
        check_user_message(req.message)
    except PromptInjectionDetected as exc:
        logger.warning("chat.blocked kind=%s", exc.kind)
        raise HTTPException(status_code=400, detail=f"Blocked: {exc.kind} detected") from exc

    # NOTE: replies can contain per-user data (salary, leave balance), so they are
    # never served from the shared cache. The cache only holds generic answers.
    cached = semantic_cache.get(req.message)
    if cached is not None:
        logger.info("chat.cache_hit")
        return ChatResponse(reply=cached, thread_id=req.thread_id or "cache", cache_hit=True)

    try:
        result = _agent.run(req.message, user_email=req.user_email, thread_id=req.thread_id)
    except AccessDenied as exc:
        logger.info("chat.access_denied resource=%s role=%s", exc.resource, exc.role)
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ContentFiltered as exc:
        # Full Azure error body goes to the server log so we can see exactly which
        # filter fired; the caller gets a clean 400 instead of a 500 crash.
        logger.warning("chat.content_filtered flagged=%s raw=%s", exc.flagged, exc.raw)
        raise HTTPException(
            status_code=400,
            detail={"error": "content_filtered", "flagged": exc.flagged},
        ) from exc

    if not result["access_denied"]:
        semantic_cache.set(req.message, result["reply"])
    return ChatResponse(
        reply=result["reply"],
        thread_id=result["thread_id"],
        access_denied=result["access_denied"],
    )
