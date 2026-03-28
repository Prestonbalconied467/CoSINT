"""
tools/worklog_base.py

Shared boilerplate for in-process worklog stores (notes and todo).
Provides thread-safe ID generation and a UTC timestamp helper.
"""

from __future__ import annotations

from datetime import datetime, timezone
import asyncio


def make_id_factory(prefix: str):
    """Return an async thread-safe callable that yields sequentially numbered IDs.

    Example:
        next_id = make_id_factory("NT")
        await next_id()  # "NT-0001"
        await next_id()  # "NT-0002"
    """
    _counter = 0
    _lock = asyncio.Lock()

    async def _next_id() -> str:
        nonlocal _counter
        async with _lock:
            _counter += 1
            return f"{prefix}-{_counter:04d}"

    return _next_id


def utc_now() -> str:
    """Return the current UTC time as an ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()


# ── Todo helpers ───────────────────────────────────────────────────────────────

VALID_PRIORITIES = {"low", "normal", "high"}
VALID_STATUSES = {
    "open",
    "in_progress",
    "done",
    "canceled_manual",
    "canceled_scope",
    "canceled_duplicate",
}


def validate_priority(priority: str) -> str:
    value = (priority or "normal").strip().lower()
    if value not in VALID_PRIORITIES:
        raise ValueError(f"invalid priority '{priority}' (use: low, normal, high)")
    return value


def validate_status(status: str) -> str:
    value = (status or "open").strip().lower()
    if value not in VALID_STATUSES:
        raise ValueError(
            f"invalid status '{status}' "
            "(use: open, in_progress, done, canceled_manual, canceled_scope, canceled_duplicate)"
        )
    return value


# ── Notes helpers ──────────────────────────────────────────────────────────────


def normalize_tags(raw: str) -> list[str]:
    parts = (raw or "").replace(";", ",").split(",")
    tags = []
    for chunk in parts:
        tag = chunk.strip().lower()
        if tag and tag not in tags:
            tags.append(tag)
    return tags[:10]
