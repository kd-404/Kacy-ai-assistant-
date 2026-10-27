"""
Browser-facing routes: Google sign-in, the draft approval endpoints, and a
small test page (GET /) until the React frontend is wired to this backend.

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
from fastapi.responses import HTMLResponse, RedirectResponse

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


# ---------- Test page ----------

@router.get("/", response_class=HTMLResponse)
def test_page() -> str:
    return _TEST_PAGE


_TEST_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Kacy test console</title>
<style>
 body{font:15px/1.5 system-ui,sans-serif;max-width:760px;margin:24px auto;padding:0 16px;background:#0d1014;color:#e7ebf0}
 a,button{font:inherit} button{background:#6c93ff;color:#fff;border:0;border-radius:6px;padding:7px 14px;cursor:pointer}
 button.ghost{background:transparent;border:1px solid #2b323b;color:#8a94a3}
 #log{border:1px solid #2b323b;border-radius:8px;padding:12px;min-height:160px;margin:12px 0}
 .me{color:#8a94a3} .u{color:#6c93ff} .k{white-space:pre-wrap;margin:4px 0 12px}
 .draft{border:1px solid #e0a356;background:#3a2b14;border-radius:8px;padding:12px;margin:10px 0}
 .draft pre{white-space:pre-wrap;margin:8px 0;font:inherit}
 form{display:flex;gap:8px} input{flex:1;padding:8px;border-radius:6px;border:1px solid #2b323b;background:#161b21;color:#e7ebf0}
</style></head><body>
<h2>Kacy test console</h2>
<p id="me" class="me">Checking sign-in...</p>
<div id="log"></div>
<form id="f"><input id="m" placeholder="Ask Kacy, e.g. email bob@example.com that the release moved to Friday" autocomplete="off"><button>Send</button></form>
<h3>Drafts waiting for your approval</h3>
<div id="drafts"><p class="me">None yet.</p></div>
<script>
let thread = null;
const H = {"Content-Type":"application/json","X-Kacy":"1"};
const el = (t, cls, text) => { const e = document.createElement(t); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; };

async function loadMe(){
  const me = await (await fetch("/me")).json();
  const p = document.getElementById("me"); p.textContent = "";
  if (me.signed_in) {
    p.append(`Signed in as ${me.email} (role: ${me.role}) `);
    const b = el("button","ghost","Sign out"); b.onclick = async () => { await fetch("/auth/logout",{method:"POST",headers:H}); location.reload(); }; p.append(b);
  } else {
    const a = el("a",null,"Sign in with Google"); a.href = "/auth/google/login"; p.append(a);
  }
}

async function loadDrafts(){
  const box = document.getElementById("drafts"); box.textContent = "";
  const r = await fetch("/drafts"); if (!r.ok) { box.append(el("p","me","Sign in to see drafts.")); return; }
  const drafts = await r.json();
  if (!drafts.length) { box.append(el("p","me","None yet.")); return; }
  for (const d of drafts) {
    const c = el("div","draft");
    c.append(el("div",null,`To: ${d.to}`), el("div",null,`Subject: ${d.subject}`), el("pre",null,d.body));
    const send = el("button",null,"Send"), discard = el("button","ghost","Discard");
    send.onclick = () => act(d.id, "send", send); discard.onclick = () => act(d.id, "discard", discard);
    c.append(send, " ", discard); box.append(c);
  }
}

async function act(id, what, btn){
  btn.disabled = true;
  const r = await fetch(`/drafts/${id}/${what}`, {method:"POST", headers:H});
  const body = await r.json();
  say("system", r.ok ? (what === "send" ? `Sent. Gmail message id ${body.gmail_message_id}` : "Discarded.") : `Error: ${JSON.stringify(body.detail)}`);
  loadDrafts();
}

function say(who, text){ const log = document.getElementById("log"); log.append(el("div", who === "you" ? "u" : "k", `${who}: ${text}`)); }

document.getElementById("f").onsubmit = async (e) => {
  e.preventDefault(); const m = document.getElementById("m"); const text = m.value.trim(); if (!text) return;
  m.value = ""; say("you", text);
  const r = await fetch("/chat", {method:"POST", headers:H, body: JSON.stringify({message:text, thread_id:thread})});
  const body = await r.json();
  if (!r.ok) { say("system", `Error ${r.status}: ${JSON.stringify(body.detail)}`); return; }
  thread = body.thread_id; say("kacy", body.reply); loadDrafts();
};

loadMe(); loadDrafts();
</script></body></html>"""
