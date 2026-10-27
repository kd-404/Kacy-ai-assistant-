"""
Time helpers shared by the calendar tools.

Everything is stored as UTC epoch seconds and shown in one company timezone
(KACY_TZ, default Asia/Kolkata). Working hours decide which meeting slots Kacy
will suggest.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

WORK_START, WORK_END = 9, 18          # local hours
SLOT_STEP_MIN = 30


def tz() -> ZoneInfo:
    return ZoneInfo(os.environ.get("KACY_TZ", "Asia/Kolkata"))


def now_local() -> datetime:
    return datetime.now(timezone.utc).astimezone(tz())


def to_local(ts: float) -> datetime:
    return datetime.fromtimestamp(ts, timezone.utc).astimezone(tz())


def label(ts: float) -> str:
    """'Wed 29 Oct, 15:30' in the company timezone."""
    d = to_local(ts)
    return f"{d.strftime('%a')} {d.day} {d.strftime('%b')}, {d.strftime('%H:%M')}"


def parse_when(text: str) -> float:
    """ISO 8601 -> epoch seconds. A time with no offset is read in the company timezone."""
    d = datetime.fromisoformat(text.strip().replace("Z", "+00:00"))
    if d.tzinfo is None:
        d = d.replace(tzinfo=tz())
    return d.timestamp()


def iso(ts: float) -> str:
    return to_local(ts).isoformat(timespec="minutes")


def free_slots(busy: list[tuple[float, float]], duration_min: int, days_ahead: int,
               now_ts: float | None = None, limit: int = 5) -> list[float]:
    """
    First `limit` start times (epoch) inside working hours, Mon-Fri, where the whole
    meeting fits between busy intervals. `busy` is the merged calendar load of everyone
    invited. Starts on SLOT_STEP_MIN boundaries, at least an hour from now.
    """
    now_ts = now_ts if now_ts is not None else datetime.now(timezone.utc).timestamp()
    earliest = now_ts + 3600
    dur = duration_min * 60
    out: list[float] = []
    day0 = to_local(now_ts).replace(hour=0, minute=0, second=0, microsecond=0)
    for offset in range(days_ahead + 1):
        day = day0 + timedelta(days=offset)
        if day.weekday() >= 5:
            continue
        start = day.replace(hour=WORK_START).timestamp()
        end = day.replace(hour=WORK_END).timestamp()
        t = max(start, earliest)
        # round up to the next slot boundary
        step = SLOT_STEP_MIN * 60
        t = ((t - start + step - 1) // step) * step + start
        while t + dur <= end:
            if not any(b0 < t + dur and t < b1 for b0, b1 in busy):
                out.append(t)
                if len(out) >= limit:
                    return out
            t += step
    return out
