"""
tools/notes.py  -  Investigation note workspace tools
Tools: osint_notes_add, osint_notes_list, osint_notes_delete, osint_notes_clear
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Annotated

from mcp.server.fastmcp import FastMCP
from pydantic import Field

from tools.helper.worklog_base import make_id_factory, utc_now, normalize_tags


@dataclass
class _NoteItem:
    note_id: str
    title: str
    content: str
    tags: list[str] = field(default_factory=list)
    created_at: str = field(default_factory=utc_now)


_NOTES: dict[str, _NoteItem] = {}
_LOCK = asyncio.Lock()
_next_id = make_id_factory("NT")


def register(mcp: FastMCP) -> None:
    @mcp.tool(annotations={"openWorldHint": False})
    async def osint_notes_add(
        title: Annotated[str, Field(description="Note title")],
        content: Annotated[str, Field(description="Note body")],
        tags: Annotated[str, Field(description="Optional comma-separated tags")] = "",
    ) -> str:
        """Store an investigator note in the current MCP session (in-memory, session-scoped).

        Use structured tags to organize notes for later retrieval:
          tags="plan"     → investigation execution plan and step checklist
          tags="anomaly"  → unexpected findings that need QA/report attention
          tags="pivot"    → open pivots that couldn't be followed immediately
          tags="finding"  → key [HIGH]/[MED] results to preserve across context compression
        Notes persist for the duration of the MCP server process only.
        """
        clean_title = (title or "").strip()
        clean_content = (content or "").strip()
        if not clean_title:
            return "Note add failed: title cannot be empty."
        if not clean_content:
            return "Note add failed: content cannot be empty."

        async with _LOCK:
            note_id = await _next_id()
            _NOTES[note_id] = _NoteItem(
                note_id=note_id,
                title=clean_title,
                content=clean_content,
                tags=normalize_tags(tags),
            )
        return f"Note created: {note_id} | {clean_title}"

    @mcp.tool(annotations={"readOnlyHint": True, "openWorldHint": False})
    async def osint_notes_list(
        tag: Annotated[str, Field(description="Optional tag filter")] = "",
        limit: Annotated[
            int, Field(description="Max notes to return", ge=1, le=200)
        ] = 20,
    ) -> str:
        """List investigation notes, optionally filtered by tag.

        Use before writing the report to sweep for missed items:
          osint_notes_list(tag="anomaly") → populate the QA Anomalies field
          osint_notes_list(tag="pivot")   → populate Pivots Taken / open leads
          osint_notes_list(tag="finding") → cross-check Key Findings completeness
          osint_notes_list(tag="plan")    → verify all planned steps were completed
        Returns up to limit notes sorted newest-first.
        """
        clean_tag = (tag or "").strip().lower()
        async with _LOCK:
            notes = sorted(_NOTES.values(), key=lambda x: x.note_id, reverse=True)

        if clean_tag:
            notes = [n for n in notes if clean_tag in n.tags]

        notes = notes[:limit]
        if not notes:
            return "No notes found."

        lines = [f"Notes ({len(notes)}):"]
        for note in notes:
            short = note.content.replace("\n", " ")[:180]
            tags = ",".join(note.tags) if note.tags else "none"
            lines.append(f"- {note.note_id} | {note.title} | tags={tags} | {short}")
        return "\n".join(lines)

    @mcp.tool(annotations={"destructiveHint": True, "openWorldHint": False})
    async def osint_notes_delete(
        note_id: Annotated[str, Field(description="Note ID, e.g. NT-0001")],
    ) -> str:
        """Delete a single note by its ID (e.g. NT-0001).

        Use to remove false-positive anomaly notes or superseded plan entries.
        """
        clean_id = (note_id or "").strip()
        async with _LOCK:
            note = _NOTES.pop(clean_id, None)
        if note is None:
            return f"Note delete failed: {clean_id} not found."
        return f"Note deleted: {clean_id}"

    @mcp.tool(annotations={"destructiveHint": True, "openWorldHint": False})
    async def osint_notes_clear() -> str:
        """Clear all notes for the current MCP session.

        Use only at the explicit end of an investigation or to reset between targets.
        This is irreversible within the session.
        """
        async with _LOCK:
            cleared = len(_NOTES)
            _NOTES.clear()
        return f"Cleared {cleared} note(s)."
