"""
The agent's tool menu + the dispatcher that runs them.

Two halves:
  - TOOL_SPECS: what the *model* is told exists (name, description, argument
    schema). The model can only ask for these; it never runs anything itself.
  - dispatch(): what *our backend* does when the model asks. The caller's
    identity (user_email) and Google session come from the verified request,
    never from the model's arguments, so the model cannot pretend to be someone
    else. Permission checks live inside the tool functions (AccessDenied).

Tools come in two kinds:
  - read tools run immediately and return data (leave balance, calendar, inbox ...)
  - propose tools (draft_email, propose_meeting, draft_document, request_leave,
    decide_leave_request, open_it_ticket) only add a card to the approval queue.
    There is deliberately no tool that sends, creates or records anything itself.

Each spec carries two backend-only fields the model never sees:
  redact:   "full"  scrub every text field with Azure AI Language (HR data)
            "light" scrub card/account/ID numbers in mail and document text only
            "none"  internal structured data
  external: True when the text was written by someone else (email, document);
            it is scanned with Prompt Shields for indirect injection first.
"""
from __future__ import annotations

import json
import logging
from typing import Callable

from app.guardrails.pii_redaction import FINANCIAL_ID_CATEGORIES, redact, redact_many
from app.guardrails.prompt_shields import PromptInjectionDetected, check_retrieved_content
from app.identity.rbac import AccessDenied
from app.tools.brief import get_morning_brief
from app.tools.mcp_approval_chain.server import (
    decide_leave_request, list_my_requests, list_pending_approvals, request_leave)
from app.tools.mcp_calendar.server import find_free_slots, list_events, propose_meeting
from app.tools.mcp_docs.server import draft_document, list_documents, read_document
from app.tools.mcp_email.server import draft_email, read_email, search_inbox
from app.tools.mcp_hr.server import get_leave_balance, get_salary
from app.tools.mcp_it_ticket.server import open_it_ticket
from app.tools.mcp_repo.server import read_repo_file, repo_overview

logger = logging.getLogger("kacy.tools")

_STR = {"type": "string"}
_NULLABLE_STR = {"type": ["string", "null"]}
_INT = {"type": "integer"}


def _spec(name: str, description: str, props: dict | None = None, *, redact: str = "none", external: bool = False) -> dict:
    props = props or {}
    return {
        "name": name, "description": description,
        "parameters": {"type": "object", "properties": props, "required": list(props), "additionalProperties": False},
        "redact": redact, "external": external,
    }


# Note: no "requesting_user" argument anywhere. Who is asking is not the model's call.
TOOL_SPECS: list[dict] = [
    # --- HR ---
    _spec("get_leave_balance",
          "Look up how many leave days an employee has left. Use null for target_user to look up the person asking.",
          {"target_user": {"type": ["string", "null"], "description": "Email of the employee, or null for the asker."}},
          redact="full"),
    _spec("get_salary", "Look up an employee's salary. Access is restricted by the caller's role.",
          {"target_user": {"type": "string", "description": "Email of the employee whose salary is requested."}},
          redact="full"),
    # --- calendar ---
    _spec("list_events", "List the user's calendar events for today and the next few days.",
          {"days_ahead": {"type": "integer", "description": "1 = today only, up to 14."}}),
    _spec("find_free_slots",
          "Find meeting times inside working hours when the user (and any attendees whose calendars can be read) are free.",
          {"duration_minutes": {"type": "integer", "description": "Meeting length, 15 to 240."},
           "days_ahead": {"type": "integer", "description": "How many days to look ahead, 1 to 14."},
           "attendee_emails": {"type": ["array", "null"], "items": _STR, "description": "Invitee emails, or null."}}),
    _spec("propose_meeting",
          "Propose a calendar invite. This does NOT create it: the user sees a card with an Add to calendar button and decides.",
          {"title": _STR, "start": {"type": "string", "description": "ISO 8601 start, e.g. 2026-10-30T15:00"},
           "duration_minutes": _INT,
           "attendee_emails": {"type": ["array", "null"], "items": _STR}, "description": _STR}),
    # --- email ---
    _spec("draft_email",
          "Draft an email for the user to review. This does NOT send it: the user sees the draft with a Send button and decides. "
          "Use for any request to write, send, or reply to an email.",
          {"to": {"type": "string", "description": "Recipient email address."}, "subject": _STR,
           "body": {"type": "string", "description": "Plain-text body."}}),
    _spec("search_inbox",
          "Search the user's inbox. Gmail search syntax works: is:unread, from:name, newer_than:2d, or plain keywords.",
          {"query": _STR, "max_results": {"type": "integer", "description": "1 to 10."}}, redact="light", external=True),
    _spec("read_email", "Read one email in full, using an id returned by search_inbox.",
          {"message_id": _STR}, redact="light", external=True),
    # --- documents ---
    _spec("draft_document",
          "Draft a new document (Markdown). This does NOT create it: the user sees a card with a Create document button.",
          {"title": _STR, "content": {"type": "string", "description": "Markdown: headings, bullets, paragraphs."}}),
    _spec("list_documents", "Search shared company documents and documents Kacy created for the user.",
          {"query": _STR}),
    _spec("read_document", "Read one document by the id returned from list_documents.",
          {"document_id": _INT}, redact="light", external=True),
    # --- leave requests and approvals ---
    _spec("request_leave",
          "Prepare a leave request. The user sees a card showing who must approve it and confirms before it is filed.",
          {"start_date": {"type": "string", "description": "YYYY-MM-DD"}, "end_date": {"type": "string", "description": "YYYY-MM-DD"},
           "reason": _STR}),
    _spec("list_pending_approvals", "Managers and executives: list leave requests waiting for their decision."),
    _spec("decide_leave_request",
          "Managers and executives: propose approving or rejecting a leave request. The user confirms on a card before it is recorded.",
          {"request_id": _INT, "decision": {"type": "string", "enum": ["approve", "reject"]}, "comment": _STR}),
    _spec("list_my_requests", "List the user's own leave requests and IT tickets with their status."),
    # --- IT ---
    _spec("open_it_ticket",
          "Prepare an IT helpdesk ticket. The user sees a card and confirms before it is opened.",
          {"summary": _STR, "details": _STR,
           "priority": {"type": "string", "enum": ["low", "normal", "high", "urgent"]}}),
    # --- GitHub (read-only, public repos) ---
    _spec("repo_overview",
          "Look at a public GitHub repository: description, top-level files and the README. Read-only; nothing is cloned or run.",
          {"repo": {"type": "string", "description": "owner/name or a github.com link."}}, external=True),
    _spec("read_repo_file", "Read one text file (or list one folder) in a public GitHub repository.",
          {"repo": {"type": "string", "description": "owner/name or a github.com link."},
           "path": {"type": "string", "description": "Path inside the repo, e.g. src/main.py"}}, external=True),
    # --- brief ---
    _spec("get_morning_brief",
          "Gather today's meetings, unread email, approvals waiting, open requests and drafts waiting, in one call.",
          redact="light", external=True),
]

# name -> function taking (requesting_user, *, session, **model_args)
_HANDLERS: dict[str, Callable[..., dict]] = {
    "get_leave_balance": get_leave_balance, "get_salary": get_salary,
    "list_events": list_events, "find_free_slots": find_free_slots, "propose_meeting": propose_meeting,
    "draft_email": draft_email, "search_inbox": search_inbox, "read_email": read_email,
    "draft_document": draft_document, "list_documents": list_documents, "read_document": read_document,
    "request_leave": request_leave, "list_pending_approvals": list_pending_approvals,
    "decide_leave_request": decide_leave_request, "list_my_requests": list_my_requests,
    "open_it_ticket": open_it_ticket, "get_morning_brief": get_morning_brief,
    "repo_overview": repo_overview, "read_repo_file": read_repo_file,
}
_SPEC_BY_NAME = {s["name"]: s for s in TOOL_SPECS}

# Text fields that hold other people's writing. Only these get scanned and redacted in "light" mode.
_FREE_TEXT_KEYS = {"subject", "snippet", "body", "content", "title"}
_REDACT_KEYS = {"snippet", "body", "content"}


def _walk_text(node, keys: set[str]):
    """Yield (container, key) for every string value under one of `keys`, at any depth."""
    if isinstance(node, dict):
        for k, v in node.items():
            if isinstance(v, str) and k in keys:
                yield node, k
            else:
                yield from _walk_text(v, keys)
    elif isinstance(node, list):
        for item in node:
            yield from _walk_text(item, keys)


def _shield(result: dict) -> bool:
    """True if Prompt Shields found an injection attempt in the external text."""
    text = "\n".join(c[k] for c, k in _walk_text(result, _FREE_TEXT_KEYS))[:9000]
    if not text.strip():
        return False
    try:
        check_retrieved_content([text])
    except PromptInjectionDetected:
        return True
    return False


def dispatch(name: str, arguments_json: str, user_email: str, session=None,
             allowed: frozenset[str] | None = None) -> tuple[str, bool, list[str]]:
    """
    Run one tool call for `user_email`. Returns (output_json, was_denied, notes).
    `allowed` is the calling agent's tool list (None = every tool). A call outside it is refused here,
    in code, no matter what the model was told.
    Denials are returned as data so the model can explain them politely; the
    protected value itself is never put in the output. `notes` are human-readable
    facts about guardrails that ran, shown in the UI's activity pane.
    """
    handler = _HANDLERS.get(name)
    if handler is None:
        return json.dumps({"error": f"unknown tool {name}"}), False, []
    if allowed is not None and name not in allowed:
        return json.dumps({"error": "This agent can't use that tool. Tell the user to switch to Kacy or the agent built for it."}), \
            False, ["Blocked: not in this agent's tool list"]

    try:
        args = json.loads(arguments_json or "{}")
    except json.JSONDecodeError:
        return json.dumps({"error": "invalid arguments"}), False, []

    # Identity comes from us. Drop any attempt by the model to supply it.
    for forbidden in ("requesting_user", "session"):
        args.pop(forbidden, None)

    try:
        result = handler(user_email, session=session, **args)
    except AccessDenied as exc:
        return json.dumps({"error": "access_denied", "message": str(exc)}), True, []
    except (PermissionError, ValueError, TypeError) as exc:
        # Missing Google permission, a bad argument, a malformed call: tell the model plainly.
        return json.dumps({"error": str(exc)}), False, []
    except Exception:  # noqa: BLE001 - a failing integration must not take the whole chat down
        logger.exception("tool.%s failed", name)
        return json.dumps({"error": "That service is unavailable right now."}), False, []

    spec = _SPEC_BY_NAME[name]
    notes: list[str] = []

    if spec["external"] and _shield(result):
        logger.warning("tool.%s withheld: Prompt Shields flagged injection in external text", name)
        return json.dumps({"error": "withheld: this content looked like an attempt to give you instructions"}), False, [
            "Prompt Shields blocked text that tried to instruct the agent"]
    if spec["external"]:
        notes.append("Prompt Shields scanned the text for hidden instructions")

    # Defense in depth: scrub before anything goes back to the model.
    scrubbed = result
    if spec["redact"] == "full":
        # Numbers/bools pass through untouched; redaction only makes sense on text.
        scrubbed = {k: (redact(v) if isinstance(v, str) else v) for k, v in result.items()}
        notes.append("PII redaction applied to the result")
    elif spec["redact"] == "light":
        refs = [(c, k) for c, k in _walk_text(result, _REDACT_KEYS)]
        if refs:
            cleaned = redact_many([c[k] for c, k in refs], FINANCIAL_ID_CATEGORIES)
            for (c, k), text in zip(refs, cleaned):
                c[k] = text
            notes.append("Card and account numbers redacted from the text")
        scrubbed = result

    # Demo-only: show what redaction changed. Off by default, because writing the
    # un-redacted value to logs would itself be a PII leak in a real deployment.
    import os
    if os.environ.get("LOG_PII_DEMO") == "1" and spec["redact"] == "full":
        logger.info("tool.%s before_redaction=%s after_redaction=%s", name, result, scrubbed)

    return json.dumps(scrubbed, default=str), False, notes
