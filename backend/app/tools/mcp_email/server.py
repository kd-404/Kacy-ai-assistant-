"""
Email tools: draft, search the inbox, read one message.

The agent can call `draft_email` but has no tool that sends. A draft waits in the
approval queue until its owner presses Send, which hits the backend's
/api/actions/{id}/approve route; that route (not the model) does the send. So no
prompt, however clever, can make Kacy send an email on its own.

Inbox text is written by other people, so results carry a note telling the model to
treat it as data, and dispatch() runs it through Prompt Shields before the model sees it.
"""
from __future__ import annotations

import re

from app import actions, db, demo_seed, timeutil
from app.integrations import gmail
from app.tools.context import UNTRUSTED_NOTE, google_token

_EMAIL_RE = re.compile(r"^[^@\s,;<>]+@[^@\s,;<>]+\.[^@\s,;<>]+$")


def draft_email(requesting_user: str, to: str, subject: str, body: str, *, session=None) -> dict:
    to = to.strip()
    if not _EMAIL_RE.match(to):
        return {"error": f"'{to}' is not a single valid email address. Ask the user for the address."}
    action = actions.propose(requesting_user, "email", {
        "to": to, "subject": subject.strip()[:200], "body": body.strip()[:8000]})
    return {
        "action_id": action["id"], "status": "pending_approval",
        "note": "Draft shown to the user with a Send button. It has NOT been sent.",
    }


def _demo_filter(owner: str, query: str, limit: int) -> list[dict]:
    demo_seed.ensure_seeded(owner)
    sql, args = "SELECT * FROM demo_emails WHERE owner=?", [owner]
    for term in query.lower().split():
        if term == "is:unread":
            sql += " AND unread=1"
        elif term.startswith("from:"):
            sql += " AND lower(sender) LIKE ?"
            args.append(f"%{term[5:]}%")
        else:
            sql += " AND (lower(subject) LIKE ? OR lower(body) LIKE ? OR lower(sender) LIKE ?)"
            args += [f"%{term}%"] * 3
    sql += " ORDER BY date DESC LIMIT ?"
    args.append(limit)
    return db.rows(sql, args)


def search_inbox(requesting_user: str, query: str, max_results: int, *, session=None) -> dict:
    max_results = max(1, min(int(max_results), 10))
    token = google_token(session, "gmail.readonly")
    if token:
        messages = gmail.search(token, query, max_results)
        return {"mode": "google", "messages": messages, "note": UNTRUSTED_NOTE}
    rows = _demo_filter(requesting_user, query, max_results)
    return {"mode": "demo", "note": UNTRUSTED_NOTE, "messages": [
        {"id": str(r["id"]), "from": r["sender"], "subject": r["subject"], "date": timeutil.label(r["date"]),
         "snippet": " ".join(r["body"].split())[:160], "unread": bool(r["unread"])} for r in rows]}


def read_email(requesting_user: str, message_id: str, *, session=None) -> dict:
    token = google_token(session, "gmail.readonly")
    if token:
        return {"mode": "google", "email": gmail.read(token, message_id), "note": UNTRUSTED_NOTE}
    row = db.one("SELECT * FROM demo_emails WHERE id=? AND owner=?", (message_id if message_id.isdigit() else -1, requesting_user))
    if row is None:
        return {"error": "no such email"}
    return {"mode": "demo", "note": UNTRUSTED_NOTE, "email": {
        "id": str(row["id"]), "from": row["sender"], "subject": row["subject"],
        "date": timeutil.label(row["date"]), "body": row["body"]}}
