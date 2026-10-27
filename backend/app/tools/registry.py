"""
The agent's tool menu + the dispatcher that runs them.

Two halves:
  - TOOL_SPECS: what the *model* is told exists (name, description, argument
    schema). The model can only ask for these; it never runs anything itself.
  - dispatch(): what *our backend* does when the model asks. The caller's
    identity (user_email) is passed in by the backend from the request, never
    taken from the model's arguments, so the model cannot pretend to be someone
    else. Permission checks live inside the tool functions (AccessDenied).
"""
from __future__ import annotations

import json
import logging
import os
from typing import Callable

from app.guardrails.pii_redaction import redact
from app.identity.rbac import AccessDenied
from app.tools.mcp_email.server import draft_email
from app.tools.mcp_hr.server import get_leave_balance, get_salary

logger = logging.getLogger("kacy.tools")

# JSON-Schema argument definitions. Note: no "requesting_user" argument anywhere.
TOOL_SPECS: list[dict] = [
    {
        "name": "get_leave_balance",
        "description": (
            "Look up how many leave days an employee has left. Omit target_user "
            "to look up the person asking."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "target_user": {
                    "type": ["string", "null"],
                    "description": "Email of the employee to look up, or null for the asker.",
                }
            },
            "required": ["target_user"],
            "additionalProperties": False,
        },
    },
    {
        "name": "get_salary",
        "description": "Look up an employee's salary. Access is restricted by the caller's role.",
        "parameters": {
            "type": "object",
            "properties": {
                "target_user": {
                    "type": "string",
                    "description": "Email of the employee whose salary is requested.",
                }
            },
            "required": ["target_user"],
            "additionalProperties": False,
        },
    },
]

TOOL_SPECS.append({
    # There is deliberately no "send_email" tool. The model can only draft;
    # sending happens when the user presses Send (backend route, not the model).
    "name": "draft_email",
    "description": (
        "Draft an email for the user to review. This does NOT send it: the user "
        "sees the draft with a Send button and decides. Use for any request to "
        "write, send, or reply to an email."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "to": {"type": "string", "description": "Recipient email address."},
            "subject": {"type": "string", "description": "Subject line."},
            "body": {"type": "string", "description": "Plain-text body of the email."},
        },
        "required": ["to", "subject", "body"],
        "additionalProperties": False,
    },
})

# name -> function taking (requesting_user, **model_args)
_HANDLERS: dict[str, Callable[..., dict]] = {
    "get_leave_balance": get_leave_balance,
    "get_salary": get_salary,
    "draft_email": draft_email,
}


def dispatch(name: str, arguments_json: str, user_email: str) -> tuple[str, bool]:
    """
    Run one tool call for `user_email`. Returns (output_json, was_denied).
    Denials are returned as data so the model can explain them politely; the
    protected value itself is never put in the output.
    """
    handler = _HANDLERS.get(name)
    if handler is None:
        return json.dumps({"error": f"unknown tool {name}"}), False

    try:
        args = json.loads(arguments_json or "{}")
    except json.JSONDecodeError:
        return json.dumps({"error": "invalid arguments"}), False

    # Identity comes from us. Drop any attempt by the model to supply it.
    args.pop("requesting_user", None)

    try:
        result = handler(requesting_user=user_email, **args)
    except AccessDenied as exc:
        return json.dumps({"error": "access_denied", "message": str(exc)}), True

    # Defense in depth: scrub any free-text field before it goes back to the
    # model, regardless of which tool produced it. Numbers/bools pass through
    # untouched -- redaction only makes sense on text.
    scrubbed = {k: (redact(v) if isinstance(v, str) else v) for k, v in result.items()}

    # Demo-only: show what redaction changed. Off by default, because writing the
    # un-redacted value to logs would itself be a PII leak in a real deployment.
    if os.environ.get("LOG_PII_DEMO") == "1":
        logger.info("tool.%s before_redaction=%s after_redaction=%s", name, result, scrubbed)

    return json.dumps(scrubbed), False
