"""
Semantic cache — catches the "10,000 employees all say Hi" problem from the
video. Skip wiring a real Redis instance for solo-dev testing (it's pure cost
with no payoff at 1 user); this module degrades to an in-process dict cache
when REDIS_URL isn't set, so the code path is exercised either way.
"""
from __future__ import annotations

import hashlib
import os

_LOCAL_CACHE: dict[str, str] = {}

SIMILARITY_BUCKET_PHRASES = {
    "hi", "hello", "hey", "good morning", "good afternoon", "thanks", "thank you",
}


def _key_for(message: str) -> str | None:
    """
    Cheap stand-in for real semantic similarity: bucket common greetings/thanks
    by normalized text. Swap for an embedding-similarity lookup against Redis
    (see docs/architecture.md section 2 / cost control) once this needs to
    scale past the demo.
    """
    normalized = message.strip().lower().rstrip("!.")
    if normalized in SIMILARITY_BUCKET_PHRASES:
        return hashlib.sha256(normalized.encode()).hexdigest()
    return None


def get(message: str) -> str | None:
    key = _key_for(message)
    if key is None:
        return None
    if os.environ.get("REDIS_URL"):
        raise NotImplementedError("Wire up redis-py against REDIS_URL here")
    return _LOCAL_CACHE.get(key)


def set(message: str, response: str) -> None:
    key = _key_for(message)
    if key is None:
        return
    if os.environ.get("REDIS_URL"):
        raise NotImplementedError("Wire up redis-py against REDIS_URL here")
    _LOCAL_CACHE[key] = response
