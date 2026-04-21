"""
agent_runtime/subagents/dispatch_preflight.py

Subagent-call parsing and scope preflight gates.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from ..models import AgentEvent, CaseFile, ScanStats
from ..scope import (
    classify_scope_preflight,
    parse_tool_call_args,
    split_scope_meta_args,
    summarize_tool_call,
)
from ..scanner.case_log import log_scope_decision
from ..investigation.events import record_event
from .registry import is_scope_exempt_subagent


@dataclass
class SubagentPreflightResult:
    approved_calls: list[tuple[Any, tuple[str, str, str]]]
    blocked_calls: list[Any]
    blocked_tool_messages: list[dict[str, str]]
    blocked_feedback_lines: list[str]


def parse_subagent_call(tool_call: Any) -> tuple[str, str, str] | None:
    try:
        if tool_call.function.name != "call_subagent":
            return None
        args = json.loads(tool_call.function.arguments or "{}")
        return args.get("agent", ""), args.get("task", ""), args.get("context", "")
    except Exception:
        return None


async def preflight_subagent_calls(
    subagent_calls: list[tuple[Any, tuple[str, str, str]]],
    round_num: int,
    target: str,
    target_type: str,
    scope_mode: str,
    extra_targets: list[str],
    case_file: CaseFile,
    stats: ScanStats,
    events: list[AgentEvent],
    event_log_size: int,
    model: str = "",
    confidence_log: Any = None,
    llm_usage: Any = None,
) -> SubagentPreflightResult:
    if not subagent_calls:
        return SubagentPreflightResult([], [], [], [])

    approved: list[tuple[Any, tuple[str, str, str]]] = []
    gated: list[tuple[Any, tuple[str, str, str]]] = []

    for tc, parsed in subagent_calls:
        agent_name = (parsed[0] or "").strip()
        if is_scope_exempt_subagent(agent_name):
            approved.append((tc, parsed))
        else:
            gated.append((tc, parsed))

    if not gated:
        return SubagentPreflightResult(approved, [], [], [])

    classified = await classify_scope_preflight(
        tool_calls=[tc for tc, _ in gated],
        primary_target=target,
        primary_type=target_type,
        related_targets=list(extra_targets),
        evidence=case_file.evidence_list(),
        scope_mode=scope_mode,
        model=model,
        confidence_log=confidence_log,
        usage=llm_usage,
    )

    blocked_by_id = {id(b.tool_call): b for b in classified.blocked_calls}
    blocked_calls: list[Any] = []
    blocked_tool_messages: list[dict[str, str]] = []
    blocked_feedback_lines: list[str] = []

    for tc, parsed in gated:
        raw_args = parse_tool_call_args(tc)
        exec_args, _ = split_scope_meta_args(raw_args)
        tested = summarize_tool_call("call_subagent", exec_args)
        decision = classified.allowed_scope_decisions.get(id(tc))

        if decision is not None:
            log_scope_decision(
                round_num=round_num,
                source="root-subagent-gate",
                tested=tested,
                scope_decision=decision,
                requested_reason=(raw_args or {}).get("reason", ""),
            )
            approved.append((tc, parsed))
            continue

        blocked = blocked_by_id.get(id(tc))
        if blocked is None:
            continue

        decision = blocked.decision
        log_scope_decision(
            round_num=round_num,
            source="root-subagent-gate",
            tested=tested,
            scope_decision=decision,
            requested_reason=(raw_args or {}).get("reason", ""),
        )
        agent_name, task, _ = parsed
        stats.tools_blocked += 1
        blocked_calls.append(tc)
        blocked_feedback_lines.append(f"{tested}: {decision.reason}")
        record_event(
            events,
            event_log_size,
            round_num + 1,
            "subagent-blocked",
            f"{agent_name}: {decision.reason}",
        )
        blocked_tool_messages.append(
            {
                "role": "tool",
                "tool_call_id": tc.id,
                "name": "call_subagent",
                "content": (
                    f"[SUBAGENT BLOCKED] {decision.reason}\n"
                    f"Agent: {agent_name}\n"
                    f"Task: {task}"
                ),
            }
        )

    return SubagentPreflightResult(
        approved_calls=approved,
        blocked_calls=blocked_calls,
        blocked_tool_messages=blocked_tool_messages,
        blocked_feedback_lines=blocked_feedback_lines,
    )


__all__ = ["SubagentPreflightResult", "parse_subagent_call", "preflight_subagent_calls"]

