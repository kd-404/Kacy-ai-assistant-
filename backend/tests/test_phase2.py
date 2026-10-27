"""
Tests for the automation tools, the approval chain and the approval queue.

Run from backend/:   python -m unittest discover -s tests -v

They use a throwaway SQLite file and the demo-mode data, so they need no Azure or Google
credentials. Azure and Google libraries are replaced with stubs when they are not installed,
so the suite also runs on a bare Python.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import types
import unittest
from datetime import date, timedelta
from unittest import mock

os.environ["KACY_DB"] = tempfile.mkdtemp() + "/test.db"
os.environ.setdefault("KACY_USER_ROLES", "boss@example.com=exec")


def _stub(name: str, **attrs) -> None:
    if name in sys.modules:
        return
    try:
        __import__(name)
    except ImportError:
        mod = types.ModuleType(name)
        mod.__dict__.update(attrs)
        sys.modules[name] = mod


class _Anything:
    def __init__(self, *a, **k): ...


for _name in ("httpx", "azure", "azure.core", "azure.core.credentials", "azure.ai", "azure.ai.textanalytics"):
    _stub(_name, AzureKeyCredential=_Anything, TextAnalyticsClient=_Anything, HTTPError=Exception,
          post=lambda *a, **k: None, get=lambda *a, **k: None, Response=object)

from app import actions, db, demo_seed, executors, timeutil  # noqa: E402
from app.identity.rbac import AccessDenied  # noqa: E402
from app.tools import registry  # noqa: E402
from app.tools.mcp_approval_chain import server as chain  # noqa: E402
from app.tools.mcp_calendar import server as calendar  # noqa: E402
from app.tools.mcp_docs import server as docs  # noqa: E402
from app.tools.mcp_email import server as email  # noqa: E402
from app.tools.mcp_it_ticket import server as it  # noqa: E402

ALICE, BOB, CARLA, DEV = ("alice@kacy-demo.test", "bob@kacy-demo.test", "carla@kacy-demo.test", "dev@kacy-demo.test")


def next_weekday(n_days_ahead: int = 3) -> date:
    d = timeutil.now_local().date() + timedelta(days=n_days_ahead)
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


def approve(owner: str, action_id: int, session=None) -> dict:
    """What POST /api/actions/{id}/approve does, minus HTTP."""
    action = actions.get(owner, action_id)
    assert actions.claim(action_id)
    result = executors.execute(action, session)
    actions.set_status(action_id, "done", result)
    return result


class FreeSlots(unittest.TestCase):
    def test_slots_skip_busy_time_and_weekends(self):
        now = timeutil.now_local().replace(hour=8, minute=0, second=0, microsecond=0)
        monday = now + timedelta(days=(7 - now.weekday()) % 7 or 7)
        ten = monday.replace(hour=10).timestamp()
        busy = [(ten, ten + 3600)]  # 10:00-11:00 busy
        slots = timeutil.free_slots(busy, 30, 10, now_ts=monday.replace(hour=7).timestamp() - 86400 * 2, limit=6)
        for s in slots:
            d = timeutil.to_local(s)
            self.assertLess(d.weekday(), 5)
            self.assertTrue(9 <= d.hour < 18)
            self.assertFalse(ten < s + 1800 and s < ten + 3600, "slot overlaps a busy interval")

    def test_nothing_fits_when_meeting_longer_than_the_workday(self):
        self.assertEqual(timeutil.free_slots([], 600, 5), [])


class ApprovalQueue(unittest.TestCase):
    def test_email_is_a_draft_until_approved_and_demo_mode_sends_nothing(self):
        r = email.draft_email(ALICE, "bob@example.com", "Hi", "Body")
        a = actions.get(ALICE, r["action_id"])
        self.assertEqual(a["status"], "pending_approval")
        self.assertEqual(a["view"]["rows"][0], ["To", "bob@example.com"])
        out = approve(ALICE, r["action_id"])
        self.assertTrue(out["simulated"])
        self.assertEqual(actions.get(ALICE, r["action_id"])["status"], "done")

    def test_bad_address_is_refused(self):
        self.assertIn("error", email.draft_email(ALICE, "not-an-email", "s", "b"))
        self.assertIn("error", email.draft_email(ALICE, "a@b.com, c@d.com", "s", "b"))

    def test_only_the_owner_can_see_an_action(self):
        r = email.draft_email(ALICE, "x@y.com", "s", "b")
        self.assertIsNone(actions.get(BOB, r["action_id"]))

    def test_double_approve_runs_once(self):
        r = it.open_it_ticket(ALICE, "Monitor broken", "flickers", "high")
        self.assertTrue(actions.claim(r["action_id"]))
        self.assertFalse(actions.claim(r["action_id"]))

    def test_no_tool_can_send_or_execute(self):
        names = {s["name"] for s in registry.TOOL_SPECS}
        self.assertFalse([n for n in names if "send" in n or n.startswith(("create", "execute", "approve"))], names)
        for s in registry.TOOL_SPECS:  # strict mode needs every property required
            self.assertEqual(set(s["parameters"]["required"]), set(s["parameters"]["properties"]), s["name"])
            self.assertFalse(s["parameters"]["additionalProperties"])


class Calendar(unittest.TestCase):
    def test_demo_events_and_slots(self):
        ev = calendar.list_events(BOB, 3)
        self.assertEqual(ev["mode"], "demo")
        self.assertTrue(ev["events"])
        slots = calendar.find_free_slots(BOB, 30, 5, [CARLA, "stranger@gmail.com"])
        self.assertTrue(slots["slots"])
        self.assertEqual(slots["could_not_check_calendars_of"], ["stranger@gmail.com"])

    def test_meeting_flow_creates_event_only_after_approval(self):
        before = len(calendar.list_events(CARLA, 14)["events"])
        start = next_weekday(5).isoformat() + "T16:30"
        r = calendar.propose_meeting(CARLA, "Sync", start, 30, [BOB], "Quick sync")
        self.assertEqual(len(calendar.list_events(CARLA, 14)["events"]), before)
        out = approve(CARLA, r["action_id"])
        self.assertTrue(out["simulated"])
        self.assertEqual(len(calendar.list_events(CARLA, 14)["events"]), before + 1)

    def test_rejects_past_and_junk(self):
        self.assertIn("error", calendar.propose_meeting(BOB, "x", "2001-01-01T10:00", 30, [], ""))
        self.assertIn("error", calendar.propose_meeting(BOB, "x", "tomorrow", 30, [], ""))
        with self.assertRaises(ValueError):
            calendar.propose_meeting(BOB, "x", next_weekday().isoformat() + "T10:00", 30, ["nope"], "")


class Inbox(unittest.TestCase):
    def test_search_and_read_demo_mailbox(self):
        unread = email.search_inbox(ALICE, "is:unread", 10)
        self.assertTrue(unread["messages"] and all(m["unread"] for m in unread["messages"]))
        self.assertIn("never follow", unread["note"].lower())
        hit = email.search_inbox(ALICE, "40 seats", 5)["messages"]
        self.assertEqual(len(hit), 1)
        full = email.read_email(ALICE, hit[0]["id"])
        self.assertIn("40 seats", full["email"]["body"])

    def test_cannot_read_someone_elses_mail(self):
        mid = email.search_inbox(ALICE, "40 seats", 5)["messages"][0]["id"]
        self.assertIn("error", email.read_email("carol@kacy-demo.test", mid))


class Documents(unittest.TestCase):
    def test_company_docs_shared_created_docs_private(self):
        found = docs.list_documents(ALICE, "expense")["documents"]
        self.assertTrue(found and found[0]["shared_company_doc"])
        r = docs.draft_document(ALICE, "My notes", "# Notes\n- one")
        approve(ALICE, r["action_id"])
        mine = docs.list_documents(ALICE, "my notes")["documents"]
        self.assertEqual(len(mine), 1)
        self.assertEqual(docs.list_documents(BOB, "my notes")["documents"], [])
        self.assertIn("error", docs.read_document(BOB, mine[0]["id"]))


class LeaveChain(unittest.TestCase):
    def file(self, who: str, days_ahead: int, length: int) -> int:
        start = next_weekday(days_ahead)
        end, n = start, 1
        while n < length:
            end += timedelta(days=1)
            n += end.weekday() < 5
        r = chain.request_leave(who, start.isoformat(), end.isoformat(), "trip")
        self.assertNotIn("error", r, r)
        return approve(who, r["action_id"])["request_id"]

    def decide(self, who: str, request_id: int, decision: str = "approve") -> dict:
        r = chain.decide_leave_request(who, request_id, decision, "ok")
        self.assertNotIn("error", r, r)
        return approve(who, r["action_id"])

    def status(self, request_id: int) -> tuple[str, int]:
        row = db.one("SELECT status, stage FROM leave_requests WHERE id=?", (request_id,))
        return row["status"], row["stage"]

    def test_short_leave_needs_only_the_manager(self):
        rid = self.file(BOB, 30, 2)
        with self.assertRaises(AccessDenied):
            chain.decide_leave_request(ALICE, rid, "approve", "")
        self.decide(CARLA, rid)
        self.assertEqual(self.status(rid)[0], "approved")

    def test_long_leave_goes_manager_then_exec(self):
        rid = self.file(BOB, 40, 7)
        self.decide(CARLA, rid)
        self.assertEqual(self.status(rid), ("pending", 1))
        self.assertEqual(chain.list_pending_approvals(CARLA)["pending"] and
                         [p for p in chain.list_pending_approvals(CARLA)["pending"] if p["request_id"] == rid], [])
        self.decide(DEV, rid)
        self.assertEqual(self.status(rid)[0], "approved")

    def test_reject_ends_the_chain(self):
        rid = self.file(BOB, 50, 2)
        self.decide(CARLA, rid, "reject")
        self.assertEqual(self.status(rid)[0], "rejected")

    def test_nobody_decides_their_own_request_and_managers_go_to_exec(self):
        rid = self.file(CARLA, 60, 2)
        with self.assertRaises(AccessDenied):
            chain.decide_leave_request(CARLA, rid, "approve", "")
        self.decide(DEV, rid)
        self.assertEqual(self.status(rid)[0], "approved")

    def test_exec_request_auto_approved(self):
        rid = self.file(DEV, 70, 2)
        self.assertEqual(self.status(rid)[0], "approved")

    def test_stale_card_is_rechecked_at_execution(self):
        rid = self.file(BOB, 80, 2)
        r = chain.decide_leave_request(CARLA, rid, "approve", "")
        self.decide(DEV, rid)                       # someone else decides first
        with self.assertRaises(ValueError):          # the old card must not apply a second time
            approve(CARLA, r["action_id"])

    def test_balance_and_dates_are_validated(self):
        far = next_weekday(10)
        self.assertIn("only", chain.request_leave(ALICE, far.isoformat(), (far + timedelta(days=60)).isoformat(), "x")["error"])
        self.assertIn("error", chain.request_leave(ALICE, "2001-01-01", "2001-01-02", "x"))
        self.assertIn("error", chain.request_leave(ALICE, "soon", "later", "x"))

    def test_interns_cannot_list_approvals(self):
        with self.assertRaises(AccessDenied):
            chain.list_pending_approvals(ALICE)
        self.assertTrue(chain.list_my_requests(ALICE)["leave_requests"] is not None)


class IT(unittest.TestCase):
    def test_ticket_after_approval(self):
        r = it.open_it_ticket(BOB, "VPN down", "cannot connect", "urgent")
        out = approve(BOB, r["action_id"])
        self.assertRegex(out["ticket"], r"IT-\d+")
        self.assertTrue(any(t["summary"] == "VPN down" for t in chain.list_my_requests(BOB)["tickets"]))
        self.assertEqual(it.open_it_ticket(BOB, "x", "y", "bogus")["status"], "pending_approval")


class Dispatch(unittest.TestCase):
    def setUp(self):  # no Azure here: make both guardrail calls harmless no-ops
        self.p1 = mock.patch.object(registry, "check_retrieved_content", lambda chunks: None)
        self.p2 = mock.patch.object(registry, "redact_many", lambda texts, cats=None: [t.replace("4111 1111 1111 1111", "[CARD]") for t in texts])
        self.p1.start(); self.p2.start()

    def tearDown(self):
        self.p1.stop(); self.p2.stop()

    def test_model_cannot_supply_identity(self):
        out, denied, _ = registry.dispatch("list_my_requests", json.dumps({"requesting_user": DEV}), ALICE)
        self.assertFalse(denied)
        self.assertNotIn("error", json.loads(out))

    def test_rbac_denial_is_returned_as_data(self):
        out, denied, _ = registry.dispatch("list_pending_approvals", "{}", ALICE)
        self.assertTrue(denied)
        self.assertEqual(json.loads(out)["error"], "access_denied")

    def test_bad_arguments_do_not_crash(self):
        out, _, _ = registry.dispatch("find_free_slots", json.dumps({"duration_minutes": 30}), ALICE)
        self.assertIn("error", json.loads(out))
        out, _, _ = registry.dispatch("nope", "{}", ALICE)
        self.assertIn("error", json.loads(out))

    def test_light_redaction_hits_text_fields_only(self):
        db.run("INSERT INTO demo_emails (owner, sender, subject, date, body, unread) VALUES (?,?,?,?,?,1)",
               (ALICE, "x@y.com", "Card", db.now(), "my card 4111 1111 1111 1111 thanks"))
        out, _, notes = registry.dispatch("search_inbox", json.dumps({"query": "card", "max_results": 3}), ALICE)
        data = json.loads(out)
        self.assertIn("[CARD]", data["messages"][0]["snippet"])
        self.assertEqual(data["messages"][0]["from"], "x@y.com")  # addresses stay readable
        self.assertTrue(any("Prompt Shields" in n for n in notes))

    def test_injection_in_an_email_is_withheld(self):
        from app.guardrails.prompt_shields import PromptInjectionDetected
        def flag(chunks):
            raise PromptInjectionDetected("indirect_injection", {})
        with mock.patch.object(registry, "check_retrieved_content", flag):
            out, denied, notes = registry.dispatch("search_inbox", json.dumps({"query": "pricing", "max_results": 3}), ALICE)
        self.assertIn("withheld", json.loads(out)["error"])
        self.assertTrue(any("blocked" in n for n in notes))

    def test_morning_brief_has_every_section(self):
        out, _, _ = registry.dispatch("get_morning_brief", "{}", CARLA)
        data = json.loads(out)
        for key in ("today", "unread_email", "approvals_waiting_for_you", "your_requests_and_tickets"):
            self.assertIn(key, data)
        self.assertTrue(data["approvals_waiting_for_you"]["pending"] is not None)

    def test_agent_publishes_every_tool_as_a_function(self):
        for spec in registry.TOOL_SPECS:
            json.dumps(spec["parameters"])  # serialisable
            self.assertIn(spec["name"], registry._HANDLERS)


class _Resp:
    def __init__(self, data, status=200):
        self._d, self.status_code = data, status

    def json(self):
        return self._d

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"http {self.status_code}")


class LiveGoogle(unittest.TestCase):
    """Live mode with Google's API replaced by a fake: checks what we send and how we read replies."""

    def setUp(self):
        from app.integrations import gcalendar, gdrive, gmail
        self.calls = []
        self.mods = (gcalendar, gdrive, gmail)
        self.fake_oauth = types.ModuleType("google_oauth_fake")
        self.fake_oauth.fresh_access_token = lambda s: "TOKEN"
        self.patches = [mock.patch.dict(sys.modules, {"app.identity.google_oauth": self.fake_oauth})]
        for p in self.patches:
            p.start()
        self.session = types.SimpleNamespace(
            demo=False, access_token="TOKEN",
            scopes=" ".join("https://www.googleapis.com/auth/" + s for s in
                            ("gmail.send", "gmail.readonly", "calendar.events", "calendar.freebusy", "drive.file")))

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def fake_http(self, replies):
        def handler(method):
            def call(url, **kw):
                self.calls.append((method, url, kw))
                for needle, reply in replies.items():
                    if needle in url:
                        return _Resp(reply)
                return _Resp({}, 404)
            return call
        fake = types.SimpleNamespace(get=handler("GET"), post=handler("POST"), HTTPError=Exception)
        return mock.patch.multiple("app.integrations.gcalendar", httpx=fake), \
            mock.patch.multiple("app.integrations.gdrive", httpx=fake), mock.patch.multiple("app.integrations.gmail", httpx=fake)

    def run_with(self, replies, fn):
        ps = self.fake_http(replies)
        for p in ps:
            p.start()
        try:
            return fn()
        finally:
            for p in ps:
                p.stop()

    def test_calendar_busy_comes_from_freebusy_and_unreadable_invitees_are_reported(self):
        reply = {"calendars": {"primary": {"busy": [{"start": "2030-01-07T10:00:00Z", "end": "2030-01-07T11:00:00Z"}]},
                               "x@gmail.com": {"errors": [{"reason": "notFound"}]}}}
        out = self.run_with({"freeBusy": reply}, lambda: calendar.find_free_slots(BOB, 30, 5, ["x@gmail.com"], session=self.session))
        self.assertEqual(out["mode"], "google")
        self.assertEqual(out["could_not_check_calendars_of"], ["x@gmail.com"])
        self.assertEqual(self.calls[0][2]["headers"]["Authorization"], "Bearer TOKEN")

    def test_event_creation_sends_invites_and_adds_meet(self):
        reply = {"id": "e1", "htmlLink": "https://cal/e1", "hangoutLink": "https://meet/x"}
        start = next_weekday(4).isoformat() + "T11:00"
        card = calendar.propose_meeting(BOB, "Demo", start, 45, ["dana@brightcloth.example"], "Follow-up")
        out = self.run_with({"calendars/primary/events": reply}, lambda: approve(BOB, card["action_id"], self.session))
        method, url, kw = self.calls[-1]
        self.assertEqual(kw["params"]["sendUpdates"], "all")
        self.assertEqual(kw["json"]["attendees"], [{"email": "dana@brightcloth.example"}])
        self.assertIn("conferenceData", kw["json"])
        self.assertEqual(out["link"], "https://cal/e1")

    def test_document_upload_is_multipart_html_converted_to_a_google_doc(self):
        card = docs.draft_document(BOB, "Plan", "# Rollout\n- step **one**\n\nDone")
        out = self.run_with({"upload/drive": {"id": "d1", "webViewLink": "https://docs/d1"}},
                            lambda: approve(BOB, card["action_id"], self.session))
        _, url, kw = self.calls[-1]
        body = kw["content"].decode()
        self.assertIn("application/vnd.google-apps.document", body)
        self.assertIn("<h1>Rollout</h1>", body)
        self.assertIn("<li>step <b>one</b></li>", body)
        self.assertEqual(out["link"], "https://docs/d1")

    def test_missing_scope_is_a_clear_error_not_a_crash(self):
        self.session.scopes = "https://www.googleapis.com/auth/gmail.send"
        with self.assertRaises(PermissionError):
            calendar.list_events(BOB, 1, session=self.session)
        out, denied, _ = (lambda: registry.dispatch("list_events", '{"days_ahead": 1}', BOB, self.session))()
        self.assertIn("permission", json.loads(out)["error"].lower())

    def test_gmail_search_reads_headers_and_unread_flag(self):
        listing = {"messages": [{"id": "abc123"}]}
        meta = {"id": "abc123", "snippet": "hello", "labelIds": ["UNREAD"],
                "payload": {"headers": [{"name": "From", "value": "d@x.com"}, {"name": "Subject", "value": "Hi"}]}}
        def fn():
            return email.search_inbox(BOB, "is:unread", 5, session=self.session)
        out = self.run_with({"messages/abc123": meta, "/messages": listing}, fn)
        self.assertEqual(out["messages"][0]["subject"], "Hi")
        self.assertTrue(out["messages"][0]["unread"])

    def test_demo_sessions_never_touch_google(self):
        demo = types.SimpleNamespace(demo=True, access_token="", scopes="")
        card = email.draft_email(BOB, "x@y.com", "s", "b")
        out = approve(BOB, card["action_id"], demo)
        self.assertTrue(out["simulated"])


if __name__ == "__main__":
    unittest.main()
