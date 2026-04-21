"""
agent_runtime/investigation/interactive.py

Interactive pause and operator-question helpers for the scanner loop.
"""

from __future__ import annotations

import re
from enum import Enum
from typing import TYPE_CHECKING, Any, Final

from ..display import interactive_pause
from .events import record_event

if TYPE_CHECKING:
    from ..execution.routing import RoutingResult
    from ..scanner.context import ScanContext

_WORKLOG_PREFIXES: Final[tuple[str, ...]] = ("osint_notes_", "osint_todo_")

_QUESTION_RE: Final = re.compile(
    r"\?$"
    r"|\bplease specify\b"
    r"|\bplease (let me know|advise|confirm|indicate)\b"
    r"|\bdo you (want|wish|prefer|need)\b"
    r"|\b(shall i|should i|would you like)\b"
    r"|\b(enter enrichment|proceed to phase|ready to proceed)\b"
    r"|\bawait(ing)? (your|further|operator)\b"
    r"|\b(specify|indicate) if you\b",
    re.IGNORECASE,
)


class InteractivePauseAction(str, Enum):
    NONE = "none"
    CONTINUE_ROUND = "continue_round"


def extract_next_hints(content: str | None, max_hints: int = 3) -> list[str]:
    if not content:
        return []

    hints: list[str] = []
    for raw_line in content.strip().splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if re.match(r"^(checking|found|phase complete|phase summary)\b", line, re.IGNORECASE):
            continue
        if not re.search(
            r"\b(next|will|plan|going to|proceed|then|after that|follow-up|"
            r"investigate|examine|look into|pivot)\b",
            line,
            re.IGNORECASE,
        ):
            continue
        if line not in hints:
            hints.append(line)
        if len(hints) >= max_hints:
            break

    return hints


def looks_like_agent_question(content: str | None) -> bool:
    if not content:
        return False

    tail = content.strip()[-800:]
    lines = [line.strip() for line in tail.splitlines() if line.strip()]
    return any(_QUESTION_RE.search(line) for line in lines[-4:])


async def handle_interactive_pause(
    ctx: "ScanContext",
    msg: Any,
    routing: "RoutingResult",
    round_tool_names: list[str],
    round_num: int,
) -> InteractivePauseAction:
    from ..execution.routing import _should_handle_no_tools

    all_worklog = bool(round_tool_names) and all(
        any(t.startswith(p) for p in _WORKLOG_PREFIXES) for t in round_tool_names
    )
    had_real_calls = (
        routing.executable_mcp_calls or routing.approved_subagent_calls
    ) and not all_worklog

    if not had_real_calls:
        return InteractivePauseAction.NONE

    next_hints = extract_next_hints(msg.content)
    directive = interactive_pause(
        last_content=msg.content,
        next_tools=round_tool_names,
        next_hints=next_hints,
    )

    if directive:
        ctx.convo.append(
            {"role": "user", "content": f"[INVESTIGATOR DIRECTIVE] {directive}"}
        )
        ctx.stats.directives_issued += 1
        ctx.directive_pending = True
        record_event(
            ctx.events,
            ctx.event_log_size,
            round_num + 1,
            "directive",
            directive,
        )
        if _should_handle_no_tools(
            routing.executable_mcp_calls,
            routing.approved_subagent_calls,
            routing.blocked_subagent_tool_messages,
        ):
            return InteractivePauseAction.CONTINUE_ROUND
    else:
        ctx.convo.append(
            {
                "role": "user",
                "content": "Confirmed. Proceed with the planned investigation now.",
            }
        )
    return InteractivePauseAction.NONE


__all__ = [
    "InteractivePauseAction",
    "extract_next_hints",
    "looks_like_agent_question",
    "handle_interactive_pause",
]
