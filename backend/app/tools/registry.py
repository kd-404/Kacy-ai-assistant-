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
from typing import Callable

from app.identity.rbac import AccessDenied
from app.tools.mcp_hr.server import get_leave_balance, get_salary

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

# name -> function taking (requesting_user, **model_args)
_HANDLERS: dict[str, Callable[..., dict]] = {
    "get_leave_balance": get_leave_balance,
    "get_salary": get_salary,
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
        return json.dumps(handler(requesting_user=user_email, **args)), False
    except AccessDenied as exc:
        return json.dumps({"error": "access_denied", "message": str(exc)}), True
