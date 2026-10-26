"""
RBAC / on-behalf-of role scoping.

In production this maps to Foundry Agent Identity + Entra ID delegated auth
(see docs/architecture.md section 2). For local dev before that's wired up,
this module is a stand-in: a plain role table the tool layer checks before
returning anything sensitive — recreates the video's "intern asks for CEO
salary -> access denied" scene without needing a real Entra tenant yet.
"""
from __future__ import annotations

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


def role_for(user_email: str) -> Role:
    if user_email not in DEMO_USERS:
        raise AccessDenied("unknown user", Role.INTERN)
    return DEMO_USERS[user_email]


def require_role(user_email: str, resource: str, allowed: set[Role]) -> None:
    role = role_for(user_email)
    if role not in allowed:
        raise AccessDenied(resource, role)
