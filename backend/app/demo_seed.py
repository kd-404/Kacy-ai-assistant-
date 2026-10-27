"""
Demo mailbox, calendar and shared documents.

People signed in with Google use their real Gmail, Calendar and Drive. Everyone
else (a recruiter trying the demo accounts) gets this Loom & Light data instead,
so every feature can be tried without any Google permission. Dates are
relative to "now", so the calendar always looks current.
"""
from __future__ import annotations

from datetime import timedelta

from app import db, timeutil

COMPANY_DOCS = [
    ("Remote work policy",
     "Loom & Light supports remote work up to 3 days a week. Fully remote arrangements need manager and HR approval. "
     "Core hours are 11:00 to 16:00 in your local timezone. Company equipment stays with the employee."),
    ("Expense policy",
     "Client dinners are reimbursed up to INR 2,500 per person. Travel must be booked through the finance portal. "
     "Receipts are required for every claim above INR 500. Claims are paid in the next payroll cycle."),
    ("Incident response runbook",
     "Severity 1: customer-facing outage. Page the on-call engineer, open a bridge call, post updates every 30 minutes. "
     "Severity 2: degraded service. Open a ticket and notify the team channel. A written review is due within 5 working days."),
    ("Q4 product roadmap",
     "Q4 priorities: ship the loom-studio editor beta, launch usage-based billing, and move uploads to the new storage tier. "
     "Owners: Editor - Priya, Billing - Marcus, Storage - Dev. The release candidate is due Friday."),
    ("Onboarding checklist",
     "Day 1: laptop, accounts and security training. Week 1: meet your team and your buddy, read the handbook. "
     "Day 30: first review with your manager. Day 90: goals for the next half-year."),
]


def _person(email: str) -> str:
    return email.split("@")[0].replace(".", " ").title()


def ensure_seeded(owner: str) -> None:
    """Create this person's demo mailbox + calendar once, and the shared docs once per database."""
    if db.one("SELECT 1 FROM seeded WHERE owner=?", (owner,)):
        return
    db.run("INSERT OR IGNORE INTO seeded (owner) VALUES (?)", (owner,))
    now = db.now()
    me = _person(owner)

    if not db.one("SELECT 1 FROM seeded WHERE owner='company'"):
        db.run("INSERT OR IGNORE INTO seeded (owner) VALUES ('company')")
        for title, content in COMPANY_DOCS:
            db.run("INSERT INTO documents (owner, title, content, source, created) VALUES ('company',?,?,'demo',?)",
                   (title, content, now))
        _seed_company_requests(now)

    emails = [
        ("priya.nair@loomandlight.example", "Release candidate is ready for review",
         f"Hi {me},\n\nThe release candidate for the loom-studio editor beta is ready. Could you review the checklist before "
         "Friday and confirm sign-off? The open items are the export bug and the onboarding copy.\n\nThanks,\nPriya", 1, 2),
        ("marcus.lee@loomandlight.example", "Usage-based billing: pricing question",
         f"{me}, finance needs a decision on the free tier limit for usage-based billing. Options are 1,000 or 2,500 "
         "events a month. Can you reply with your pick by Thursday?\n\nMarcus", 1, 5),
        ("it-helpdesk@loomandlight.example", "Your VPN certificate expires in 7 days",
         "Your VPN certificate expires next week. Renew it from the IT portal under Access > Certificates. "
         "If renewal fails, open a ticket with the helpdesk.", 1, 9),
        ("hr@loomandlight.example", "Annual leave reminder",
         "Reminder: unused leave above 10 days does not carry over after 31 December. Plan your remaining leave and "
         "submit requests through the HR portal.", 0, 30),
        ("dana@brightcloth.example", "Following up on our demo",
         f"Hi {me}, thanks for the demo last week. We liked the batch ordering flow. Could you send over pricing for "
         "about 40 seats and an outline of the rollout plan?\n\nDana, Bright Cloth Co.", 1, 14),
        ("billing@loomandlight.example", "Invoice INV-2041 awaiting approval",
         "Invoice INV-2041 from Threadline Print for INR 84,200 is waiting for your approval in the finance portal.", 0, 26),
    ]
    for sender, subject, body, unread, hours_ago in emails:
        db.run("INSERT INTO demo_emails (owner, sender, subject, date, body, unread) VALUES (?,?,?,?,?,?)",
               (owner, sender, subject, now - hours_ago * 3600, body, unread))

    today = timeutil.now_local().replace(hour=0, minute=0, second=0, microsecond=0)
    schedule = [  # (day offset, hour, minute, minutes, title, location)
        (0, 10, 0, 30, "Team standup", "Meet"),
        (0, 14, 0, 60, "Editor beta review", "Room Loom"),
        (1, 11, 0, 30, "1:1 with manager", "Meet"),
        (1, 15, 0, 60, "Billing launch planning", "Room Light"),
        (2, 10, 30, 45, "Customer call: Bright Cloth Co.", "Meet"),
        (3, 16, 0, 30, "Release go/no-go", "Meet"),
    ]
    for off, hour, minute, mins, title, loc in schedule:
        start = (today + timedelta(days=off)).replace(hour=hour, minute=minute).timestamp()
        db.run("INSERT INTO demo_events (owner, title, start, end, attendees, location) VALUES (?,?,?,?,?,?)",
               (owner, title, start, start + mins * 60, "[]", loc))


def _seed_company_requests(now: float) -> None:
    """A couple of in-flight requests so a manager signing in has something to approve."""
    today = timeutil.now_local().date()
    for requester, start_off, days, reason, stages in [
        ("alice@kacy-demo.test", 14, 3, "Family wedding", ["manager"]),
        ("bob@kacy-demo.test", 21, 8, "Trip abroad", ["manager", "exec"]),
    ]:
        start = today + timedelta(days=start_off)
        end = start + timedelta(days=days - 1)
        db.run("INSERT INTO leave_requests (requester, start_date, end_date, days, reason, stages, created) VALUES (?,?,?,?,?,?,?)",
               (requester, start.isoformat(), end.isoformat(), days, reason, db.dumps(stages), now))
    db.run("INSERT INTO tickets (requester, summary, details, priority, created) VALUES (?,?,?,?,?)",
           ("alice@kacy-demo.test", "Laptop battery drains quickly", "About 2 hours of battery after a full charge.", "normal", now))
