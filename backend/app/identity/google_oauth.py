"""
"Sign in with Google" for Kacy: one login that answers two questions.

  1. WHO is this?   Google returns a signed ID token. We verify the signature,
                    read the email from it, and look that email up in the role
                    table (rbac.py). That role is what RBAC enforces.
  2. WHAT can Kacy do as them?  The user grants a few Google permissions (send
                    mail, read the inbox, see and add calendar events, create
                    documents Kacy makes itself). Google gives us an access
                    token for those, used when a tool reads something or when
                    the user presses the button on an approval card.

The flow (standard OAuth 2.0 "authorization code" flow):
  browser -> /auth/google/login -> Google consent screen -> back to
  /auth/google/callback?code=...  -> we swap the code for tokens (server side,
  using our client secret) -> verify the ID token -> create a session cookie.

Sessions live in memory (fine for a single-process demo; a real deployment
would use Redis or a database, and encrypt the stored tokens).
"""
from __future__ import annotations

import os
import secrets
import time
from dataclasses import dataclass
from urllib.parse import urlencode

import httpx
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
_GOOGLE = "https://www.googleapis.com/auth/"
# Narrowest scopes that do the job: send only (not compose), calendar events + free/busy,
# and drive.file, which only reaches files Kacy itself created.
SCOPES = " ".join(["openid", "email", "profile"] + [_GOOGLE + s for s in (
    "gmail.send", "gmail.readonly", "calendar.events", "calendar.freebusy", "drive.file")])
SESSION_COOKIE = "kacy_session"


@dataclass
class Session:
    email: str
    name: str
    access_token: str
    refresh_token: str | None
    expires_at: float
    scopes: str = ""        # what the person actually granted (they can untick boxes on Google's screen)
    demo: bool = False      # demo accounts have no Google access; tools use the seeded demo data


_sessions: dict[str, Session] = {}
_pending_states: dict[str, float] = {}  # anti-CSRF "state" values we issued -> issue time


def _cfg(key: str) -> str:
    value = os.environ.get(key)
    if not value:
        raise RuntimeError(f"{key} is not set in .env (see .env.example)")
    return value


def login_url() -> str:
    """Build the Google consent-screen URL. `state` proves the callback is a reply to us."""
    state = secrets.token_urlsafe(24)
    _pending_states[state] = time.time()
    params = {
        "client_id": _cfg("GOOGLE_CLIENT_ID"),
        "redirect_uri": _cfg("GOOGLE_REDIRECT_URI"),
        "response_type": "code",
        "scope": SCOPES,
        "access_type": "offline",  # also give us a refresh token
        "prompt": "consent",  # always show consent, so the refresh token is issued
        "state": state,
    }
    return f"{AUTH_URL}?{urlencode(params)}"


def complete_login(code: str, state: str) -> tuple[str, Session]:
    """Handle Google's redirect back to us. Returns (session_id, session)."""
    issued = _pending_states.pop(state, None)
    if issued is None or time.time() - issued > 600:
        raise PermissionError("invalid or expired login state; start sign-in again")

    resp = httpx.post(
        TOKEN_URL,
        data={
            "code": code,
            "client_id": _cfg("GOOGLE_CLIENT_ID"),
            "client_secret": _cfg("GOOGLE_CLIENT_SECRET"),
            "redirect_uri": _cfg("GOOGLE_REDIRECT_URI"),
            "grant_type": "authorization_code",
        },
        timeout=15,
    )
    resp.raise_for_status()
    tokens = resp.json()

    # Verify the ID token's signature and audience with Google's public keys.
    # Never trust an email that hasn't been through this check.
    claims = id_token.verify_oauth2_token(
        tokens["id_token"], google_requests.Request(), _cfg("GOOGLE_CLIENT_ID")
    )
    if not claims.get("email_verified"):
        raise PermissionError("Google account email is not verified")

    session = Session(
        email=claims["email"].lower(),
        name=claims.get("name", claims["email"]),
        access_token=tokens["access_token"],
        refresh_token=tokens.get("refresh_token"),
        expires_at=time.time() + int(tokens.get("expires_in", 3600)) - 60,
        scopes=tokens.get("scope", ""),
    )
    session_id = secrets.token_urlsafe(32)
    _sessions[session_id] = session
    return session_id, session


def create_demo_session(email: str, name: str) -> tuple[str, Session]:
    """A session for a demo persona: signed in to Kacy, but with no Google access at all."""
    session = Session(email=email, name=name, access_token="", refresh_token=None, expires_at=0, demo=True)
    session_id = secrets.token_urlsafe(32)
    _sessions[session_id] = session
    return session_id, session


def get_session(session_id: str | None) -> Session | None:
    return _sessions.get(session_id) if session_id else None


def end_session(session_id: str | None) -> None:
    if session_id:
        _sessions.pop(session_id, None)


def fresh_access_token(session: Session) -> str:
    """Access tokens last ~1 hour; use the refresh token to get a new one when needed."""
    if session.demo:
        raise PermissionError("demo accounts have no Google access")
    if time.time() < session.expires_at:
        return session.access_token
    if not session.refresh_token:
        raise PermissionError("Google access expired; sign in again")

    resp = httpx.post(
        TOKEN_URL,
        data={
            "client_id": _cfg("GOOGLE_CLIENT_ID"),
            "client_secret": _cfg("GOOGLE_CLIENT_SECRET"),
            "refresh_token": session.refresh_token,
            "grant_type": "refresh_token",
        },
        timeout=15,
    )
    resp.raise_for_status()
    tokens = resp.json()
    session.access_token = tokens["access_token"]
    session.expires_at = time.time() + int(tokens.get("expires_in", 3600)) - 60
    return session.access_token
