"""
Approval-chain tool — fourth new agentic action. e.g. a manager approving a
teammate's leave request. Checks the requester's role before allowing the
decision to be recorded, same RBAC pattern as the HR tool.
"""
from __future__ import annotations

from app.identity.rbac import AccessDenied, Role, role_for
from app.tools.base import ToolStore
from app.tools.mcp_hr.server import get_leave_balance

_decisions = ToolStore()


def review_leave_request(approving_user: str, requesting_user: str, days: int) -> dict:
    approver_role = role_for(approving_user)
    if approver_role not in {Role.MANAGER, Role.EXEC}:
        raise AccessDenied(resource="leave_approval", role=approver_role)

    balance = get_leave_balance(approving_user, requesting_user)
    conflict = balance.get("leave_balance_days", 0) < days
    return {
        "requesting_user": requesting_user,
        "days": days,
        "balance_check": balance,
        "conflict": conflict,
        "recommendation": "deny — insufficient balance" if conflict else "approve",
    }


def record_decision(approving_user: str, requesting_user: str, days: int, decision: str) -> dict:
    """Only call this after the approver explicitly confirms the decision in the UI."""
    return _decisions.add({
        "approved_by": approving_user, "requesting_user": requesting_user,
        "days": days, "decision": decision,
    })
