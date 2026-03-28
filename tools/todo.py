"""
tools/todo.py  -  Investigation task tracking tools
Tools: osint_todo_add, osint_todo_update, osint_todo_list, osint_todo_summary, osint_todo_clear
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Annotated

from mcp.server.fastmcp import FastMCP
from pydantic import Field

from tools.helper.worklog_base import (
    make_id_factory,
    utc_now,
    validate_priority,
    validate_status,
    VALID_STATUSES,
)


@dataclass
class _TodoItem:
    todo_id: str
    title: str
    priority: str
    status: str = "open"
    note: str = ""
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)


_TODOS: dict[str, _TodoItem] = {}
_LOCK = asyncio.Lock()
_next_id = make_id_factory("TD")


def register(mcp: FastMCP) -> None:
    @mcp.tool()
    async def osint_todo_add(
        title: Annotated[str, Field(description="Task title")],
        priority: Annotated[
            str, Field(description="Priority: low, normal, high")
        ] = "normal",
        note: Annotated[str, Field(description="Optional context or rationale")] = "",
    ) -> str:
        """Add a tracked investigation task to the runtime todo list.

        priority values: critical, high, medium, low
        Use for tasks needing status tracking (open → in_progress → done/canceled).
        Prefer osint_notes_add(tags="pivot") for open pivots and osint_notes_add(tags="plan")
          for the execution checklist.
        """
        try:
            clean_title = (title or "").strip()
            if not clean_title:
                return "Todo add failed: title cannot be empty."
            clean_priority = validate_priority(priority)
            async with _LOCK:
                todo_id = await _next_id()
                _TODOS[todo_id] = _TodoItem(
                    todo_id=todo_id,
                    title=clean_title,
                    priority=clean_priority,
                    note=(note or "").strip(),
                )
            return f"Todo created: {todo_id} | [{clean_priority}] {clean_title}"
        except ValueError as exc:
            return f"Todo add failed: {exc}"

    @mcp.tool()
    async def osint_todo_update(
        todo_id: Annotated[str, Field(description="Todo ID, e.g. TD-0001")],
        status: Annotated[str, Field(description="New status")],
        note: Annotated[str, Field(description="Optional update note")] = "",
        priority: Annotated[
            str, Field(description="Optional new priority: low, normal, high")
        ] = "",
    ) -> str:
        """Update a todo item's status and/or priority.

        Status values: open, in_progress, done, canceled_scope, canceled_duplicate
          canceled_scope → pivot was out of scope for this investigation
          canceled_duplicate → already covered by another todo or tool call
        """
        try:
            clean_status = validate_status(status)
            clean_priority = (
                validate_priority(priority) if (priority or "").strip() else None
            )
            async with _LOCK:
                item = _TODOS.get((todo_id or "").strip())
                if not item:
                    return f"Todo update failed: {todo_id} not found."
                item.status = clean_status
                if clean_priority is not None:
                    item.priority = clean_priority
                if (note or "").strip():
                    item.note = (note or "").strip()
                item.updated_at = utc_now()
            return f"Todo updated: {item.todo_id} | status={item.status} | priority={item.priority}"
        except ValueError as exc:
            return f"Todo update failed: {exc}"

    @mcp.tool(annotations={"readOnlyHint": True})
    async def osint_todo_list(
        status: Annotated[str, Field(description="Filter by status or 'all'")] = "all",
    ) -> str:
        """List investigation todo items, optionally filtered by status.

        Use osint_todo_summary first for a quick count, then list specific statuses.
        Before the report: list all open/in_progress todos to ensure nothing was missed.
        """
        status_filter = (status or "all").strip().lower()
        async with _LOCK:
            items = sorted(
                _TODOS.values(), key=lambda x: (x.status, x.priority, x.todo_id)
            )

        if status_filter != "all":
            if status_filter not in VALID_STATUSES:
                return (
                    "Todo list failed: invalid status filter "
                    f"'{status}'. Use 'all' or one of: {', '.join(sorted(VALID_STATUSES))}."
                )
            items = [x for x in items if x.status == status_filter]

        if not items:
            return "No todo items."

        lines = [f"Todo items ({len(items)}):"]
        for item in items:
            lines.append(
                f"- {item.todo_id} | [{item.priority}] {item.status} | {item.title}"
                + (f" | note: {item.note}" if item.note else "")
            )
        return "\n".join(lines)

    @mcp.tool(annotations={"readOnlyHint": True})
    async def osint_todo_summary() -> str:
        """Return todo counts grouped by status for a quick investigation progress check.

        Use at the start of each new round to assess remaining work before deciding
        whether to continue investigating or move to the report.
        """
        async with _LOCK:
            items = list(_TODOS.values())

        if not items:
            return "Todo summary: no items."

        counts: dict[str, int] = {k: 0 for k in sorted(VALID_STATUSES)}
        for item in items:
            counts[item.status] = counts.get(item.status, 0) + 1

        chunks = [f"{k}={v}" for k, v in counts.items() if v > 0]
        return f"Todo summary ({len(items)} total): " + ", ".join(chunks)

    @mcp.tool(annotations={"destructiveHint": True})
    async def osint_todo_clear() -> str:
        """Clear all todo items for the current MCP session.

        Use only at the explicit end of an investigation or to reset between targets.
        Irreversible within the session.
        """
        async with _LOCK:
            cleared = len(_TODOS)
            _TODOS.clear()
        return f"Cleared {cleared} todo item(s)."
