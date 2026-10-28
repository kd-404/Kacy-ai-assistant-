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

-- Desk: a wire between two chats. `a` is the source, `b` the listener; with two_way
-- both read each other. Only briefs (short summaries) travel along a link.
CREATE TABLE IF NOT EXISTS chat_links (
    id      TEXT PRIMARY KEY,
    owner   TEXT NOT NULL,
    a       TEXT NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
    b       TEXT NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
    two_way INTEGER NOT NULL DEFAULT 0,
    created REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS links_owner ON chat_links(owner);
"""

# Columns added after the first release. CREATE TABLE IF NOT EXISTS won't add them to an
# existing database, so they are added here when missing.
_MIGRATIONS = {
    "brief": "ALTER TABLE chats ADD COLUMN brief TEXT NOT NULL DEFAULT ''",
    "desk": "ALTER TABLE chats ADD COLUMN desk TEXT NOT NULL DEFAULT '{}'",
}


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
    have = {r["name"] for r in conn.execute("PRAGMA table_info(chats)")}
    for column, ddl in _MIGRATIONS.items():
        if column not in have:
            conn.execute(ddl)
    conn.commit()
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


# ---------- Desk: briefs, window layout, links ----------

BRIEF_MAX = 700


def set_brief(owner: str, chat_id: str, brief: str) -> None:
    with _connect() as db:
        db.execute("UPDATE chats SET brief=? WHERE id=? AND owner=?", (brief.strip()[:BRIEF_MAX], chat_id, owner))


def get_brief(owner: str, chat_id: str) -> str:
    chat = get_chat(owner, chat_id)
    return chat["brief"] if chat else ""


def _clean_geom(g: dict) -> dict:
    """Window layout is just numbers and flags. Anything else a client sends is dropped."""
    def num(key, lo, hi):
        try:
            return max(lo, min(hi, float(g.get(key, lo))))
        except (TypeError, ValueError):
            return lo
    return {
        "open": bool(g.get("open", True)),
        "min": bool(g.get("min", False)),
        "x": num("x", 0, 20000), "y": num("y", 0, 20000),
        "w": num("w", 300, 2000), "h": num("h", 260, 2000),
        "z": int(num("z", 0, 100000)),
    }


def set_desk(owner: str, chat_id: str, geom: dict) -> bool:
    with _connect() as db:
        cur = db.execute("UPDATE chats SET desk=? WHERE id=? AND owner=?",
                         (json.dumps(_clean_geom(geom)), chat_id, owner))
    return cur.rowcount > 0


def desk_chats(owner: str) -> list[dict]:
    """Chats that have been put on the Desk, with a one-line snippet of the latest answer."""
    with _connect() as db:
        rows = db.execute(
            "SELECT id, title, agent, brief, desk, updated, "
            "(SELECT content FROM messages m WHERE m.chat_id = chats.id AND m.role='assistant' "
            " ORDER BY m.id DESC LIMIT 1) AS last "
            "FROM chats WHERE owner=? AND desk != '{}' ORDER BY updated DESC", (owner,)
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["desk"] = json.loads(d["desk"])
        d["last"] = " ".join((d["last"] or "").split())[:160]
        if d["desk"].get("open"):
            out.append(d)
    return out


def list_links(owner: str) -> list[dict]:
    with _connect() as db:
        rows = db.execute("SELECT id, a, b, two_way FROM chat_links WHERE owner=? ORDER BY created", (owner,)).fetchall()
    return [{**dict(r), "two_way": bool(r["two_way"])} for r in rows]


def add_link(owner: str, a: str, b: str, two_way: bool = False) -> dict:
    """Both chats must be the caller's own, different, and not already linked in either direction."""
    if a == b:
        raise ValueError("A chat can't be linked to itself.")
    if not (get_chat(owner, a) and get_chat(owner, b)):
        raise LookupError("no such chat")
    with _connect() as db:
        dup = db.execute(
            "SELECT 1 FROM chat_links WHERE owner=? AND ((a=? AND b=?) OR (a=? AND b=?))", (owner, a, b, b, a)
        ).fetchone()
        if dup:
            raise ValueError("These chats are already linked.")
        link_id = uuid.uuid4().hex
        db.execute("INSERT INTO chat_links (id, owner, a, b, two_way, created) VALUES (?,?,?,?,?,?)",
                   (link_id, owner, a, b, 1 if two_way else 0, time.time()))
    return {"id": link_id, "a": a, "b": b, "two_way": bool(two_way)}


def get_link(owner: str, link_id: str) -> dict | None:
    return next((l for l in list_links(owner) if l["id"] == link_id), None)


def update_link(owner: str, link_id: str, two_way: bool) -> bool:
    with _connect() as db:
        cur = db.execute("UPDATE chat_links SET two_way=? WHERE id=? AND owner=?", (1 if two_way else 0, link_id, owner))
    return cur.rowcount > 0


def remove_link(owner: str, link_id: str) -> bool:
    with _connect() as db:
        cur = db.execute("DELETE FROM chat_links WHERE id=? AND owner=?", (link_id, owner))
    return cur.rowcount > 0


def readable_from(owner: str, chat_id: str) -> list[dict]:
    """Chats whose brief `chat_id` may read: sources of links pointing at it, plus both ends of two-way links."""
    out = []
    for l in list_links(owner):
        other = None
        if l["b"] == chat_id:
            other = l["a"]
        elif l["a"] == chat_id and l["two_way"]:
            other = l["b"]
        chat = get_chat(owner, other) if other else None
        if chat:
            out.append({"id": chat["id"], "title": chat["title"], "brief": chat["brief"]})
    return out


def has_links(owner: str, chat_id: str) -> bool:
    return any(chat_id in (l["a"], l["b"]) for l in list_links(owner))
