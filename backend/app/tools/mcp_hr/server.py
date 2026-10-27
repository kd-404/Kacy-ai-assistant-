"""
HR tool — read-only lookups (leave balance, salary). Salary lookups are where
the RBAC + PII redaction story from the video plays out: an intern querying
their own leave balance is fine; an intern querying someone else's salary
gets denied before redaction even matters.

Run as its own process once wired to a real MCP transport:
    python -m app.tools.mcp_hr.server
For now, exposed as plain functions the FastAPI backend calls directly —
swap for an actual `mcp.server.Server` + stdio/SSE transport when you're
ready to register it as a real MCP endpoint in Foundry (see docs/architecture.md).
"""
from __future__ import annotations

from app.guardrails.pii_redaction import redact_field
from app.identity.rbac import AccessDenied, Role, role_for
from app.tools.base import ToolStore

_store = ToolStore()
_store.add({"employee": "alice@kacy-demo.test", "leave_balance_days": 12, "salary": 55000})
_store.add({"employee": "bob@kacy-demo.test", "leave_balance_days": 18, "salary": 72000})
_store.add({"employee": "carla@kacy-demo.test", "leave_balance_days": 20, "salary": 115000})
_store.add({"employee": "dev@kacy-demo.test", "leave_balance_days": 25, "salary": 240000})


def get_leave_balance(requesting_user: str, target_user: str | None = None) -> dict:
    target_user = target_user or requesting_user
    if target_user != requesting_user and role_for(requesting_user) not in {Role.MANAGER, Role.EXEC}:
        raise AccessDenied(resource=f"leave:{target_user}", role=role_for(requesting_user))
    record = next((r for r in _store.all() if r["employee"] == target_user), None)
    if record is None:
        return {"error": "no such employee"}
    return {"employee": target_user, "leave_balance_days": record["leave_balance_days"]}


def get_salary(requesting_user: str, target_user: str) -> dict:
    """
    This is the video's exact scenario: an intern asking for the CEO's salary.
    Only the record owner, or a manager/exec, may see an un-redacted figure.
    """
    requester_role = role_for(requesting_user)
    record = next((r for r in _store.all() if r["employee"] == target_user), None)
    if record is None:
        return {"error": "no such employee"}

    is_self = requesting_user == target_user
    if is_self or requester_role in {Role.MANAGER, Role.EXEC}:
        return {"employee": target_user, "salary": record["salary"]}

    raise AccessDenied(resource=f"salary:{target_user}", role=requester_role)
