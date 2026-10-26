"""IT ticket tool — mocked Jira-style ticketing. Write action: goes through the approval gate."""
from __future__ import annotations

from app.tools.base import ToolStore

_store = ToolStore()


def create_ticket(requesting_user: str, summary: str, priority: str = "normal") -> dict:
    return _store.add({
        "requested_by": requesting_user,
        "summary": summary,
        "priority": priority,
        "status": "open",
    })


def get_ticket_status(ticket_id: int) -> dict:
    ticket = _store.get(ticket_id)
    return ticket or {"error": "no such ticket"}
