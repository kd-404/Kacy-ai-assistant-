"""
Email tool: drafts only.

The agent can call `draft_email`. It has no tool that sends. A draft sits in
`pending_approval` until its owner presses Send in the app, which hits the
backend's /drafts/{id}/send route; that route (not the model) does the send.
So no prompt, however clever, can make Kacy send an email on its own.
"""
from __future__ import annotations

from app.tools.base import ToolStore

_drafts = ToolStore()


def draft_email(requesting_user: str, to: str, subject: str, body: str) -> dict:
    draft = _drafts.add({
        "owner": requesting_user,
        "to": to,
        "subject": subject,
        "body": body,
        "status": "pending_approval",
    })
    # What the model gets back: just enough to tell the user what happens next.
    # It already knows what it wrote, so the content isn't echoed back.
    return {
        "draft_id": draft["id"],
        "status": "pending_approval",
        "note": "Draft shown to the user with a Send button. It has NOT been sent.",
    }


def pending_for(owner: str) -> list[dict]:
    return [d for d in _drafts.all() if d["owner"] == owner and d["status"] == "pending_approval"]


def get_draft(draft_id: int) -> dict | None:
    return _drafts.get(draft_id)


def mark(draft_id: int, status: str, **extra) -> None:
    draft = _drafts.get(draft_id)
    if draft is not None:
        draft.update(status=status, **extra)
