"""
Chat history store: the data behind the left sidebar (recent chats, pinned,
search) and the "Customize Kacy" settings.

Why SQLite: it is in Python's standard library, needs no server, and the SQL is
the same shape we'd run on Azure SQL / Postgres later. Only the connection line
in `_connect` would change.

Every query filters on `owner` (the verified email). A chat id alone is never
enough to read a chat, so one user can't open another user's history by guessing
ids. This is the same "identity comes from the session, not the request" rule
the tools follow.
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
import uuid
from pathlib import Path

DB_PATH = Path(os.environ.get("KACY_DB", Path(__file__).resolve().parent.parent / "data" / "kacy.db"))

_SCHEMA = """
CREATE TABLE IF NOT EXISTS chats (
    id           TEXT PRIMARY KEY,
    owner        TEXT NOT NULL,
    title        TEXT NOT NULL,
    agent        TEXT NOT NULL DEFAULT 'kacy',
    conversation TEXT,                       -- Foundry conversation id, set on first model call
    pinned       INTEGER NOT NULL DEFAULT 0,
    created      REAL NOT NULL,
    updated      REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS chats_owner ON chats(owner, updated DESC);

CREATE TABLE IF NOT EXISTS messages (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id TEXT NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
    role    TEXT NOT NULL,                   -- 'user' | 'assistant'
    content TEXT NOT NULL,
    meta    TEXT NOT NULL DEFAULT '{}',      -- JSON: activity steps, draft snapshots
    created REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS messages_chat ON messages(chat_id, id);

CREATE TABLE IF NOT EXISTS settings (
    owner TEXT PRIMARY KEY,
    data  TEXT NOT NULL
);
"""


class _Conn(sqlite3.Connection):
    """`with _connect() as db:` commits on success AND closes, so no connection leaks."""

    def __exit__(self, *exc):
        try:
            return super().__exit__(*exc)
        finally:
            self.close()


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, factory=_Conn)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(_SCHEMA)
    return conn


def _like(text: str) -> str:
    """Escape LIKE wildcards so searching for '100%' matches the text, not everything."""
    return "%" + text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def _title_from(message: str) -> str:
    one_line = " ".join(message.split())
    return one_line if len(one_line) <= 48 else one_line[:47].rstrip() + "…"


# ---------- chats ----------

def create_chat(owner: str, first_message: str, agent: str) -> str:
    chat_id = uuid.uuid4().hex
    now = time.time()
    with _connect() as db:
        db.execute(
            "INSERT INTO chats (id, owner, title, agent, created, updated) VALUES (?,?,?,?,?,?)",
            (chat_id, owner, _title_from(first_message), agent, now, now),
        )
    return chat_id


def get_chat(owner: str, chat_id: str) -> dict | None:
    with _connect() as db:
        row = db.execute("SELECT * FROM chats WHERE id=? AND owner=?", (chat_id, owner)).fetchone()
    return dict(row) if row else None


def list_chats(owner: str, query: str | None = None, limit: int = 200) -> list[dict]:
    sql = "SELECT id, title, agent, pinned, updated FROM chats WHERE owner=?"
    args: list = [owner]
    if query:
        pattern = _like(query)
        sql += (
            " AND (title LIKE ? ESCAPE '\\' OR EXISTS ("
            "SELECT 1 FROM messages m WHERE m.chat_id = chats.id AND m.content LIKE ? ESCAPE '\\'))"
        )
        args += [pattern, pattern]
    sql += " ORDER BY pinned DESC, updated DESC LIMIT ?"
    args.append(limit)
    with _connect() as db:
        return [dict(r) for r in db.execute(sql, args).fetchall()]


def update_chat(owner: str, chat_id: str, *, pinned: bool | None = None, title: str | None = None) -> bool:
    sets, args = [], []
    if pinned is not None:
        sets.append("pinned=?")
        args.append(1 if pinned else 0)
    if title is not None:
        sets.append("title=?")
        args.append(title.strip()[:80] or "Untitled chat")
    if not sets:
        return False
    with _connect() as db:
        cur = db.execute(f"UPDATE chats SET {', '.join(sets)} WHERE id=? AND owner=?", [*args, chat_id, owner])
    return cur.rowcount > 0


def set_conversation(owner: str, chat_id: str, conversation: str) -> None:
    with _connect() as db:
        db.execute("UPDATE chats SET conversation=? WHERE id=? AND owner=?", (conversation, chat_id, owner))


def delete_chat(owner: str, chat_id: str) -> bool:
    with _connect() as db:
        cur = db.execute("DELETE FROM chats WHERE id=? AND owner=?", (chat_id, owner))
    return cur.rowcount > 0


# ---------- messages ----------

def add_message(owner: str, chat_id: str, role: str, content: str, meta: dict | None = None) -> None:
    now = time.time()
    with _connect() as db:
        # The owner check lives in the WHERE of the UPDATE; insert only if it matched.
        cur = db.execute("UPDATE chats SET updated=? WHERE id=? AND owner=?", (now, chat_id, owner))
        if cur.rowcount:
            db.execute(
                "INSERT INTO messages (chat_id, role, content, meta, created) VALUES (?,?,?,?,?)",
                (chat_id, role, content, json.dumps(meta or {}), now),
            )


def messages(owner: str, chat_id: str) -> list[dict]:
    with _connect() as db:
        rows = db.execute(
            "SELECT m.id, m.role, m.content, m.meta, m.created FROM messages m "
            "JOIN chats c ON c.id = m.chat_id WHERE m.chat_id=? AND c.owner=? ORDER BY m.id",
            (chat_id, owner),
        ).fetchall()
    return [{**dict(r), "meta": json.loads(r["meta"])} for r in rows]


def drop_last_assistant(owner: str, chat_id: str) -> str | None:
    """For 'regenerate': remove the last answer, return the user message that preceded it."""
    msgs = messages(owner, chat_id)
    if len(msgs) < 2 or msgs[-1]["role"] != "assistant":
        return None
    with _connect() as db:
        db.execute("DELETE FROM messages WHERE id=?", (msgs[-1]["id"],))
    return msgs[-2]["content"]


# ---------- settings ----------

DEFAULT_SETTINGS = {"display_name": "", "tone": "balanced", "instructions": ""}
TONES = {"balanced", "concise", "detailed", "friendly"}


def get_settings(owner: str) -> dict:
    with _connect() as db:
        row = db.execute("SELECT data FROM settings WHERE owner=?", (owner,)).fetchone()
    return {**DEFAULT_SETTINGS, **(json.loads(row["data"]) if row else {})}


def save_settings(owner: str, data: dict) -> dict:
    clean = {
        "display_name": str(data.get("display_name", ""))[:60].strip(),
        "tone": data.get("tone") if data.get("tone") in TONES else "balanced",
        "instructions": str(data.get("instructions", ""))[:1000].strip(),
    }
    with _connect() as db:
        db.execute(
            "INSERT INTO settings (owner, data) VALUES (?,?) "
            "ON CONFLICT(owner) DO UPDATE SET data=excluded.data",
            (owner, json.dumps(clean)),
        )
    return clean
