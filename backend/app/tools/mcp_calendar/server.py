"""Calendar tool — propose slots, then book on approval. Second new agentic action."""
from __future__ import annotations

from app.tools.base import ToolStore

_events = ToolStore()

# Demo-only fixed availability per attendee, so slot-finding is deterministic for the demo.
_DEMO_AVAILABILITY = {
    "design-team": ["2026-11-02T10:00", "2026-11-02T14:00", "2026-11-03T09:30"],
}


def propose_slots(attendee_group: str, duration_minutes: int = 30) -> dict:
    slots = _DEMO_AVAILABILITY.get(attendee_group, [])
    return {"attendee_group": attendee_group, "duration_minutes": duration_minutes, "slots": slots}


def book_meeting(requesting_user: str, attendee_group: str, slot: str, title: str) -> dict:
    """Only call this after the user approves one of the proposed slots."""
    return _events.add({
        "organizer": requesting_user, "attendee_group": attendee_group, "slot": slot, "title": title,
    })
