"""
IT ticket tool. open_it_ticket queues an approval card; the ticket is created only
after the person confirms what Kacy wrote (summary, details, priority).
"""
from __future__ import annotations

from app import actions, db

PRIORITIES = {"low", "normal", "high", "urgent"}


def open_it_ticket(requesting_user: str, summary: str, details: str, priority: str, *, session=None) -> dict:
    if priority not in PRIORITIES:
        priority = "normal"
    action = actions.propose(requesting_user, "ticket", {
        "summary": summary.strip()[:200], "details": details.strip()[:4000], "priority": priority})
    return {"action_id": action["id"], "status": "pending_approval",
            "note": "Shown to the user with an Open ticket button. NOT opened yet."}


def create_ticket(requester: str, p: dict) -> dict:
    ticket_id = db.run("INSERT INTO tickets (requester, summary, details, priority, created) VALUES (?,?,?,?,?)",
                       (requester, p["summary"], p["details"], p["priority"], db.now()))
    return {"message": f"Ticket IT-{1000 + ticket_id} opened ({p['priority']} priority).", "ticket": f"IT-{1000 + ticket_id}"}
