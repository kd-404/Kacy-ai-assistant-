"""
Executors: what actually happens when a person presses the button on an approval card.

This is the only place that sends an email, creates a calendar event or a Google Doc,
files a leave request, records a decision or opens a ticket. It runs inside the
approve route, as the signed-in person, after the card they saw. The model never
calls anything here.

Live vs demo: a real Google session acts through Gmail, Calendar and Drive with that
person's own permissions. A demo session records what WOULD have happened and says so;
nothing leaves the app.
"""
from __future__ import annotations

from app import db
from app.integrations import gcalendar, gdrive, gmail
from app.timeutil import tz
from app.tools.context import google_token
from app.tools.mcp_approval_chain.server import apply_decision, submit_request
from app.tools.mcp_it_ticket.server import create_ticket


def execute(action: dict, session) -> dict:
    """Run one approved action. Returns {"message": ..., optional "link"}; raises on failure."""
    kind, p, owner = action["kind"], action["payload"], action["owner"]
    return _RUNNERS[kind](owner, p, session)


def _email(owner: str, p: dict, session) -> dict:
    token = google_token(session, "gmail.send")
    if token is None:
        # Demo account: keep it in the demo outbox so the flow can be shown, but send nothing.
        return {"message": f"Demo mode: this email to {p['to']} was not sent. Sign in with Google to send for real.",
                "simulated": True}
    sent = gmail.send(token, p["to"], p["subject"], p["body"])
    return {"message": f"Email sent to {p['to']}.", "gmail_message_id": sent.get("id")}


def _event(owner: str, p: dict, session) -> dict:
    token = google_token(session, "calendar.events")
    if token is None:
        db.run("INSERT INTO demo_events (owner, title, start, end, attendees, location) VALUES (?,?,?,?,?,?)",
               (owner, p["title"], p["start_ts"], p["end_ts"], db.dumps(p["attendees"]), ""))
        return {"message": f"Added to your demo calendar: {p['title']}, {p['when_label']}. No invitations were sent (demo mode).",
                "simulated": True}
    created = gcalendar.create_event(token, p["title"], p["start_iso"], p["end_iso"], p["attendees"],
                                     p.get("description", ""), time_zone=str(tz()))
    who = f" Invitations sent to {', '.join(p['attendees'])}." if p["attendees"] else ""
    return {"message": f"Event created: {p['title']}, {p['when_label']}.{who}", "link": created["link"], "meet": created["meet"]}


def _document(owner: str, p: dict, session) -> dict:
    token = google_token(session, "drive.file")
    link, source = "", "demo"
    if token is not None:
        link = gdrive.create_doc(token, p["title"], p["content"])["link"]
        source = "google"
    # Keep our own copy: with drive.file Kacy can read back only what it made itself.
    db.run("INSERT INTO documents (owner, title, content, url, source, created) VALUES (?,?,?,?,?,?)",
           (owner, p["title"], p["content"], link, source, db.now()))
    if token is None:
        return {"message": f"Saved '{p['title']}' to your demo documents. No Google Doc was created (demo mode).", "simulated": True}
    return {"message": f"Google Doc created: {p['title']}.", "link": link}


def _ticket(owner: str, p: dict, session) -> dict:
    return create_ticket(owner, p)


_RUNNERS = {
    "email": _email,
    "event": _event,
    "document": _document,
    "leave_request": lambda owner, p, session: submit_request(owner, p),
    "leave_decision": lambda owner, p, session: apply_decision(owner, p),
    "ticket": _ticket,
}
