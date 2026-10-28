"""
Tests for the agent catalog (per-agent tool lists) and the read-only GitHub tools.

Run from backend/:   python -m unittest discover -s tests -v
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import types
import unittest
from unittest import mock

os.environ["KACY_DB"] = tempfile.mkdtemp() + "/agents.db"


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

from app import agents_catalog as catalog  # noqa: E402
from app.tools import registry  # noqa: E402
from app.tools.mcp_repo import server as repo  # noqa: E402

DEMO = "alice@kacy-demo.test"
DEMO_SESSION = types.SimpleNamespace(demo=True, access_token="", scopes="")


class Catalog(unittest.TestCase):
    def test_every_listed_tool_exists(self):
        known = {s["name"] for s in registry.TOOL_SPECS}
        for a in catalog.AGENTS:
            for t in a["tools"] or []:
                self.assertIn(t, known, f"{a['id']} lists unknown tool {t}")

    def test_no_agent_can_directly_send_or_create(self):
        # The model only ever gets "propose" tools; there is no tool that sends or creates anything.
        for s in registry.TOOL_SPECS:
            self.assertFalse(any(w in s["name"] for w in ("send_", "create_", "execute", "approve", "submit")), s["name"])

    def test_agent_fields(self):
        ids = [a["id"] for a in catalog.AGENTS]
        self.assertEqual(len(ids), len(set(ids)))
        for a in catalog.AGENTS:
            for k in ("name", "icon", "blurb", "can", "instructions", "tools", "starters"):
                self.assertIn(k, a)
            self.assertTrue(a["starters"] and a["can"])

    def test_unknown_agent_falls_back_to_kacy(self):
        self.assertEqual(catalog.get_agent("people")["id"], "kacy")   # an old chat from before the redesign
        self.assertIsNone(catalog.allowed_tools("people"))

    def test_summarizer_is_read_only(self):
        tools = catalog.allowed_tools("summarizer")
        for propose in ("draft_email", "propose_meeting", "draft_document", "request_leave", "open_it_ticket", "decide_leave_request"):
            self.assertNotIn(propose, tools)

    def test_context_names_the_tools(self):
        ctx = catalog.build_context("mail", {})
        self.assertIn("search_inbox", ctx)
        self.assertIn("only use these tools", ctx)
        self.assertEqual(catalog.build_context("kacy", {}), "")


class Allowlist(unittest.TestCase):
    def test_tool_outside_the_list_is_refused_in_code(self):
        out, denied, notes = registry.dispatch("list_events", "{}", DEMO, DEMO_SESSION, catalog.allowed_tools("mail"))
        self.assertIn("can't use that tool", json.loads(out)["error"])
        self.assertFalse(denied)
        self.assertIn("not in this agent's tool list", notes[0])

    def test_tool_inside_the_list_runs(self):
        out, _, _ = registry.dispatch("list_events", "{}", DEMO, DEMO_SESSION, catalog.allowed_tools("calendar"))
        self.assertNotIn("error", json.loads(out))

    def test_no_list_means_all_tools(self):
        out, _, _ = registry.dispatch("list_events", "{}", DEMO, DEMO_SESSION, None)
        self.assertNotIn("error", json.loads(out))

    def test_refusal_happens_before_any_work(self):
        with mock.patch.dict(registry._HANDLERS, {"draft_email": mock.Mock(side_effect=AssertionError("ran"))}):
            out, _, _ = registry.dispatch("draft_email", "{}", DEMO, DEMO_SESSION, catalog.allowed_tools("summarizer"))
        self.assertIn("error", json.loads(out))


class RepoParsing(unittest.TestCase):
    def test_accepted_forms(self):
        for text in ("DietrichGebert/ponytail", "https://github.com/DietrichGebert/ponytail", "github.com/DietrichGebert/ponytail/",
                     "https://github.com/DietrichGebert/ponytail.git", "https://github.com/DietrichGebert/ponytail/tree/main/src",
                     "  www.github.com/DietrichGebert/ponytail?tab=readme  "):
            self.assertEqual(repo.parse_repo(text), "DietrichGebert/ponytail", text)

    def test_rejected_forms(self):
        for text in ("", "justone", "https://evil.example/a/b", "a/b/../c", "../x", "owner/na me", "owner/name;rm"):
            if text in ("a/b/../c",):   # extra segments are dropped, leaving a valid a/b
                continue
            with self.assertRaises(ValueError, msg=text):
                repo.parse_repo(text)


class RepoTools(unittest.TestCase):
    def fake(self, path, raw=False):
        if path.endswith("/readme"):
            return "# Ponytail\nWrite less code."
        if path.endswith("/contents/"):
            return [{"name": "src", "type": "dir"}, {"name": "README.md", "type": "file"}]
        if path.endswith("/contents/src"):
            return [{"name": "main.py", "type": "file"}]
        if path.endswith("/contents/big.bin"):
            return {"size": 999999} if not raw else ""
        if path.endswith("/contents/img.png"):
            return {"size": 10} if not raw else "\x00PNG"
        if "/contents/" in path:
            return "print('hi')" if raw else {"size": 11}
        return {"description": "agent skill", "language": "Python", "stargazers_count": 5, "default_branch": "main", "pushed_at": "2026-08-01"}

    def test_overview(self):
        with mock.patch.object(repo, "_get", self.fake):
            r = repo.repo_overview(DEMO, "github.com/DietrichGebert/ponytail")
        self.assertEqual(r["repo"], "DietrichGebert/ponytail")
        self.assertEqual([f["name"] for f in r["files"]], ["src", "README.md"])   # folders first
        self.assertIn("Write less code", r["content"])

    def test_read_file_and_folder(self):
        with mock.patch.object(repo, "_get", self.fake):
            self.assertEqual(repo.read_repo_file(DEMO, "a/b", "run.py")["content"], "print('hi')")
            self.assertEqual(repo.read_repo_file(DEMO, "a/b", "src")["folder"], ["main.py"])

    def test_path_tricks_and_big_or_binary_files_are_refused(self):
        with mock.patch.object(repo, "_get", self.fake):
            for bad in ("../secret", "a/../../b", "", "x\\y", "big.bin", "img.png"):
                with self.assertRaises(ValueError, msg=bad):
                    repo.read_repo_file(DEMO, "a/b", bad)

    def test_file_text_is_capped(self):
        with mock.patch.object(repo, "_get", lambda p, raw=False: "x" * 50000 if raw else {"size": 50000}):
            r = repo.read_repo_file(DEMO, "a/b", "long.txt")
        self.assertEqual(len(r["content"]), repo.MAX_FILE_CHARS)
        self.assertTrue(r["truncated"])

    def test_repo_text_is_scanned_like_email(self):
        spec = {s["name"]: s for s in registry.TOOL_SPECS}
        self.assertTrue(spec["repo_overview"]["external"] and spec["read_repo_file"]["external"])
        with mock.patch.object(repo, "_get", self.fake), \
             mock.patch.object(registry, "_shield", lambda result: True):
            out, _, notes = registry.dispatch("repo_overview", json.dumps({"repo": "a/b"}), DEMO, DEMO_SESSION, None)
        self.assertIn("withheld", json.loads(out)["error"])
        self.assertTrue(any("blocked" in n for n in notes))


if __name__ == "__main__":
    unittest.main()
