"""
Document tools: draft a new document, find and read existing ones.

draft_document queues an approval card; the Google Doc is created only after the
person confirms. Reading covers the shared company documents plus anything Kacy
created for this person. (With the non-sensitive drive.file scope Kacy cannot see
the rest of a person's Drive, by design.)
"""
from __future__ import annotations

from app import actions, db, demo_seed
from app.tools.context import UNTRUSTED_NOTE


def draft_document(requesting_user: str, title: str, content: str, *, session=None) -> dict:
    action = actions.propose(requesting_user, "document", {
        "title": title.strip()[:200], "content": content.strip()[:20000]})
    return {"action_id": action["id"], "status": "pending_approval",
            "note": "Shown to the user with a Create document button. NOT created yet."}


def list_documents(requesting_user: str, query: str, *, session=None) -> dict:
    demo_seed.ensure_seeded(requesting_user)
    like = f"%{query.strip().lower()}%"
    rows = db.rows(
        "SELECT id, title, owner, source, url FROM documents WHERE (owner=? OR owner='company') "
        "AND (lower(title) LIKE ? OR lower(content) LIKE ?) ORDER BY id DESC LIMIT 10",
        (requesting_user, like, like))
    return {"documents": [{"id": r["id"], "title": r["title"],
                           "shared_company_doc": r["owner"] == "company"} for r in rows]}


def read_document(requesting_user: str, document_id: int, *, session=None) -> dict:
    row = db.one("SELECT * FROM documents WHERE id=? AND (owner=? OR owner='company')", (document_id, requesting_user))
    if row is None:
        return {"error": "no such document"}
    return {"id": row["id"], "title": row["title"], "content": row["content"][:6000], "note": UNTRUSTED_NOTE}
