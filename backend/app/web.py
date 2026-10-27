"""
Browser-facing routes: Google sign-in and demo sign-in.
(The chat UI is served from main.py; history, settings and approval routes are in api.py.)

Who is the caller?  current_user() answers that for every route:
  - Signed in with Google -> the verified email from Google (the real path).
  - Not signed in, local dev only -> a demo account named in the request body
    (alice@kacy-demo.test etc.), so curl tests keep working. Demo accounts can
    draft but can never send: sending needs a real Google session.
"""
from __future__ import annotations

import logging
import os

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel

from app.identity import google_oauth
from app.identity.google_oauth import SESSION_COOKIE, Session
from app.identity.rbac import DEMO_USERS, AccessDenied, is_demo_user, role_for

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


class DemoLogin(BaseModel):
    persona: str


@router.post("/auth/demo")
def demo_login(body: DemoLogin, x_kacy: str | None = Header(default=None)):
    """Sign in as one of the four demo people. No Google involved, and no Google access either."""
    _require_app_header(x_kacy)
    email = f"{body.persona.lower()}@kacy-demo.test"
    if email not in DEMO_USERS:
        raise HTTPException(status_code=404, detail="no such demo account")
    session_id, session = google_oauth.create_demo_session(email, body.persona.capitalize())
    resp = JSONResponse({"ok": True})
    resp.set_cookie(SESSION_COOKIE, session_id, httponly=True, secure=True, samesite="lax", max_age=8 * 3600)
    logger.info("auth.demo_login email=%s role=%s", email, role_for(email).value)
    return resp


@router.get("/api/personas")
def personas() -> list[dict]:
    return [{"id": e.split("@")[0], "role": r.value} for e, r in DEMO_USERS.items()]


@router.get("/me")
def me(request: Request) -> dict:
    session = google_oauth.get_session(request.cookies.get(SESSION_COOKIE))
    if session is None:
        return {"signed_in": False}
    return {"signed_in": True, "email": session.email, "name": session.name,
            "role": role_for(session.email).value, "demo": session.demo,
            "scopes": sorted(s.rsplit("/", 1)[-1] for s in session.scopes.split() if "googleapis.com/auth/" in s)}
