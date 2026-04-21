"""
agent_runtime/investigation/no_tool.py

No-tool decision and handling path for the root scanner loop.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Final

from ..display import dim, interactive_pause
from ..prompting import looks_like_final_report
from ..reporting.prompting import build_report_prompt
from ..scanner.decision_types import NoToolDecision
from ..scanner.pivot_tracker import build_continue_pivot_prompt, find_unfollowed_pivots
from .events import append_case_relation, record_event

if TYPE_CHECKING:
    from ..execution.routing import RoutingResult
    from ..scanner.context import ScanContext

MAX_PIVOT_FOLLOWUPS: Final[int] = 3


def decide_no_tool_action(
    *,
    msg_content: str | None,
    interactive_root: bool,
    report_requested: bool,
    extra_targets: list[str],
    correlate_targets: bool,
    report_request_count: int = 0,
) -> NoToolDecision:
    content = msg_content or ""

    if not interactive_root:
        if looks_like_final_report(content):
            return NoToolDecision(action="return_report", report=content)
        if not report_requested:
            return NoToolDecision(
                action="request_report", prompt="Continue the investigation..."
            )
        return NoToolDecision(
            action="request_report",
            prompt=build_report_prompt(extra_targets, correlate_targets, mode="force"),
        )

    if report_requested:
        if looks_like_final_report(content):
            return NoToolDecision(action="return_report", report=content)
        if report_request_count >= 2:
            return NoToolDecision(
                action="return_report",
                report=content
                if content.strip()
                else "(report not generated after repeated requests)",
            )
        return NoToolDecision(
            action="request_report",
            prompt=build_report_prompt(extra_targets, correlate_targets, mode="force"),
        )

    return NoToolDecision(action="pause_interactive")


def handle_no_tools(
    ctx: ScanContext,
    msg: Any,
    routing: RoutingResult,
    round_num: int,
    qa_newly_handled: bool = False,
) -> str | None:
    from ..execution.routing import _should_handle_no_tools

    if not _should_handle_no_tools(
        routing.executable_mcp_calls,
        routing.approved_subagent_calls,
        routing.blocked_subagent_tool_messages,
    ):
        return None

    append_case_relation(ctx)

    if qa_newly_handled and not looks_like_final_report(msg.content):
        return None

    pending_pivots: list[tuple[str, str]] = []
    if not ctx.report_requested:
        pending_pivots = find_unfollowed_pivots(evidence=ctx.case_file.evidence_list())

    if pending_pivots and ctx.pivot_followup_requests < MAX_PIVOT_FOLLOWUPS:
        ctx.pivot_followup_requests += 1
        record_event(
            ctx.events,
            ctx.event_log_size,
            round_num + 1,
            "pivot-followup",
            ", ".join(f"{kind}:{value}" for kind, value in pending_pivots),
        )
        ctx.convo.append(
            {"role": "user", "content": build_continue_pivot_prompt(pending_pivots)}
        )
        return None

    no_tool = decide_no_tool_action(
        msg_content=msg.content,
        interactive_root=ctx.interactive_root,
        report_requested=ctx.report_requested,
        extra_targets=ctx.extra_targets,
        correlate_targets=ctx.correlate_targets,
        report_request_count=getattr(ctx, "report_request_count", 0),
    )

    if no_tool.action == "return_report":
        if looks_like_final_report(no_tool.report or ""):
            print(f"\n  {dim('Report complete. Saving...')}\n")
            return no_tool.report or "(no report generated)"

        ctx.report_requested = True
        print(f"\n  {dim('Report structure incomplete -- retrying...')}\n")

        ctx.report_request_count = getattr(ctx, "report_request_count", 0) + 1
        record_event(
            ctx.events,
            ctx.event_log_size,
            round_num + 1,
            "report-request",
            "return_report action but report failed structure check -- re-requesting",
        )
        ctx.convo.append(
            {
                "role": "user",
                "content": build_report_prompt(
                    ctx.extra_targets, ctx.correlate_targets, mode="force"
                ),
            }
        )
        return None

    if no_tool.action == "request_report":
        ctx.report_requested = True
        print(f"\n  {dim('Requesting final report...')}\n")
        ctx.report_request_count = getattr(ctx, "report_request_count", 0) + 1
        record_event(
            ctx.events,
            ctx.event_log_size,
            round_num + 1,
            "report-request",
            "forced",
        )
        ctx.convo.append(
            {"role": "user", "content": no_tool.prompt or "Write the final report now."}
        )
        return None

    if ctx.already_paused:
        ctx.already_paused = False
        return None

    directive = interactive_pause(
        last_content=msg.content,
        tools_ran=False,
    )
    if directive:
        inject: str = f"[INVESTIGATOR DIRECTIVE] {directive}"
        ctx.stats.directives_issued += 1
        ctx.directive_pending = True
        record_event(
            ctx.events, ctx.event_log_size, round_num + 1, "directive", directive
        )
    else:
        ctx.convo.append(
            {
                "role": "user",
                "content": "Confirmed. Proceed with the planned investigation now.",
            }
        )
        return None
    ctx.convo.append({"role": "user", "content": inject})
    return None


__all__ = [
    "MAX_PIVOT_FOLLOWUPS",
    "NoToolDecision",
    "decide_no_tool_action",
    "handle_no_tools",
]
