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

from app import chats
from app.agents_catalog import AGENTS
from app.web import _require_app_header, current_user

router = APIRouter(prefix="/api")


class ChatPatch(BaseModel):
    pinned: bool | None = None
    title: str | None = None


class SettingsBody(BaseModel):
    display_name: str = ""
    tone: str = "balanced"
    instructions: str = ""


@router.get("/agents")
def agents() -> list[dict]:
    return [{k: a[k] for k in ("id", "name", "blurb", "starters")} for a in AGENTS]


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
    return {
        "id": chat["id"], "title": chat["title"], "agent": chat["agent"], "pinned": bool(chat["pinned"]),
        "messages": chats.messages(owner, chat_id),
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
