"""
Shared shape for every mocked tool. Each tool in backend/app/tools/mcp_* is a
standalone MCP server (one process, one `mcp.server.Server` instance) so it
can be swapped for a real HR/Jira/Slack integration later without touching
the agent — the agent only knows about the MCP endpoint, not the backend
behind it.

For now each tool keeps its state in-memory via `ToolStore`, seeded with demo
data, so the whole system is runnable without any external service.
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass, field


@dataclass
class ToolStore:
    """In-memory store standing in for a real backend (Workday, Jira, etc.)."""
    _id_counter: itertools.count = field(default_factory=lambda: itertools.count(1))
    records: dict[int, dict] = field(default_factory=dict)

    def next_id(self) -> int:
        return next(self._id_counter)

    def add(self, record: dict) -> dict:
        record_id = self.next_id()
        record = {"id": record_id, **record}
        self.records[record_id] = record
        return record

    def get(self, record_id: int) -> dict | None:
        return self.records.get(record_id)

    def all(self) -> list[dict]:
        return list(self.records.values())
