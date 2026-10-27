"""
The approval queue.

Anything Kacy wants to do that changes something outside the chat (send an
email, create a calendar event, make a document, file a leave request, open a
ticket, record a decision) is saved here as a pending action. The model can only
add to this queue. Running an action happens when the person presses the button
in the UI, via POST /api/actions/{id}/approve, which calls executors.py.

Each action carries a "view": the labelled rows the UI shows on the approval
card, so the card always displays exactly what will be executed.
"""
from __future__ import annotations

import json

from app import db

PENDING = "pending_approval"

# kind -> (card title, confirm button label)
KINDS = {
    "email": ("Email draft", "Send email"),
    "event": ("Calendar invite", "Add to calendar"),
    "document": ("New document", "Create document"),
    "leave_request": ("Leave request", "Submit request"),
    "leave_decision": ("Approval decision", "Confirm decision"),
    "ticket": ("IT ticket", "Open ticket"),
}


def propose(owner: str, kind: str, payload: dict) -> dict:
    if kind not in KINDS:
        raise ValueError(f"unknown action kind {kind}")
    action_id = db.run(
        "INSERT INTO actions (owner, kind, payload, created) VALUES (?,?,?,?)",
        (owner, kind, db.dumps(payload), db.now()),
    )
    return get(owner, action_id)


def _hydrate(row: dict) -> dict:
    row["payload"] = json.loads(row["payload"])
    row["result"] = json.loads(row["result"])
    title, confirm = KINDS[row["kind"]]
    row["title"], row["confirm_label"] = title, confirm
    row["view"] = view_of(row["kind"], row["payload"])
    return row


def get(owner: str, action_id: int) -> dict | None:
    row = db.one("SELECT * FROM actions WHERE id=? AND owner=?", (action_id, owner))
    return _hydrate(row) if row else None


def pending_for(owner: str) -> list[dict]:
    return [_hydrate(r) for r in db.rows(
        "SELECT * FROM actions WHERE owner=? AND status=? ORDER BY id", (owner, PENDING))]


def claim(action_id: int) -> bool:
    """Atomically move pending -> running. False if someone else already did (double click)."""
    return db.run("UPDATE actions SET status='running' WHERE id=? AND status=?", (action_id, PENDING)) == 1


def release(action_id: int) -> None:
    """Put a claimed action back to pending after a failure that changed nothing."""
    db.run("UPDATE actions SET status=? WHERE id=? AND status='running'", (PENDING, action_id))


def set_status(action_id: int, status: str, result: dict | None = None) -> None:
    db.run("UPDATE actions SET status=?, result=?, decided=? WHERE id=?",
           (status, db.dumps(result or {}), db.now(), action_id))


def public(action: dict) -> dict:
    """What the browser gets. Never includes the payload's internals beyond the view."""
    return {k: action[k] for k in ("id", "kind", "title", "confirm_label", "status", "view", "result")}


def view_of(kind: str, p: dict) -> dict:
    """Rows shown on the approval card. `body` is a block of text, shown in full."""
    if kind == "email":
        return {"rows": [["To", p["to"]], ["Subject", p["subject"]]], "body": p["body"]}
    if kind == "event":
        rows = [["Title", p["title"]], ["When", p["when_label"]], ["Duration", f"{p['duration_minutes']} min"]]
        if p.get("attendees"):
            rows.append(["With", ", ".join(p["attendees"])])
        return {"rows": rows, "body": p.get("description", "")}
    if kind == "document":
        return {"rows": [["Title", p["title"]]], "body": p["content"]}
    if kind == "leave_request":
        rows = [["Dates", f"{p['start_date']} to {p['end_date']} ({p['days']} working days)"],
                ["Goes to", p["route"]]]
        return {"rows": rows, "body": p.get("reason", "")}
    if kind == "leave_decision":
        rows = [["Request", f"#{p['request_id']} from {p['requester']}"],
                ["Dates", p["dates"]], ["Decision", p["decision"].capitalize()]]
        return {"rows": rows, "body": p.get("comment", "")}
    if kind == "ticket":
        return {"rows": [["Summary", p["summary"]], ["Priority", p["priority"]]], "body": p.get("details", "")}
    return {"rows": [], "body": ""}
