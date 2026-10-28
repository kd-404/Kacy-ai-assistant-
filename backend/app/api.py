"""
JSON routes behind the sidebar: chat history, agents, settings.

Every route resolves the caller with current_user() (a verified Google session,
or a demo account in local dev) and passes that email into the store, which
filters on it. State-changing routes also require the X-Kacy header, the same
cross-site-request guard the draft routes use.
"""
from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel

from app import actions, chats, db, desk, executors
from app.agents_catalog import AGENTS
from app.guardrails.prompt_shields import check_retrieved_content
from app.identity.rbac import AccessDenied
from app.web import _require_app_header, current_user

import logging

logger = logging.getLogger("kacy.api")

router = APIRouter(prefix="/api")


class ChatPatch(BaseModel):
    pinned: bool | None = None
    title: str | None = None


class DeskGeom(BaseModel):
    open: bool = True
    min: bool = False
    x: float = 0
    y: float = 0
    w: float = 520
    h: float = 560
    z: int = 1


class LinkBody(BaseModel):
    a: str
    b: str
    two_way: bool = False


class LinkPatch(BaseModel):
    two_way: bool


class SettingsBody(BaseModel):
    display_name: str = ""
    tone: str = "balanced"
    instructions: str = ""


@router.get("/agents")
def agents() -> list[dict]:
    return [{k: a[k] for k in ("id", "name", "icon", "blurb", "can", "starters")} for a in AGENTS]


@router.get("/chats")
def list_chats(request: Request, q: str | None = None) -> list[dict]:
    owner, _ = current_user(request)
    return chats.list_chats(owner, q.strip() if q else None)


@router.get("/chats/{chat_id}")
def get_chat(chat_id: str, request: Request) -> dict:
    owner, _ = current_user(request)
    chat = chats.get_chat(owner, chat_id)
    if chat is None:
        raise HTTPException(status_code=404, detail="no such chat")
    messages = chats.messages(owner, chat_id)
    for m in messages:
        # Approval cards are stored as ids; show each one's CURRENT state (sent, discarded, still waiting).
        ids = m["meta"].pop("action_ids", [])
        m["meta"]["actions"] = [actions.public(a) for a in (actions.get(owner, i) for i in ids) if a]
    return {
        "id": chat["id"], "title": chat["title"], "agent": chat["agent"], "pinned": bool(chat["pinned"]),
        "messages": messages,
    }


@router.patch("/chats/{chat_id}")
def patch_chat(chat_id: str, body: ChatPatch, request: Request, x_kacy: str | None = Header(default=None)) -> dict:
    _require_app_header(x_kacy)
    owner, _ = current_user(request)
    if not chats.update_chat(owner, chat_id, pinned=body.pinned, title=body.title):
        raise HTTPException(status_code=404, detail="no such chat")
    return {"ok": True}


@router.delete("/chats/{chat_id}")
def delete_chat(chat_id: str, request: Request, x_kacy: str | None = Header(default=None)) -> dict:
    _require_app_header(x_kacy)
    owner, _ = current_user(request)
    if not chats.delete_chat(owner, chat_id):
        raise HTTPException(status_code=404, detail="no such chat")
    return {"ok": True}


@router.get("/settings")
def get_settings(request: Request) -> dict:
    owner, _ = current_user(request)
    return chats.get_settings(owner)


@router.put("/settings")
def put_settings(body: SettingsBody, request: Request, x_kacy: str | None = Header(default=None)) -> dict:
    _require_app_header(x_kacy)
    owner, _ = current_user(request)
    return chats.save_settings(owner, body.model_dump())


# ---------- Approval queue: the human step ----------

@router.get("/actions")
def pending_actions(request: Request) -> list[dict]:
    owner, _ = current_user(request)
    return [actions.public(a) for a in actions.pending_for(owner)]


@router.get("/actions/{action_id}")
def get_action(action_id: int, request: Request) -> dict:
    owner, _ = current_user(request)
    action = actions.get(owner, action_id)
    if action is None:
        raise HTTPException(status_code=404, detail="no such action")
    return actions.public(action)


@router.post("/actions/{action_id}/approve")
def approve_action(action_id: int, request: Request, x_kacy: str | None = Header(default=None)) -> dict:
    """
    The ONLY path that sends, creates or records anything. It needs a signed-in session, and the
    action must belong to that person. The status flips to 'running' in one atomic UPDATE, so a
    double click cannot run it twice.
    """
    _require_app_header(x_kacy)
    owner, session = current_user(request)
    # Same 404 whether it doesn't exist or belongs to someone else: don't reveal which.
    action = actions.get(owner, action_id)
    if action is None:
        raise HTTPException(status_code=404, detail="no such action")
    if not actions.claim(action_id):
        raise HTTPException(status_code=409, detail=f"already {action['status'].replace('_', ' ')}")

    try:
        result = executors.execute(action, session)
    except PermissionError as exc:
        actions.release(action_id)  # nothing happened; let them retry after signing in again
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except AccessDenied as exc:
        actions.set_status(action_id, "failed", {"message": str(exc)})
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        actions.set_status(action_id, "failed", {"message": str(exc)})
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 - Google/network failure: keep the card so they can retry
        logger.warning("action.failed id=%s kind=%s err=%s", action_id, action["kind"], exc)
        actions.release(action_id)
        raise HTTPException(status_code=502, detail="That service did not accept the request. Try again in a moment.") from exc

    actions.set_status(action_id, "done", result)
    logger.info("action.done id=%s kind=%s by=%s", action_id, action["kind"], owner)
    return actions.public(actions.get(owner, action_id))


@router.post("/actions/{action_id}/discard")
def discard_action(action_id: int, request: Request, x_kacy: str | None = Header(default=None)) -> dict:
    _require_app_header(x_kacy)
    owner, _ = current_user(request)
    if actions.get(owner, action_id) is None:
        raise HTTPException(status_code=404, detail="no such action")
    if not actions.claim(action_id):
        raise HTTPException(status_code=409, detail="already handled")
    actions.set_status(action_id, "discarded", {"message": "Discarded."})
    return actions.public(actions.get(owner, action_id))


# ---------- Desk ----------

@router.get("/desk")
def desk_state(request: Request) -> dict:
    owner, _ = current_user(request)
    return {"windows": chats.desk_chats(owner), "links": chats.list_links(owner)}


@router.put("/desk/{chat_id}")
def desk_put(chat_id: str, body: DeskGeom, request: Request, x_kacy: str | None = Header(default=None)) -> dict:
    _require_app_header(x_kacy)
    owner, _ = current_user(request)
    if not chats.set_desk(owner, chat_id, body.model_dump()):
        raise HTTPException(status_code=404, detail="no such chat")
    return {"ok": True}


@router.post("/links")
def link_create(body: LinkBody, request: Request, x_kacy: str | None = Header(default=None)) -> dict:
    _require_app_header(x_kacy)
    owner, _ = current_user(request)
    try:
        link = chats.add_link(owner, body.a, body.b, body.two_way)
    except LookupError:
        raise HTTPException(status_code=404, detail="no such chat")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    for cid in (body.a, body.b):  # older chats get their first brief now, so the link has something to carry
        desk.ensure_brief(owner, cid)
    return link


@router.patch("/links/{link_id}")
def link_patch(link_id: str, body: LinkPatch, request: Request, x_kacy: str | None = Header(default=None)) -> dict:
    _require_app_header(x_kacy)
    owner, _ = current_user(request)
    if not chats.update_link(owner, link_id, body.two_way):
        raise HTTPException(status_code=404, detail="no such link")
    return {"ok": True}


@router.delete("/links/{link_id}")
def link_delete(link_id: str, request: Request, x_kacy: str | None = Header(default=None)) -> dict:
    _require_app_header(x_kacy)
    owner, _ = current_user(request)
    if not chats.remove_link(owner, link_id):
        raise HTTPException(status_code=404, detail="no such link")
    return {"ok": True}


@router.post("/links/{link_id}/relationship")
def link_relationship(link_id: str, request: Request, x_kacy: str | None = Header(default=None)) -> dict:
    _require_app_header(x_kacy)
    owner, _ = current_user(request)
    link = chats.get_link(owner, link_id)
    a = chats.get_chat(owner, link["a"]) if link else None
    b = chats.get_chat(owner, link["b"]) if link else None
    if not (a and b):
        raise HTTPException(status_code=404, detail="no such link")
    try:  # briefs can carry email/document text, so they get the same scan as any external text
        check_retrieved_content([a["brief"], b["brief"]])
    except Exception:  # noqa: BLE001 - attack found or scanner down: do not feed them to the model
        raise HTTPException(status_code=422, detail="One of these summaries failed the safety scan, so Kacy won't compare them.")
    return desk.relationship(a["title"], a["brief"], b["title"], b["brief"])
