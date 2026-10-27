"""
Google Calendar as the signed-in user: list events, check free/busy, create an event.
Scopes: calendar.events (list + create) and calendar.freebusy (availability).
"""
from __future__ import annotations

import uuid

import httpx

API = "https://www.googleapis.com/calendar/v3"


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _check(resp: httpx.Response) -> dict:
    if resp.status_code == 403:
        raise PermissionError("Calendar permission not granted; sign out and sign in again, and allow calendar access")
    resp.raise_for_status()
    return resp.json()


def list_events(token: str, time_min_iso: str, time_max_iso: str, limit: int = 20) -> list[dict]:
    data = _check(httpx.get(
        f"{API}/calendars/primary/events", headers=_auth(token), timeout=15,
        params={"timeMin": time_min_iso, "timeMax": time_max_iso, "singleEvents": "true",
                "orderBy": "startTime", "maxResults": limit}))
    return [{
        "title": e.get("summary", "(no title)"),
        "start": e["start"].get("dateTime", e["start"].get("date")),
        "end": e["end"].get("dateTime", e["end"].get("date")),
        "attendees": [a["email"] for a in e.get("attendees", [])],
        "location": e.get("location", ""),
    } for e in data.get("items", [])]


def busy(token: str, emails: list[str], time_min_iso: str, time_max_iso: str) -> tuple[list[tuple[str, str]], list[str]]:
    """
    Busy intervals for the user ('primary') plus any invitees, and the invitees whose calendars
    we could not read. Google only shares free/busy for people in the same organisation or who
    shared their calendar, so outside addresses usually come back unreadable.
    """
    items = [{"id": "primary"}] + [{"id": e} for e in emails]
    data = _check(httpx.post(
        f"{API}/freeBusy", headers=_auth(token), timeout=15,
        json={"timeMin": time_min_iso, "timeMax": time_max_iso, "items": items}))
    intervals, unreadable = [], []
    for cal_id, info in data.get("calendars", {}).items():
        if info.get("errors"):
            unreadable.append(cal_id)
        intervals += [(b["start"], b["end"]) for b in info.get("busy", [])]
    return intervals, unreadable


def create_event(token: str, title: str, start_iso: str, end_iso: str, attendees: list[str],
                 description: str = "", time_zone: str = "UTC") -> dict:
    """Creates the event, adds a Google Meet link, and emails invitations to attendees."""
    body = {
        "summary": title, "description": description,
        "start": {"dateTime": start_iso, "timeZone": time_zone},
        "end": {"dateTime": end_iso, "timeZone": time_zone},
        "attendees": [{"email": a} for a in attendees],
        "conferenceData": {"createRequest": {
            "requestId": uuid.uuid4().hex, "conferenceSolutionKey": {"type": "hangoutsMeet"}}},
    }
    data = _check(httpx.post(
        f"{API}/calendars/primary/events", headers=_auth(token), timeout=20,
        params={"sendUpdates": "all", "conferenceDataVersion": 1}, json=body))
    return {"id": data["id"], "link": data.get("htmlLink", ""), "meet": data.get("hangoutLink", "")}
