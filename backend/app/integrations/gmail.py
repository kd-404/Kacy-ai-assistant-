"""
Send one email through the Gmail API, as the signed-in user.

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
    resp.raise_for_status()
    return resp.json()  # contains the Gmail message id
