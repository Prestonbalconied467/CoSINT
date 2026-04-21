"""
agent_runtime/investigation/events.py

Event-log, worklog snapshot, and case-relation helpers.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..models import AgentEvent
from ..targeting import build_relation_summary

if TYPE_CHECKING:
    from mcp import ClientSession
    from ..models import CaseFile
    from ..scanner.context import ScanContext


def record_event(
    events: list[AgentEvent],
    max_events: int,
    round_num: int,
    phase: str,
    detail: str,
) -> None:
    events.append(AgentEvent(round_num=round_num, phase=phase, detail=detail))
    if len(events) > max_events:
        del events[:-max_events]


async def capture_worklog_snapshots(
    session: ClientSession,
    case_file: CaseFile,
) -> None:
    from ..mcp_runtime import call_mcp_tool

    if case_file.todo_snapshot is None:
        try:
            case_file.todo_snapshot = await call_mcp_tool(
                session, "osint_todo_list", {"status": "all"}
            )
        except Exception as exc:
            case_file.todo_snapshot = f"Todo snapshot unavailable: {exc}"

    if case_file.notes_snapshot is None:
        try:
            case_file.notes_snapshot = await call_mcp_tool(
                session, "osint_notes_list", {"tag": "", "limit": 200}
            )
        except Exception as exc:
            case_file.notes_snapshot = f"Notes snapshot unavailable: {exc}"


def append_case_relation(ctx: ScanContext) -> None:
    ctx.case_file.events = list(ctx.events)
    ctx.case_file.relation = build_relation_summary(
        primary_target=ctx.target,
        related_targets=list(ctx.extra_targets),
        correlate_targets=ctx.correlate_targets,
        evidence=ctx.case_file.evidence_list(),
    )


__all__ = ["append_case_relation", "capture_worklog_snapshots", "record_event"]
