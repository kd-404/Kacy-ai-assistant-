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

from app import actions, chats, desk, timeutil
from app.agent.client import ContentFiltered, KacyAgent
from app.agents_catalog import allowed_tools, build_context, get_agent
from app.api import router as api_router
from app.cost import semantic_cache
from app.guardrails.prompt_shields import PromptInjectionDetected, check_retrieved_content, check_user_message
from app.identity.rbac import AccessDenied, role_for
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
desk.completer = _agent.complete  # Desk's small model jobs (briefs, relationship) use the same deployment

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
    on_desk: bool = False           # chat was started from a Desk window


class ChatResponse(BaseModel):
    reply: str
    chat_id: str
    title: str
    cache_hit: bool = False
    access_denied: bool = False
    pending_actions: list[dict] = []
    new_actions: list[dict] = []    # approval cards created by this turn (shown inline with the answer)
    activity: dict = {}


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


def _date_line() -> str:
    """Models don't know today's date. Without it, 'next Friday at 3' cannot become a real time."""
    now = timeutil.now_local()
    return f"Current date and time: {now.strftime('%A')} {now.day} {now.strftime('%B %Y, %H:%M')} ({timeutil.tz()})."


def _step(id_: str, label: str, detail: str, status: str = "ok") -> dict:
    return {"id": id_, "label": label, "detail": detail, "status": status}


@app.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest, request: Request) -> ChatResponse:
    started = time.perf_counter()
    user_email, session = current_user(request, req.user_email)
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
    context = build_context(agent_id, chats.get_settings(user_email))  # personal style/focus only
    # Desk: summaries of chats linked to this one. They are personal, so they also turn the cache off.
    linked, linked_notes = desk.linked_context(user_email, chat_row["id"] if chat_row else None, check_retrieved_content)
    prefix = f"<context>\n{_date_line()}\n</context>\n\n" + context + linked

    # 2) Semantic cache. Shared across users, so it is only used when nothing about
    #    the answer depends on this user's agent or personal instructions.
    cached = None if (context or linked) else semantic_cache.get(message)
    actions_before = {a["id"] for a in actions.pending_for(user_email)}

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
                prefix + message, user_email=user_email, thread_id=conversation, seed_history=seed, session=session,
                allowed_tools=allowed_tools(agent_id),
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
        if not result["tools_called"] and not context and not linked:
            semantic_cache.set(message, reply)

    if linked_notes:
        steps.append(_step("linked", "Linked chats", ". ".join(linked_notes),
                           "ok" if linked else "skipped"))
    for call in tool_trace:
        detail = "Denied by role check" if call["denied"] else f"Ran as {role}"
        if call["notes"]:
            detail += ". " + ". ".join(call["notes"])
        blocked = any("blocked" in n for n in call["notes"])
        steps.append(_step("tool", f"Tool: {call['name']}", detail,
                           "denied" if call["denied"] or blocked else "ok"))

    new_actions = [actions.public(a) for a in actions.pending_for(user_email) if a["id"] not in actions_before]
    if new_actions:
        what = ", ".join(sorted({a["title"].lower() for a in new_actions}))
        steps.append(_step("approval", "Human approval", f"Waiting for you: {what}", "pending"))

    # 3) Save the turn. The chat row is created only now, so a blocked first message
    #    doesn't leave an empty chat in the sidebar.
    if chat_row is None:
        chat_id = chats.create_chat(user_email, message, agent_id)
        title = chats.get_chat(user_email, chat_id)["title"]
        if req.on_desk:
            chats.set_desk(user_email, chat_id, {"open": True})  # the client sends the real layout right after
    else:
        chat_id, title = chat_row["id"], chat_row["title"]
    if result is not None and not (chat_row and chat_row["conversation"]):
        chats.set_conversation(user_email, chat_id, result["thread_id"])
    if not req.regenerate:
        chats.add_message(user_email, chat_id, "user", message)
    ms = int((time.perf_counter() - started) * 1000)
    activity = {"steps": steps, "ms": ms}
    chats.add_message(user_email, chat_id, "assistant", reply,
                      {"activity": activity, "action_ids": [a["id"] for a in new_actions]})
    # Keep the brief fresh only where something reads it (a linked chat or one on the Desk), to save a model call.
    row = chats.get_chat(user_email, chat_id)
    if row and (chats.has_links(user_email, chat_id) or row["desk"] != "{}"):
        desk.update_brief(user_email, chat_id, message, reply)

    return ChatResponse(
        reply=reply,
        chat_id=chat_id,
        title=title,
        cache_hit=cached is not None,
        access_denied=access_denied,
        pending_actions=[actions.public(a) for a in actions.pending_for(user_email)],
        new_actions=new_actions,
        activity=activity,
    )
