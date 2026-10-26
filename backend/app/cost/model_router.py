"""
Model router stand-in. In production this is Foundry's built-in Model Router
deployment (see docs/architecture.md) — a single endpoint that routes each
request to the cheapest model that can handle it. Until that's configured,
this is a crude heuristic so the routing decision is still visible in traces.
"""
from __future__ import annotations

SIMPLE_QUERY_MAX_CHARS = 80


def choose_model(message: str, small_model: str, large_model: str) -> str:
    """Longer / more complex-looking messages go to the larger model."""
    if len(message) <= SIMPLE_QUERY_MAX_CHARS and "?" not in message[:-1]:
        return small_model
    return large_model
