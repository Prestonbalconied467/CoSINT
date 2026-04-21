"""
agent_runtime/execution/preflight.py

Pre-execution helpers for scanner tool calls:
- scope preflight classification and blocked-call feedback
- dedupe/cap preflight
- lightweight artifact extraction helper reused by MCP execution
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from shared.url_utils import extract_domain

from ..display import get_phase_label
from ..investigation.events import record_event
from ..mcp_runtime import make_tool_call_signature
from ..models import AgentEvent, ArtifactObservation, CaseFile, ScanStats, ToolEvidenceRecord
from ..scope import (
    ScopePreflightResult,
    build_scope_policy,
    classify_scope_preflight,
    parse_tool_call_args,
    split_scope_meta_args,
    summarize_tool_call,
)
from ..targeting import extract_artifact_observations
from ..scanner.case_log import log_scope_decision

MAX_EVIDENCE_PREVIEW = 1_000


@dataclass
class DedupePreflightResult:
    tool_calls: list[Any]


@dataclass
class RuntimeScopeResult:
    scope_preflight: ScopePreflightResult
    blocked_feedback_lines: list[str]
    blocked_domains: set[str]
    current_phase_label: str


def _iter_candidate_domains(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        domain = extract_domain(value)
        return [domain] if domain else []
    if isinstance(value, list):
        out: list[str] = []
        for item in value:
            out.extend(_iter_candidate_domains(item))
        return out
    return []


def _collect_artifacts(
    *,
    args: dict[str, Any],
    raw_output: str,
    tool_name: str,
) -> list[ArtifactObservation]:
    username = str(args.get("username") or args.get("handle") or "")
    text = f"{json.dumps(args, ensure_ascii=True, sort_keys=True)}\n{raw_output or ''}"
    return extract_artifact_observations(
        text=text,
        source=f"tool:{tool_name}",
        username=username,
    )


async def apply_scope_preflight(
    *,
    tool_calls: list[Any],
    round_num: int,
    target: str,
    target_type: str,
    scope_mode: str,
    extra_targets: list[str],
    case_file: CaseFile,
    stats: ScanStats,
    events: list[AgentEvent],
    event_log_size: int,
    evidence_by_id: dict[str, ToolEvidenceRecord],
    current_phase_label: str,
    approved_domains: set[str] | None = None,
    model: str = "",
    confidence_log=None,
    llm_usage=None,
) -> RuntimeScopeResult:
    del evidence_by_id

    if not tool_calls:
        empty = ScopePreflightResult(
            executable_tool_calls=[],
            allowed_scope_decisions={},
            blocked_calls=[],
        )
        return RuntimeScopeResult(
            scope_preflight=empty,
            blocked_feedback_lines=[],
            blocked_domains=set(),
            current_phase_label=current_phase_label,
        )

    _scope_policy = build_scope_policy(
        primary_target=target,
        primary_type=target_type,
        related_targets=list(extra_targets),
        evidence=case_file.evidence_list(),
        approved_domains=approved_domains,
    )
    del _scope_policy

    scope_preflight = await classify_scope_preflight(
        tool_calls=tool_calls,
        primary_target=target,
        primary_type=target_type,
        related_targets=list(extra_targets),
        evidence=case_file.evidence_list(),
        scope_mode=scope_mode,
        model=model,
        confidence_log=confidence_log,
        usage=llm_usage,
    )

    blocked_feedback_lines: list[str] = []
    blocked_domains: set[str] = set()
    phase = current_phase_label

    for blocked in scope_preflight.blocked_calls:
        tc = blocked.tool_call
        tool_name = getattr(getattr(tc, "function", None), "name", "unknown")
        tool_args = blocked.tool_args or {}
        tested = summarize_tool_call(tool_name, tool_args)
        reason = blocked.decision.reason
        blocked_feedback_lines.append(f"{tested}: {reason}")
        stats.tools_blocked += 1
        record_event(
            events,
            event_log_size,
            round_num + 1,
            "tool-blocked",
            f"{tool_name}: {reason}",
        )
        log_scope_decision(
            round_num=round_num,
            source="root",
            tested=tested,
            scope_decision=blocked.decision,
            requested_reason=(tool_args or {}).get("reason", ""),
        )
        for key, value in tool_args.items():
            if "domain" in str(key).lower() or "url" in str(key).lower():
                blocked_domains.update(_iter_candidate_domains(value))

        new_phase = get_phase_label(tool_name)
        if new_phase:
            phase = new_phase

    return RuntimeScopeResult(
        scope_preflight=scope_preflight,
        blocked_feedback_lines=blocked_feedback_lines,
        blocked_domains=blocked_domains,
        current_phase_label=phase,
    )


def apply_dedupe_preflight(
    *,
    tool_calls: list[Any],
    seen_call_signatures: set[str],
    cap: int,
    stats: ScanStats,
    events: list[AgentEvent],
    event_log_size: int,
    round_num: int,
) -> DedupePreflightResult:
    executable: list[Any] = []
    for tc in tool_calls:
        fn = tc.function
        raw_args = parse_tool_call_args(tc)
        args, _ = split_scope_meta_args(raw_args)
        sig = make_tool_call_signature(name=fn.name, args=args)

        if sig in seen_call_signatures:
            stats.tools_deduped += 1
            record_event(
                events,
                event_log_size,
                round_num + 1,
                "tool-dedupe",
                f"{fn.name} preflight duplicate",
            )
            continue

        if len(executable) >= cap:
            stats.tools_deduped += 1
            record_event(
                events,
                event_log_size,
                round_num + 1,
                "tool-cap",
                f"cap reached at {cap} calls",
            )
            continue

        executable.append(tc)

    return DedupePreflightResult(tool_calls=executable)


__all__ = [
    "MAX_EVIDENCE_PREVIEW",
    "DedupePreflightResult",
    "RuntimeScopeResult",
    "_collect_artifacts",
    "apply_dedupe_preflight",
    "apply_scope_preflight",
]
