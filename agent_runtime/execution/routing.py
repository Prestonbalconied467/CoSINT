"""
agent_runtime/execution/routing.py

Tool-call routing and preflight gating for scanner rounds.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..scanner.context import ScanContext
from ..subagents import parse_subagent_call, preflight_subagent_calls
from .preflight import apply_dedupe_preflight, apply_scope_preflight


def _serialize_tool_call(tc: Any) -> dict[str, Any]:
    return {
        "id": tc.id,
        "type": "function",
        "function": {"name": tc.function.name, "arguments": tc.function.arguments},
    }


def _should_handle_no_tools(
    executable_mcp_calls: list,
    approved_subagent_calls: list,
    blocked_subagent_tool_messages: list,
) -> bool:
    return (
        not executable_mcp_calls
        and not approved_subagent_calls
        and not blocked_subagent_tool_messages
    )


@dataclass
class RoutingResult:
    executable_mcp_calls: list
    approved_subagent_calls: list
    blocked_subagent_calls: list
    blocked_subagent_tool_messages: list
    blocked_feedback_lines: list
    answered_tool_calls: list
    allowed_scope_decisions: dict | None


async def route_tool_calls(
    ctx: "ScanContext", msg: Any, round_num: int
) -> RoutingResult:
    all_tool_calls = list(msg.tool_calls or [])
    subagent_calls, mcp_tool_calls = [], []
    for tc in all_tool_calls:
        parsed = parse_subagent_call(tc)
        if parsed is not None:
            subagent_calls.append((tc, parsed))
        else:
            mcp_tool_calls.append(tc)

    approved_subagent_calls = list(subagent_calls)
    blocked_subagent_calls, blocked_subagent_tool_messages = [], []
    blocked_feedback_lines = []
    executable_mcp_calls = list(mcp_tool_calls)
    allowed_scope_decisions = None

    if mcp_tool_calls:
        runtime_scope = await apply_scope_preflight(
            tool_calls=mcp_tool_calls,
            round_num=round_num,
            target=ctx.target,
            target_type=ctx.target_type,
            scope_mode=ctx.scope_mode,
            extra_targets=ctx.extra_targets,
            case_file=ctx.case_file,
            stats=ctx.stats,
            events=ctx.events,
            event_log_size=ctx.event_log_size,
            evidence_by_id=ctx.evidence_by_id,
            current_phase_label=ctx.current_phase_label,
            approved_domains=ctx.confidence_approved_domains,
            model=ctx.model,
            confidence_log=ctx.confidence_log,
            llm_usage=ctx.llm_usage,
        )
        ctx.current_phase_label = runtime_scope.current_phase_label
        blocked_feedback_lines = runtime_scope.blocked_feedback_lines
        ctx.scope_blocked_domains.update(runtime_scope.blocked_domains)
        allowed_scope_decisions = runtime_scope.scope_preflight.allowed_scope_decisions

        dedupe = apply_dedupe_preflight(
            tool_calls=runtime_scope.scope_preflight.executable_tool_calls,
            seen_call_signatures=ctx.seen_call_signatures,
            cap=ctx.max_tool_calls,
            stats=ctx.stats,
            events=ctx.events,
            event_log_size=ctx.event_log_size,
            round_num=round_num,
        )
        executable_mcp_calls = list(dedupe.tool_calls or [])

    if subagent_calls:
        subagent_preflight = await preflight_subagent_calls(
            subagent_calls=subagent_calls,
            round_num=round_num,
            target=ctx.target,
            target_type=ctx.target_type,
            scope_mode=ctx.scope_mode,
            extra_targets=ctx.extra_targets,
            case_file=ctx.case_file,
            stats=ctx.stats,
            events=ctx.events,
            event_log_size=ctx.event_log_size,
            model=ctx.model,
            confidence_log=ctx.confidence_log,
            llm_usage=ctx.llm_usage,
        )
        approved_subagent_calls = list(subagent_preflight.approved_calls)
        blocked_subagent_calls = list(subagent_preflight.blocked_calls)
        blocked_subagent_tool_messages = list(subagent_preflight.blocked_tool_messages)
        blocked_feedback_lines.extend(subagent_preflight.blocked_feedback_lines)

    answered_tool_calls = (
        [_serialize_tool_call(tc) for tc in executable_mcp_calls]
        + [_serialize_tool_call(tc) for tc, _ in approved_subagent_calls]
        + [_serialize_tool_call(tc) for tc in blocked_subagent_calls]
    )

    return RoutingResult(
        executable_mcp_calls=executable_mcp_calls,
        approved_subagent_calls=approved_subagent_calls,
        blocked_subagent_calls=blocked_subagent_calls,
        blocked_subagent_tool_messages=blocked_subagent_tool_messages,
        blocked_feedback_lines=blocked_feedback_lines,
        answered_tool_calls=answered_tool_calls,
        allowed_scope_decisions=allowed_scope_decisions,
    )


__all__ = [
    "RoutingResult",
    "_serialize_tool_call",
    "_should_handle_no_tools",
    "route_tool_calls",
]
