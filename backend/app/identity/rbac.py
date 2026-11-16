"""
RBAC / on-behalf-of role scoping.

In production this maps to Foundry Agent Identity + Entra ID delegated auth
(see docs/architecture.md section 2). For local dev before that's wired up,
this module is a stand-in: a plain role table the tool layer checks before
returning anything sensitive — recreates the video's "intern asks for CEO
salary -> access denied" scene without needing a real Entra tenant yet.
"""
from __future__ import annotations

import os
from enum import Enum


class Role(str, Enum):
    INTERN = "intern"
    EMPLOYEE = "employee"
    MANAGER = "manager"
    EXEC = "exec"


# Demo org chart (demo-company/org-chart.md is the human-readable version of this).
DEMO_USERS = {
    "alice@kacy-demo.test": Role.INTERN,
    "bob@kacy-demo.test": Role.EMPLOYEE,
    "carla@kacy-demo.test": Role.MANAGER,
    "dev@kacy-demo.test": Role.EXEC,
}


class AccessDenied(Exception):
    def __init__(self, resource: str, role: Role):
        self.resource = resource
        self.role = role
        super().__init__(f"{role.value} is not authorized to access {resource}")


def _configured_users() -> dict[str, Role]:
    """
    Real accounts (people who sign in with Google) and their roles, from .env:
        KACY_USER_ROLES=you@gmail.com=exec,friend@gmail.com=intern
    Only accounts listed here (or the demo accounts) can use Kacy at all, the
    way a company only lets in employees that IT has provisioned.
    """
    users: dict[str, Role] = {}
    for pair in os.environ.get("KACY_USER_ROLES", "").split(","):
        if "=" in pair:
            email, role = pair.split("=", 1)
            users[email.strip().lower()] = Role(role.strip().lower())
    return users


def is_demo_user(user_email: str) -> bool:
    return user_email.lower() in DEMO_USERS


def _default_role() -> Role | None:
    """
    Optional open sign-in: KACY_DEFAULT_ROLE=employee lets any Google account in at that role.
    Accounts listed in KACY_USER_ROLES (and the demo personas) keep their own role.
    Leave it unset to keep Kacy closed to everyone who is not listed.
    """
    raw = os.environ.get("KACY_DEFAULT_ROLE", "").strip().lower()
    if not raw:
        return None
    role = Role(raw)
    if role in (Role.MANAGER, Role.EXEC):
        raise ValueError("KACY_DEFAULT_ROLE must be intern or employee, never manager or exec")
    return role


def is_open_user(user_email: str) -> bool:
    """A signed-in person who has no listed role and got the default one."""
    email = user_email.lower()
    return email not in {**DEMO_USERS, **_configured_users()} and _default_role() is not None


def role_for(user_email: str) -> Role:
    users = {**DEMO_USERS, **_configured_users()}
    email = user_email.lower()
    if email in users:
        return users[email]
    default = _default_role()
    if default is None:
        raise AccessDenied("unknown user", Role.INTERN)
    return default


def require_role(user_email: str, resource: str, allowed: set[Role]) -> None:
    role = role_for(user_email)
    if role not in allowed:
        raise AccessDenied(resource, role)
