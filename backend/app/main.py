"""
FastAPI entry point — the one HTTP surface the frontend talks to.

Request flow matches the architecture diagram: guardrails -> agent ->
(RAG / tools / cost routing, inside the agent run) -> response, with every
step logged for the observability layer.
"""
from __future__ import annotations

import logging

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from app.agent.client import ContentFiltered, KacyAgent
from app.cost import semantic_cache
from app.guardrails.prompt_shields import PromptInjectionDetected, check_user_message
from app.identity.rbac import AccessDenied
from app.tools.mcp_email import server as email_store
from app.web import current_user, router as web_router

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

app.include_router(web_router)

_agent = KacyAgent()


class ChatRequest(BaseModel):
    # Ignored when signed in with Google (the verified Google email wins).
    # Only demo accounts are accepted here, and only in local dev.
    user_email: str | None = None
    message: str
    thread_id: str | None = None


class ChatResponse(BaseModel):
    reply: str
    thread_id: str
    cache_hit: bool = False
    access_denied: bool = False
    pending_drafts: list[dict] = []


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest, request: Request) -> ChatResponse:
    user_email, _ = current_user(request, req.user_email)
    logger.info("chat.request user=%s thread=%s", user_email, req.thread_id)

    try:
        check_user_message(req.message)
    except PromptInjectionDetected as exc:
        logger.warning("chat.blocked kind=%s", exc.kind)
        raise HTTPException(status_code=400, detail=f"Blocked: {exc.kind} detected") from exc

    # The cache is shared across users, so it may only hold answers that are the
    # same for everyone (no tool calls). See the cache.set guard below.
    cached = semantic_cache.get(req.message)
    if cached is not None:
        logger.info("chat.cache_hit")
        return ChatResponse(reply=cached, thread_id=req.thread_id or "cache", cache_hit=True)

    try:
        result = _agent.run(req.message, user_email=user_email, thread_id=req.thread_id)
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

    # Only cache answers that involved no tools. A tool answer is personal
    # ("you have 12 days left") or has side effects (a draft was created), so
    # replaying it to the next person who types the same words would be wrong.
    if not result["tools_called"]:
        semantic_cache.set(req.message, result["reply"])

    drafts = [
        {k: d[k] for k in ("id", "to", "subject", "body", "status")}
        for d in email_store.pending_for(user_email)
    ]
    return ChatResponse(
        reply=result["reply"],
        thread_id=result["thread_id"],
        access_denied=result["access_denied"],
        pending_drafts=drafts,
    )
