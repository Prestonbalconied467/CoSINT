"""Execution round compatibility layer (phase-1 rehome)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..display import (
    print_narrative,
    print_scope_promote,
    print_subagent_dispatch,
    usage_line,
)
from ..investigation.events import record_event
from ..scanner.context import ScanContext
from ..scanner.pivot_tracker import count_pivot_mentions
from ..subagents import (
    append_subagent_call_records,
    dispatch_subagent,
    parse_scope_promote_block,
    print_subagent_summary,
    should_auto_dispatch_evidence_linker,
)
from .mcp_batch import execute_tool_call_batch
from .routing import RoutingResult, _should_handle_no_tools


@dataclass
class ExecuteRoundResult:
    round_tool_results: list
    pending_linker_dispatches: list


async def execute_round(
    ctx: "ScanContext",
    msg: Any,
    routing: RoutingResult,
    round_num: int,
) -> ExecuteRoundResult:
    assistant_message: dict[str, Any] = {"role": "assistant"}
    if msg.content is not None:
        assistant_message["content"] = msg.content
    if routing.answered_tool_calls:
        assistant_message["tool_calls"] = routing.answered_tool_calls
    ctx.convo.append(assistant_message)

    pending_tool_messages: dict[str, dict] = {}
    pending_scope_note = None
    if routing.blocked_feedback_lines:
        note_lines = "\n".join(
            f"- {line}" for line in routing.blocked_feedback_lines[:12]
        )
        pending_scope_note = (
            "[RUNTIME SCOPE NOTE] These tool calls were blocked as out-of-scope:\n"
            f"{note_lines}\n"
        )
        if _should_handle_no_tools(
            routing.executable_mcp_calls,
            routing.approved_subagent_calls,
            routing.blocked_subagent_tool_messages,
        ):
            ctx.convo.append({"role": "user", "content": pending_scope_note})
            pending_scope_note = None

    for blocked_msg in routing.blocked_subagent_tool_messages:
        pending_tool_messages[blocked_msg["tool_call_id"]] = blocked_msg

    if msg.content and msg.content.strip() and not getattr(ctx, "qa_narrative_printed", False):
        if not getattr(ctx, "qa_narrative_printed", False):
            print_narrative(msg.content)
            ctx.stats.pivots_found += count_pivot_mentions(msg.content)
        else:
            ctx.qa_narrative_printed = False

    pending_linker_dispatches: list[tuple[str, Any, list]] = []
    for tc, (agent_name, task, context) in routing.approved_subagent_calls:
        print_subagent_dispatch(agent_name, task)
        record_event(
            ctx.events,
            ctx.event_log_size,
            round_num + 1,
            "subagent-dispatch",
            f"{agent_name}: {task[:80]}",
        )
        subagent_result, tool_message = await dispatch_subagent(
            tc=tc,
            agent_name=agent_name,
            task=task,
            context=context,
            session=ctx.session,
            model=ctx.model,
            all_mcp_tools=ctx.all_mcp_tools,
            verbose=ctx.verbose,
            primary_target=ctx.target,
            primary_target_type=ctx.target_type,
            extra_targets=ctx.extra_targets,
            scope_mode=ctx.scope_mode,
            scope_blocked_domains=ctx.scope_blocked_domains,
        )
        ctx.root.record_result(subagent_result)
        ctx.stats.subagents_activated.append(agent_name)
        append_subagent_call_records(
            ctx,
            round_num=round_num,
            agent_name=agent_name,
            tool_call_records=subagent_result.tool_call_records,
            raw_output=tool_message["content"],
        )
        record_event(
            ctx.events,
            ctx.event_log_size,
            round_num + 1,
            "subagent-complete",
            f"{agent_name}: {len(subagent_result.tools_called)} tools, error={subagent_result.error}",
        )
        pending_tool_messages[tc.id] = tool_message
        print_subagent_summary(agent_name, subagent_result, verbose=ctx.verbose)

        promoted = []
        if subagent_result.findings:
            promoted = parse_scope_promote_block(
                findings=subagent_result.findings,
                agent_name=agent_name,
                round_num=round_num + 1,
                case_file=ctx.case_file,
                evidence_by_id=ctx.evidence_by_id,
                confidence_approved_domains=ctx.confidence_approved_domains,
                confidence_log=ctx.confidence_log,
            )
            for kind_p, value_p, reason_p in promoted:
                print_scope_promote(kind_p, value_p, reason_p)

        if should_auto_dispatch_evidence_linker(
            agent_name=agent_name, result=subagent_result, promoted=promoted
        ):
            pending_linker_dispatches.append((agent_name, subagent_result, promoted))

    round_tool_results = []
    if routing.executable_mcp_calls:
        batch = await execute_tool_call_batch(
            session=ctx.session,
            tool_calls=routing.executable_mcp_calls,
            round_num=round_num,
            verbose=ctx.verbose,
            target=ctx.target,
            target_type=ctx.target_type,
            scope_mode=ctx.scope_mode,
            extra_targets=ctx.extra_targets,
            case_file=ctx.case_file,
            stats=ctx.stats,
            events=ctx.events,
            event_log_size=ctx.event_log_size,
            seen_call_signatures=ctx.seen_call_signatures,
            cached_call_results=ctx.cached_call_results,
            cached_evidence_ids=ctx.cached_evidence_ids,
            evidence_by_id=ctx.evidence_by_id,
            current_phase_label=ctx.current_phase_label,
            approved_domains=ctx.confidence_approved_domains,
            model=ctx.model,
            confidence_log=ctx.confidence_log,
            llm_usage=ctx.llm_usage,
            allowed_scope_decisions=routing.allowed_scope_decisions,
            interactive_root=ctx.interactive_root,
        )
        round_tool_results = batch.tool_results
        ctx.current_phase_label = batch.current_phase_label

        if round_tool_results:
            ctx.pivot_followup_requests = 0

        for tool_call_id, name, result, evidence_id in round_tool_results:
            status = (
                ctx.evidence_by_id.get(evidence_id).status
                if evidence_id in ctx.evidence_by_id
                else "unknown"
            )
            pending_tool_messages[tool_call_id] = {
                "role": "tool",
                "tool_call_id": tool_call_id,
                "name": name,
                "content": f"[CASE EVIDENCE {evidence_id} | status={status}]\n{result}",
            }

        if ctx.verbose:
            print(f"  {usage_line(ctx.usage)}")

    for tc_entry in routing.answered_tool_calls:
        tc_id = tc_entry["id"]
        if tc_id in pending_tool_messages:
            ctx.convo.append(pending_tool_messages[tc_id])

    if pending_scope_note:
        ctx.convo.append({"role": "user", "content": pending_scope_note})

    return ExecuteRoundResult(
        round_tool_results=round_tool_results,
        pending_linker_dispatches=pending_linker_dispatches,
    )


__all__ = ["ExecuteRoundResult", "execute_round"]
