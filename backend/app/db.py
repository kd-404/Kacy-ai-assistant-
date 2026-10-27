"""
Tables for the company systems Kacy can act on: the approval queue, demo
mailbox/calendar/drive, leave requests and IT tickets.

Same SQLite file as chat history (see chats.py). Each table here is what a real
system would own: Gmail/Calendar/Drive are Google's, leave requests are the HR
system's, tickets are Jira's. Keeping them behind small functions means swapping
in the real thing later only touches those functions.
"""
from __future__ import annotations

import json
import sqlite3
import time

from app.chats import DB_PATH

_SCHEMA = """
CREATE TABLE IF NOT EXISTS actions (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    owner    TEXT NOT NULL,
    kind     TEXT NOT NULL,              -- email | event | document | leave_request | leave_decision | ticket
    payload  TEXT NOT NULL,              -- JSON the executor needs
    status   TEXT NOT NULL DEFAULT 'pending_approval',  -- pending_approval | done | discarded | failed
    result   TEXT NOT NULL DEFAULT '{}', -- JSON: what happened (message, link, ids)
    created  REAL NOT NULL,
    decided  REAL
);
CREATE INDEX IF NOT EXISTS actions_owner ON actions(owner, status);

CREATE TABLE IF NOT EXISTS demo_emails (
    id INTEGER PRIMARY KEY AUTOINCREMENT, owner TEXT NOT NULL, sender TEXT NOT NULL,
    subject TEXT NOT NULL, date REAL NOT NULL, body TEXT NOT NULL, unread INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS demo_emails_owner ON demo_emails(owner, date DESC);

CREATE TABLE IF NOT EXISTS demo_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT, owner TEXT NOT NULL, title TEXT NOT NULL,
    start REAL NOT NULL, end REAL NOT NULL, attendees TEXT NOT NULL DEFAULT '[]', location TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS demo_events_owner ON demo_events(owner, start);

CREATE TABLE IF NOT EXISTS documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    owner TEXT NOT NULL,                 -- an email, or 'company' for shared demo docs
    title TEXT NOT NULL, content TEXT NOT NULL, url TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL,                -- demo | google
    created REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS leave_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT, requester TEXT NOT NULL,
    start_date TEXT NOT NULL, end_date TEXT NOT NULL, days INTEGER NOT NULL, reason TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending',      -- pending | approved | rejected
    stages TEXT NOT NULL,                        -- JSON list of roles that must approve, in order
    stage INTEGER NOT NULL DEFAULT 0,
    history TEXT NOT NULL DEFAULT '[]',          -- JSON list of {by, role, decision, comment, at}
    created REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS tickets (
    id INTEGER PRIMARY KEY AUTOINCREMENT, requester TEXT NOT NULL, summary TEXT NOT NULL,
    details TEXT NOT NULL DEFAULT '', priority TEXT NOT NULL DEFAULT 'normal',
    status TEXT NOT NULL DEFAULT 'open', created REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS seeded (owner TEXT PRIMARY KEY);
"""


class _Conn(sqlite3.Connection):
    """`with connect() as db:` commits on success AND closes, so no connection leaks."""

    def __exit__(self, *exc):
        try:
            return super().__exit__(*exc)
        finally:
            self.close()


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, factory=_Conn)
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    return conn


def rows(sql: str, args: tuple | list = ()) -> list[dict]:
    with connect() as db:
        return [dict(r) for r in db.execute(sql, args).fetchall()]


def one(sql: str, args: tuple | list = ()) -> dict | None:
    found = rows(sql, args)
    return found[0] if found else None


def run(sql: str, args: tuple | list = ()) -> int:
    """Execute a write; returns the new row id (for INSERT) or rows changed."""
    with connect() as db:
        cur = db.execute(sql, args)
        return cur.lastrowid if sql.lstrip().upper().startswith("INSERT") else cur.rowcount


def dumps(value) -> str:
    return json.dumps(value, ensure_ascii=False)


def now() -> float:
    return time.time()
