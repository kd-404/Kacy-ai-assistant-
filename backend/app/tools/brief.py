"""
Morning brief: one call that gathers the day. Each section is fetched on its own, so
if one source fails (for example Gmail permission not granted) the rest still arrive.
"""
from __future__ import annotations

import logging

from app import actions
from app.identity.rbac import AccessDenied
from app.tools.mcp_approval_chain import server as chain
from app.tools.mcp_calendar import server as calendar
from app.tools.mcp_email import server as email

logger = logging.getLogger("kacy.tools")


def get_morning_brief(requesting_user: str, *, session=None) -> dict:
    def section(fn, *args):
        try:
            return fn(requesting_user, *args, session=session)
        except AccessDenied:
            return {"not_available_for_your_role": True}
        except Exception as exc:  # noqa: BLE001 - one broken source must not sink the brief
            logger.warning("brief section %s failed: %s", fn.__name__, exc)
            return {"error": str(exc) if isinstance(exc, (PermissionError, ValueError)) else "could not load this section"}

    return {
        "today": section(calendar.list_events, 1),
        "unread_email": section(email.search_inbox, "is:unread", 5),
        "approvals_waiting_for_you": section(chain.list_pending_approvals),
        "your_requests_and_tickets": section(chain.list_my_requests),
        "drafts_waiting_for_your_approval": len(actions.pending_for(requesting_user)),
    }
