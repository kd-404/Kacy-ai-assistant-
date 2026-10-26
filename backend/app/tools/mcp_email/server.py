"""
Email tool — draft & send. The first of the four new agentic actions.
`draft_email` never sends anything; `send_email` only ever gets called by the
backend after the user approves the draft in the UI's activity pane.
"""
from __future__ import annotations

from app.tools.base import ToolStore

_drafts = ToolStore()
_sent = ToolStore()


def draft_email(requesting_user: str, to: str, subject: str, body: str) -> dict:
    return _drafts.add({
        "from": requesting_user, "to": to, "subject": subject, "body": body, "status": "draft",
    })


def send_email(draft_id: int) -> dict:
    """Only call this after explicit user approval of the draft."""
    draft = _drafts.get(draft_id)
    if draft is None:
        return {"error": "no such draft"}
    sent = _sent.add({**draft, "status": "sent"})
    return sent
