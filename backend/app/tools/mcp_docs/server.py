"""Document drafting/summarizing tool. Third new agentic action — read-only (no approval gate needed)."""
from __future__ import annotations


def draft_document(bullet_points: list[str], title: str) -> dict:
    """
    Turns bullet points into a formatted draft. The actual drafting happens
    in the agent (it's a model generation, not a lookup) — this function is
    the tool-call contract the agent's draft gets validated/stored against.
    """
    return {"title": title, "bullet_points": bullet_points, "status": "drafted"}


def summarize_document(source_text: str, max_words: int = 200) -> dict:
    return {"source_length_chars": len(source_text), "max_words": max_words, "status": "requested"}
