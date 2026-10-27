"""
FastAPI entry point: the one HTTP surface the UI talks to.

Request flow matches the architecture diagram: guardrails -> agent ->
(RAG / tools / cost routing, inside the agent run) -> response. Each step
that runs is also returned as an "activity" list, which the UI draws as the
timeline in its right-hand pane.
"""
from __future__ import annotations

import logging
import os
import time
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app import chats
from app.agent.client import ContentFiltered, KacyAgent
from app.agents_catalog import build_context, get_agent
from app.api import router as api_router
from app.cost import semantic_cache
from app.guardrails.prompt_shields import PromptInjectionDetected, check_user_message
from app.identity.rbac import AccessDenied, role_for
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
app.include_router(api_router)

_agent = KacyAgent()

UI_DIR = Path(__file__).resolve().parent / "ui"
app.mount("/ui", StaticFiles(directory=UI_DIR), name="ui")


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(UI_DIR / "index.html")


class ChatRequest(BaseModel):
    # Ignored when signed in with Google (the verified Google email wins).
    # Only demo accounts are accepted here, and only in local dev.
    user_email: str | None = None
    message: str
    chat_id: str | None = None      # our chat id (sidebar entry); None starts a new chat
    agent: str | None = None        # which agent preset, for a new chat
    regenerate: bool = False        # answer the last question again


class ChatResponse(BaseModel):
    reply: str
    chat_id: str
    title: str
    cache_hit: bool = False
    access_denied: bool = False
    pending_drafts: list[dict] = []
    new_drafts: list[dict] = []     # drafts created by this turn (shown inline with the answer)
    activity: dict = {}


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


def _draft_view(d: dict) -> dict:
    return {k: d[k] for k in ("id", "to", "subject", "body", "status")}


def _step(id_: str, label: str, detail: str, status: str = "ok") -> dict:
    return {"id": id_, "label": label, "detail": detail, "status": status}


@app.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest, request: Request) -> ChatResponse:
    started = time.perf_counter()
    user_email, _ = current_user(request, req.user_email)
    try:
        role = role_for(user_email).value
    except AccessDenied:
        role = "unknown"

    chat_row = None
    if req.chat_id:
        chat_row = chats.get_chat(user_email, req.chat_id)
        if chat_row is None:
            raise HTTPException(status_code=404, detail="no such chat")

    message = req.message.strip()
    if req.regenerate:
        # Re-ask the last question; the old answer is removed from the history we show.
        if chat_row is None:
            raise HTTPException(status_code=400, detail="nothing to regenerate")
        message = chats.drop_last_assistant(user_email, chat_row["id"]) or ""
    if not message:
        raise HTTPException(status_code=400, detail="empty message")

    logger.info("chat.request user=%s chat=%s", user_email, req.chat_id)
    steps = [_step("identity", "Signed in", f"{user_email} as {role}")]

    # 1) Guardrail: Prompt Shields looks at what the user typed, nothing else.
    try:
        check_user_message(message)
    except PromptInjectionDetected as exc:
        logger.warning("chat.blocked kind=%s", exc.kind)
        steps.append(_step("shields", "Prompt Shields", f"{exc.kind} detected, request stopped", "blocked"))
        raise HTTPException(
            status_code=400,
            detail={"error": "blocked", "kind": exc.kind, "activity": {"steps": steps}},
        ) from exc
    steps.append(_step("shields", "Prompt Shields", "No injection or jailbreak found"))

    agent_id = chat_row["agent"] if chat_row else get_agent(req.agent)["id"]
    context = build_context(agent_id, chats.get_settings(user_email))

    # 2) Semantic cache. Shared across users, so it is only used when nothing about
    #    the answer depends on this user's agent or personal instructions.
    cached = None if context else semantic_cache.get(message)
    drafts_before = {d["id"] for d in email_store.pending_for(user_email)}

    result: dict | None = None
    if cached is not None:
        logger.info("chat.cache_hit")
        steps.append(_step("cache", "Semantic cache", "Hit, no model call needed"))
        reply, access_denied, tool_trace = cached, False, []
    else:
        steps.append(_step("cache", "Semantic cache", "Miss", "skipped"))
        conversation = chat_row["conversation"] if chat_row else None
        seed = chats.messages(user_email, chat_row["id"]) if chat_row and not conversation else None
        try:
            result = _agent.run(
                context + message, user_email=user_email, thread_id=conversation, seed_history=seed
            )
        except AccessDenied as exc:
            logger.info("chat.access_denied resource=%s role=%s", exc.resource, exc.role)
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except ContentFiltered as exc:
            # Azure's own content filter (on the model deployment) fired. The full body
            # goes to the log; the user gets a clean message naming the categories.
            logger.warning("chat.content_filtered flagged=%s raw=%s", exc.flagged, exc.raw)
            steps.append(_step("filter", "Azure content filter", ", ".join(exc.flagged) or "policy", "blocked"))
            raise HTTPException(
                status_code=400,
                detail={"error": "content_filtered", "flagged": exc.flagged, "activity": {"steps": steps}},
            ) from exc
        reply, access_denied, tool_trace = result["reply"], result["access_denied"], result["tool_trace"]
        steps.append(_step("agent", "Agent", f"{get_agent(agent_id)['name']} on {_agent.model}"))

        # Only cache answers that involved no tools and no personal context. A tool answer is
        # personal ("you have 12 days left") or has side effects (a draft was created).
        if not result["tools_called"] and not context:
            semantic_cache.set(message, reply)

    for call in tool_trace:
        steps.append(_step(
            "tool", f"Tool: {call['name']}",
            "Denied by role check" if call["denied"] else f"Ran as {role}",
            "denied" if call["denied"] else "ok",
        ))
    if any(not c["denied"] and c["name"] != "draft_email" for c in tool_trace):
        steps.append(_step("pii", "PII redaction", "Tool output scrubbed before the model saw it"))

    new_drafts = [_draft_view(d) for d in email_store.pending_for(user_email) if d["id"] not in drafts_before]
    if new_drafts:
        steps.append(_step("approval", "Human approval", "Draft is waiting for your Send", "pending"))

    # 3) Save the turn. The chat row is created only now, so a blocked first message
    #    doesn't leave an empty chat in the sidebar.
    if chat_row is None:
        chat_id = chats.create_chat(user_email, message, agent_id)
        title = chats.get_chat(user_email, chat_id)["title"]
    else:
        chat_id, title = chat_row["id"], chat_row["title"]
    if result is not None and not (chat_row and chat_row["conversation"]):
        chats.set_conversation(user_email, chat_id, result["thread_id"])
    if not req.regenerate:
        chats.add_message(user_email, chat_id, "user", message)
    ms = int((time.perf_counter() - started) * 1000)
    activity = {"steps": steps, "ms": ms}
    chats.add_message(user_email, chat_id, "assistant", reply, {"activity": activity, "drafts": new_drafts})

    return ChatResponse(
        reply=reply,
        chat_id=chat_id,
        title=title,
        cache_hit=cached is not None,
        access_denied=access_denied,
        pending_drafts=[_draft_view(d) for d in email_store.pending_for(user_email)],
        new_drafts=new_drafts,
        activity=activity,
    )
