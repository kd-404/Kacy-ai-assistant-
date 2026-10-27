"""
Browser-facing routes: Google sign-in and the draft approval endpoints.
(The chat UI itself is served from main.py; history/settings routes are in api.py.)

Who is the caller?  current_user() answers that for every route:
  - Signed in with Google -> the verified email from Google (the real path).
  - Not signed in, local dev only -> a demo account named in the request body
    (alice@kacy-demo.test etc.), so curl tests keep working. Demo accounts can
    draft but can never send: sending needs a real Google session.
"""
from __future__ import annotations

import logging
import os

import httpx
from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import RedirectResponse

from app.identity import google_oauth
from app.identity.google_oauth import SESSION_COOKIE, Session
from app.identity.rbac import AccessDenied, is_demo_user, role_for
from app.integrations import gmail
from app.tools.mcp_email import server as email_store

logger = logging.getLogger("kacy.web")
router = APIRouter()


def current_user(request: Request, claimed_email: str | None = None) -> tuple[str, Session | None]:
    session = google_oauth.get_session(request.cookies.get(SESSION_COOKIE))
    if session is not None:
        return session.email, session
    dev = os.environ.get("ENVIRONMENT", "dev") == "dev"
    if dev and claimed_email and is_demo_user(claimed_email):
        return claimed_email.lower(), None
    raise HTTPException(status_code=401, detail="Sign in with Google first")


def _require_app_header(x_kacy: str | None) -> None:
    # Browsers can't add custom headers to cross-site form posts, so requiring
    # one on state-changing routes blocks cross-site request forgery.
    if x_kacy != "1":
        raise HTTPException(status_code=400, detail="missing X-Kacy header")


def _public(draft: dict) -> dict:
    return {k: draft[k] for k in ("id", "to", "subject", "body", "status")}


# ---------- Google sign-in ----------

@router.get("/auth/google/login")
def google_login() -> RedirectResponse:
    return RedirectResponse(google_oauth.login_url())


@router.get("/auth/google/callback")
def google_callback(code: str, state: str) -> RedirectResponse:
    try:
        session_id, session = google_oauth.complete_login(code, state)
    except PermissionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    try:
        role = role_for(session.email)
    except AccessDenied:
        google_oauth.end_session(session_id)
        raise HTTPException(
            status_code=403,
            detail=f"{session.email} is not a Kacy user. Add it to KACY_USER_ROLES in .env.",
        )

    logger.info("auth.login email=%s role=%s", session.email, role.value)
    resp = RedirectResponse("/", status_code=303)
    resp.set_cookie(SESSION_COOKIE, session_id, httponly=True, secure=True, samesite="lax", max_age=8 * 3600)
    return resp


@router.post("/auth/logout")
def logout(request: Request, x_kacy: str | None = Header(default=None)) -> dict:
    _require_app_header(x_kacy)
    google_oauth.end_session(request.cookies.get(SESSION_COOKIE))
    return {"ok": True}


@router.get("/me")
def me(request: Request) -> dict:
    session = google_oauth.get_session(request.cookies.get(SESSION_COOKIE))
    if session is None:
        return {"signed_in": False}
    return {"signed_in": True, "email": session.email, "name": session.name,
            "role": role_for(session.email).value}


# ---------- Drafts: the human approval step ----------

@router.get("/drafts")
def list_drafts(request: Request, user_email: str | None = None) -> list[dict]:
    owner, _ = current_user(request, user_email)
    return [_public(d) for d in email_store.pending_for(owner)]


def _owned_pending_draft(draft_id: int, owner: str) -> dict:
    draft = email_store.get_draft(draft_id)
    # Same 404 whether it doesn't exist or belongs to someone else: don't reveal which.
    if draft is None or draft["owner"] != owner:
        raise HTTPException(status_code=404, detail="no such draft")
    if draft["status"] != "pending_approval":
        raise HTTPException(status_code=409, detail=f"draft is already {draft['status']}")
    return draft


@router.post("/drafts/{draft_id}/send")
def send_draft(draft_id: int, request: Request, x_kacy: str | None = Header(default=None)) -> dict:
    """The ONLY path that sends email. Requires a real Google session, and the draft's owner."""
    _require_app_header(x_kacy)
    session = google_oauth.get_session(request.cookies.get(SESSION_COOKIE))
    if session is None:
        raise HTTPException(status_code=401, detail="Sign in with Google to send email")

    draft = _owned_pending_draft(draft_id, session.email)
    try:
        sent = gmail.send(google_oauth.fresh_access_token(session), draft["to"], draft["subject"], draft["body"])
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except (PermissionError, httpx.HTTPError) as exc:
        logger.warning("gmail.send_failed draft=%s err=%s", draft_id, exc)
        raise HTTPException(status_code=502, detail=f"Gmail send failed: {exc}") from exc

    email_store.mark(draft_id, "sent", gmail_message_id=sent.get("id"))
    logger.info("gmail.sent draft=%s by=%s to=%s", draft_id, session.email, draft["to"])
    return {"status": "sent", "gmail_message_id": sent.get("id")}


@router.post("/drafts/{draft_id}/discard")
def discard_draft(draft_id: int, request: Request, user_email: str | None = None,
                  x_kacy: str | None = Header(default=None)) -> dict:
    _require_app_header(x_kacy)
    owner, _ = current_user(request, user_email)
    _owned_pending_draft(draft_id, owner)
    email_store.mark(draft_id, "discarded")
    return {"status": "discarded"}
