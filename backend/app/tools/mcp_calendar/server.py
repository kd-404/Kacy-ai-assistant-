"""
Calendar tools: read your schedule, find a time that works, propose a meeting.

list_events / find_free_slots only read. propose_meeting writes nothing to any
calendar: it queues an approval card, and the invite is created (and emailed to
attendees) only when the person presses the button.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone

from app import actions, db, demo_seed, timeutil
from app.integrations import gcalendar
from app.tools.context import google_token
from app.identity.rbac import DEMO_USERS

_EMAIL_RE = re.compile(r"^[^@\s,;<>]+@[^@\s,;<>]+\.[^@\s,;<>]+$")


def _day_bounds(days_ahead: int) -> tuple[float, float]:
    start = timeutil.now_local().replace(hour=0, minute=0, second=0, microsecond=0)
    return start.timestamp(), (start + timedelta(days=days_ahead)).timestamp()


def _clean_emails(emails: list[str] | None) -> list[str]:
    out = []
    for e in emails or []:
        e = e.strip().lower()
        if not _EMAIL_RE.match(e):
            raise ValueError(f"not a valid email address: {e!r}")
        if e not in out:
            out.append(e)
    if len(out) > 20:
        raise ValueError("too many attendees (max 20)")
    return out


def list_events(requesting_user: str, days_ahead: int = 1, *, session=None) -> dict:
    days_ahead = max(1, min(int(days_ahead), 14))
    t0, t1 = _day_bounds(days_ahead)
    token = google_token(session, "calendar.events")
    if token:
        events = gcalendar.list_events(token, timeutil.iso(t0), timeutil.iso(t1))
        return {"mode": "google", "timezone": str(timeutil.tz()), "events": events}
    demo_seed.ensure_seeded(requesting_user)
    rows = db.rows("SELECT * FROM demo_events WHERE owner=? AND start>=? AND start<? ORDER BY start",
                   (requesting_user, t0, t1))
    return {"mode": "demo", "timezone": str(timeutil.tz()), "events": [
        {"title": r["title"], "start": timeutil.iso(r["start"]), "end": timeutil.iso(r["end"]),
         "attendees": json.loads(r["attendees"]), "location": r["location"]} for r in rows]}


def find_free_slots(requesting_user: str, duration_minutes: int, days_ahead: int,
                    attendee_emails: list[str] | None = None, *, session=None) -> dict:
    duration_minutes = max(15, min(int(duration_minutes), 240))
    days_ahead = max(1, min(int(days_ahead), 14))
    attendees = _clean_emails(attendee_emails)
    t0, t1 = _day_bounds(days_ahead + 1)
    busy: list[tuple[float, float]] = []
    unreadable: list[str] = []

    token = google_token(session, "calendar.freebusy")
    if token:
        found, unreadable = gcalendar.busy(token, attendees, timeutil.iso(t0), timeutil.iso(t1))
        busy = [(datetime.fromisoformat(a.replace("Z", "+00:00")).timestamp(),
                 datetime.fromisoformat(b.replace("Z", "+00:00")).timestamp()) for a, b in found]
        mode = "google"
    else:
        mode = "demo"
        for who in [requesting_user] + attendees:
            if who == requesting_user or who in DEMO_USERS:
                demo_seed.ensure_seeded(who)
                busy += [(r["start"], r["end"]) for r in db.rows(
                    "SELECT start, end FROM demo_events WHERE owner=? AND end>? AND start<?", (who, t0, t1))]
            else:
                unreadable.append(who)

    slots = timeutil.free_slots(busy, duration_minutes, days_ahead)
    return {
        "mode": mode, "timezone": str(timeutil.tz()), "duration_minutes": duration_minutes,
        "slots": [{"start": timeutil.iso(s), "label": timeutil.label(s)} for s in slots],
        "could_not_check_calendars_of": unreadable,
    }


def propose_meeting(requesting_user: str, title: str, start: str, duration_minutes: int,
                    attendee_emails: list[str] | None, description: str, *, session=None) -> dict:
    try:
        start_ts = timeutil.parse_when(start)
    except ValueError:
        return {"error": "start must be an ISO 8601 date and time, for example 2026-10-30T15:00"}
    duration_minutes = int(duration_minutes)
    if not 5 <= duration_minutes <= 480:
        return {"error": "duration_minutes must be between 5 and 480"}
    if start_ts < datetime.now(timezone.utc).timestamp():
        return {"error": "that time is in the past"}
    attendees = _clean_emails(attendee_emails)
    end_ts = start_ts + duration_minutes * 60
    action = actions.propose(requesting_user, "event", {
        "title": title.strip()[:200], "description": (description or "").strip()[:2000],
        "start_ts": start_ts, "end_ts": end_ts, "start_iso": timeutil.iso(start_ts), "end_iso": timeutil.iso(end_ts),
        "when_label": timeutil.label(start_ts), "duration_minutes": duration_minutes, "attendees": attendees,
    })
    return {"action_id": action["id"], "status": "pending_approval",
            "note": "Shown to the user with an Add to calendar button. NOT created yet; invitations go out only after they confirm."}
