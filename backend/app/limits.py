"""
Usage limits for the public demo.

Anyone on the internet can sign in as a demo person, and every message costs real
money on Azure. Three guards keep the bill bounded:
  1. Per visitor: a few messages per hour and per day, counted by the visitor's
     network address (not by session, because a new session is one click away).
  2. Global: one daily cap across all demo visitors. When it is used up, the demo
     pauses until tomorrow (UTC) and the owner (Google sign-in) keeps working.
  3. Kill switch: set KACY_DEMO_PAUSED=1 and the demo stops at once.

State is in memory, which is fine because the app runs as a single copy. A restart
resets the counters, which is acceptable for a demo-sized bill.
All numbers can be changed with environment variables.
"""
from __future__ import annotations

import os
import threading
import time
from collections import defaultdict, deque



class LimitError(Exception):
    """A demo limit was hit. The web layer turns this into an HTTP error (429 / 503)."""

    def __init__(self, status_code: int, detail: str):
        self.status_code, self.detail = status_code, detail
        super().__init__(detail)

_lock = threading.Lock()
_hits: dict[str, deque] = defaultdict(deque)   # visitor -> timestamps of recent messages
_logins: dict[str, deque] = defaultdict(deque)
_day = {"key": "", "count": 0}


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


def visitor_id(request) -> str:
    """Container Apps puts the real client address first in X-Forwarded-For."""
    fwd = request.headers.get("x-forwarded-for", "")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _prune(q: deque, window: float, now: float) -> None:
    while q and now - q[0] > window:
        q.popleft()


def _today(now: float) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(now))


def check_demo_message(visitor: str, now: float | None = None) -> None:
    """Call before every demo chat turn. Raises 429/503 with a message the UI can show."""
    now = time.time() if now is None else now
    if os.environ.get("KACY_DEMO_PAUSED") == "1":
        raise LimitError(503, detail="The public demo is paused right now.")
    per_hour, per_day = _int("KACY_DEMO_PER_HOUR", 15), _int("KACY_DEMO_PER_DAY", 40)
    daily_cap = _int("KACY_DEMO_DAILY_CAP", 300)
    with _lock:
        day = _today(now)
        if _day["key"] != day:
            _day["key"], _day["count"] = day, 0
        if _day["count"] >= daily_cap:
            raise LimitError(429, detail="The demo has reached today's limit. Please try again tomorrow.")
        q = _hits[visitor]
        _prune(q, 86400, now)
        if len(q) >= per_day:
            raise LimitError(429, detail=f"Demo limit reached: {per_day} messages a day per visitor.")
        if sum(1 for t in q if now - t <= 3600) >= per_hour:
            raise LimitError(429, detail=f"Slow down a little: {per_hour} messages an hour per visitor in the demo.")
        q.append(now)
        _day["count"] += 1


def check_demo_login(visitor: str, now: float | None = None) -> None:
    """Demo sign-ins are cheap, but each one keeps a session in memory, so they are capped too."""
    now = time.time() if now is None else now
    if os.environ.get("KACY_DEMO_PAUSED") == "1":
        raise LimitError(503, detail="The public demo is paused right now.")
    limit = _int("KACY_DEMO_LOGINS_PER_HOUR", 30)
    with _lock:
        q = _logins[visitor]
        _prune(q, 3600, now)
        if len(q) >= limit:
            raise LimitError(429, detail="Too many demo sign-ins. Try again later.")
        q.append(now)


def reset() -> None:
    with _lock:
        _hits.clear(); _logins.clear(); _day.update(key="", count=0)
