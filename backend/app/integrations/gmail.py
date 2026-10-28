"""
Gmail as the signed-in user: send one email, search the inbox, read one message.

Gmail's send endpoint takes a complete email (headers + body) encoded as
base64url in a field called "raw". Python's EmailMessage builds that email for
us and refuses header values containing line breaks, which blocks header
injection (someone smuggling an extra "Bcc:" line into the subject).
"""
from __future__ import annotations

import base64
import re
from email.message import EmailMessage

import httpx

from app.integrations._google import check

SEND_URL = "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"
_EMAIL_RE = re.compile(r"^[^@\s,;<>]+@[^@\s,;<>]+\.[^@\s,;<>]+$")


def send(access_token: str, to: str, subject: str, body: str) -> dict:
    if not _EMAIL_RE.match(to):
        raise ValueError(f"not a single valid email address: {to!r}")

    msg = EmailMessage()
    msg["To"] = to
    msg["Subject"] = subject  # From is filled in by Gmail: the signed-in account
    msg.set_content(body)

    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    resp = httpx.post(
        SEND_URL,
        headers={"Authorization": f"Bearer {access_token}"},
        json={"raw": raw},
        timeout=15,
    )
    return check(resp, "Gmail")  # contains the Gmail message id


# ---------- reading (needs the gmail.readonly scope) ----------

API = "https://gmail.googleapis.com/gmail/v1/users/me"


def _get(access_token: str, path: str, params: dict) -> dict:
    resp = httpx.get(f"{API}/{path}", headers={"Authorization": f"Bearer {access_token}"}, params=params, timeout=15)
    return check(resp, "Gmail")


def _header(msg: dict, name: str) -> str:
    for h in msg.get("payload", {}).get("headers", []):
        if h["name"].lower() == name.lower():
            return h["value"]
    return ""


def search(access_token: str, query: str, max_results: int = 8) -> list[dict]:
    """Gmail search syntax works as-is, e.g. 'is:unread', 'from:dana', 'newer_than:2d'."""
    listing = _get(access_token, "messages", {"q": query, "maxResults": max_results})
    out = []
    for ref in listing.get("messages", []):
        msg = _get(access_token, f"messages/{ref['id']}", {
            "format": "metadata", "metadataHeaders": ["From", "Subject", "Date"]})
        out.append({
            "id": msg["id"], "from": _header(msg, "From"), "subject": _header(msg, "Subject"),
            "date": _header(msg, "Date"), "snippet": msg.get("snippet", ""),
            "unread": "UNREAD" in msg.get("labelIds", []),
        })
    return out


def _text_of(payload: dict) -> str:
    """Pull the plain-text part out of a (possibly multipart) message."""
    mime = payload.get("mimeType", "")
    data = payload.get("body", {}).get("data")
    if mime == "text/plain" and data:
        return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode("utf-8", "replace")
    for part in payload.get("parts", []) or []:
        found = _text_of(part)
        if found:
            return found
    if mime == "text/html" and data:  # last resort: strip tags
        html = base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode("utf-8", "replace")
        return re.sub(r"<[^>]+>", " ", html)
    return ""


def read(access_token: str, message_id: str, max_chars: int = 4000) -> dict:
    if not re.fullmatch(r"[0-9a-fA-F]{6,32}", message_id):
        raise ValueError("invalid message id")
    msg = _get(access_token, f"messages/{message_id}", {"format": "full"})
    body = " ".join(_text_of(msg.get("payload", {})).split()) or msg.get("snippet", "")
    return {"id": msg["id"], "from": _header(msg, "From"), "to": _header(msg, "To"),
            "subject": _header(msg, "Subject"), "date": _header(msg, "Date"), "body": body[:max_chars]}
