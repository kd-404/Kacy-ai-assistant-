"""
Leave requests and the approval chain.

Flow: an employee asks Kacy for leave -> approval card -> on confirm the request is
filed and routed. Up to 5 working days needs the manager. Longer needs the manager and
then an executive. A manager's own request goes to an executive; an executive's is
auto-approved. A manager or executive then asks Kacy for their pending approvals and
records a decision, which again goes through an approval card before it is applied.

Rules enforced in code (not by the prompt):
  - only managers and executives can list or decide requests
  - nobody decides their own request
  - the decider must hold the role of the CURRENT stage (an executive can cover any stage)
  - every rule is checked again when the decision is applied, because a card can be stale
"""
from __future__ import annotations

import json
from datetime import date, timedelta

from app import actions, db, timeutil
from app.identity.rbac import AccessDenied, Role, role_for
from app.tools.mcp_hr import server as hr

LONG_LEAVE_DAYS = 5
_ROLE_NAMES = {"manager": "Manager", "exec": "Executive"}


def working_days(start: date, end: date) -> int:
    return sum(1 for i in range((end - start).days + 1) if (start + timedelta(days=i)).weekday() < 5)


def stages_for(requester_role: Role, days: int) -> list[str]:
    if requester_role is Role.EXEC:
        return []
    if requester_role is Role.MANAGER:
        return ["exec"]
    return ["manager", "exec"] if days > LONG_LEAVE_DAYS else ["manager"]


def route_label(stages: list[str]) -> str:
    return ", then ".join(_ROLE_NAMES[s] for s in stages) if stages else "No approval needed"


def _can_decide(approver: str, requester: str, stage_role: str) -> bool:
    role = role_for(approver)
    if approver == requester or role not in {Role.MANAGER, Role.EXEC}:
        return False
    return role is Role.EXEC or role.value == stage_role


def _dates(row: dict) -> str:
    return f"{row['start_date']} to {row['end_date']} ({row['days']} working days)"


# ---------- tools the model can call ----------

def request_leave(requesting_user: str, start_date: str, end_date: str, reason: str, *, session=None) -> dict:
    try:
        start, end = date.fromisoformat(start_date), date.fromisoformat(end_date)
    except ValueError:
        return {"error": "dates must be YYYY-MM-DD"}
    if start < timeutil.now_local().date() or end < start:
        return {"error": "the start date is in the past or the end date is before the start date"}
    days = working_days(start, end)
    if days == 0:
        return {"error": "those dates contain no working days"}
    balance = hr.get_leave_balance(requesting_user).get("leave_balance_days", 0)
    if days > balance:
        return {"error": f"{days} working days requested but only {balance} left"}
    stages = stages_for(role_for(requesting_user), days)
    action = actions.propose(requesting_user, "leave_request", {
        "start_date": start.isoformat(), "end_date": end.isoformat(), "days": days,
        "reason": reason.strip()[:500], "stages": stages, "route": route_label(stages)})
    return {"action_id": action["id"], "status": "pending_approval", "working_days": days,
            "route": route_label(stages), "note": "Shown to the user with a Submit request button. NOT filed yet."}


def list_pending_approvals(requesting_user: str, *, session=None) -> dict:
    role = role_for(requesting_user)
    if role not in {Role.MANAGER, Role.EXEC}:
        raise AccessDenied("approvals", role)
    out = []
    for row in db.rows("SELECT * FROM leave_requests WHERE status='pending' ORDER BY id"):
        stages = json.loads(row["stages"])
        if _can_decide(requesting_user, row["requester"], stages[row["stage"]]):
            out.append({"request_id": row["id"], "requester": row["requester"], "dates": _dates(row),
                        "reason": row["reason"], "waiting_for": _ROLE_NAMES[stages[row["stage"]]]})
    return {"pending": out}


def list_my_requests(requesting_user: str, *, session=None) -> dict:
    leave = db.rows("SELECT * FROM leave_requests WHERE requester=? ORDER BY id DESC LIMIT 10", (requesting_user,))
    tickets = db.rows("SELECT * FROM tickets WHERE requester=? ORDER BY id DESC LIMIT 10", (requesting_user,))
    return {
        "leave_requests": [{"request_id": r["id"], "dates": _dates(r), "status": r["status"],
                            "waiting_for": (_ROLE_NAMES[json.loads(r["stages"])[r["stage"]]] if r["status"] == "pending" else None)}
                           for r in leave],
        "tickets": [{"ticket": f"IT-{1000 + t['id']}", "summary": t["summary"], "status": t["status"],
                     "priority": t["priority"]} for t in tickets],
    }


def decide_leave_request(requesting_user: str, request_id: int, decision: str, comment: str, *, session=None) -> dict:
    role = role_for(requesting_user)
    if role not in {Role.MANAGER, Role.EXEC}:
        raise AccessDenied("approvals", role)
    if decision not in {"approve", "reject"}:
        return {"error": "decision must be approve or reject"}
    row = db.one("SELECT * FROM leave_requests WHERE id=?", (request_id,))
    if row is None or row["status"] != "pending":
        return {"error": "no pending request with that id"}
    stages = json.loads(row["stages"])
    if not _can_decide(requesting_user, row["requester"], stages[row["stage"]]):
        raise AccessDenied(f"leave_request:{request_id}", role)
    action = actions.propose(requesting_user, "leave_decision", {
        "request_id": request_id, "requester": row["requester"], "dates": _dates(row),
        "decision": decision, "comment": comment.strip()[:500]})
    return {"action_id": action["id"], "status": "pending_approval",
            "note": "Shown to the user with a Confirm decision button. NOT recorded yet."}


# ---------- applied by executors.py after the person confirms ----------

def submit_request(requester: str, p: dict) -> dict:
    role = role_for(requester)
    stages = stages_for(role, p["days"])  # recomputed here, never trusted from the stored payload
    status = "pending" if stages else "approved"
    request_id = db.run(
        "INSERT INTO leave_requests (requester, start_date, end_date, days, reason, status, stages, created) VALUES (?,?,?,?,?,?,?,?)",
        (requester, p["start_date"], p["end_date"], p["days"], p["reason"], status, db.dumps(stages), db.now()))
    if status == "approved":
        hr.deduct_leave(requester, p["days"])
    return {"message": f"Leave request #{request_id} filed. Route: {route_label(stages)}.", "request_id": request_id}


def apply_decision(approver: str, p: dict) -> dict:
    row = db.one("SELECT * FROM leave_requests WHERE id=?", (p["request_id"],))
    if row is None or row["status"] != "pending":
        raise ValueError("this request is no longer pending")
    stages = json.loads(row["stages"])
    if not _can_decide(approver, row["requester"], stages[row["stage"]]):
        raise AccessDenied(f"leave_request:{row['id']}", role_for(approver))
    history = json.loads(row["history"]) + [{
        "by": approver, "role": role_for(approver).value, "decision": p["decision"],
        "comment": p.get("comment", ""), "at": db.now()}]
    if p["decision"] == "reject":
        status, stage = "rejected", row["stage"]
    elif role_for(approver) is Role.EXEC or row["stage"] + 1 >= len(stages):
        # an executive is the top of the chain, so their approval completes it at any stage
        status, stage = "approved", row["stage"]
    else:
        status, stage = "pending", row["stage"] + 1
    db.run("UPDATE leave_requests SET status=?, stage=?, history=? WHERE id=?",
           (status, stage, db.dumps(history), row["id"]))
    if status == "approved":
        hr.deduct_leave(row["requester"], row["days"])
    nxt = f" Now waiting for {_ROLE_NAMES[stages[stage]]}." if status == "pending" else ""
    return {"message": f"Request #{row['id']} {status}.{nxt}"}
