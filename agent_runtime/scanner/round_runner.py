"""
agent_runtime/scanner/round_runner.py

Single-round orchestration for the scanner loop.
"""

from __future__ import annotations

from .context import maybe_compress_context
from ..investigation.events import capture_worklog_snapshots
from ..investigation.interactive import InteractivePauseAction, handle_interactive_pause
from .llm_round import get_llm_response
from ..investigation.no_tool import handle_no_tools
from ..investigation.plan_checks import inject_plan_check, should_inject_plan_check
from ..investigation.qa import extract_qa_verdict, handle_qa_verdict
from ..reporting.dispatch import maybe_generate_report_via_subagent
from ..execution.round_execution import execute_round
from ..execution.routing import route_tool_calls

_VERDICT_RANK = {"FAIL": 0, "PASS WITH NOTES": 1, "PASS": 2}


async def run_round(ctx: "ScanContext", round_num: int) -> "ScanResult" | None:
    ctx.stats.rounds += 1
    maybe_compress_context(ctx, round_num)

    subagent_report = await maybe_generate_report_via_subagent(ctx, round_num)
    if subagent_report is not None:
        await capture_worklog_snapshots(ctx.session, ctx.case_file)
        ctx.llm_usage.merge_into(ctx.usage)
        return subagent_report, ctx.case_file, ctx.usage, ctx.stats

    if should_inject_plan_check(round_num, ctx.depth, ctx):
        inject_plan_check(ctx, round_num)
    ctx.directive_pending = False

    msg = await get_llm_response(ctx, round_num)

    qa_newly_handled = False
    verdict = extract_qa_verdict(msg.content)
    if verdict:
        existing = getattr(ctx, "qa_verdict_seen", None)
        if not existing or _VERDICT_RANK.get(verdict, 0) > _VERDICT_RANK.get(existing, 0):
            ctx.qa_verdict_seen = verdict
            qa_handled = handle_qa_verdict(ctx, msg, verdict, round_num)
            if qa_handled:
                ctx.qa_verdict_pending = True  # type: ignore[attr-defined]
                qa_newly_handled = True
    ctx.last_assistant_content = msg.content

    routing = await route_tool_calls(ctx, msg, round_num)

    result = await execute_round(ctx, msg, routing, round_num)
    if result.round_tool_results:
        ctx.continuation_nudges = 0

    report_winding_down = ctx.report_requested or getattr(ctx, "qa_verdict_pending", False)
    if not report_winding_down:
        from ..subagents import dispatch_evidence_linkers

        await dispatch_evidence_linkers(ctx, result.pending_linker_dispatches, round_num)

    round_tool_names: list[str] = [name for _, name, _, _ in result.round_tool_results]

    if ctx.interactive_root:
        pause_action = await handle_interactive_pause(
            ctx, msg, routing, round_tool_names, round_num
        )
        if pause_action is InteractivePauseAction.CONTINUE_ROUND:
            return None

    report = handle_no_tools(ctx, msg, routing, round_num, qa_newly_handled)
    if report is not None:
        await capture_worklog_snapshots(ctx.session, ctx.case_file)
        ctx.llm_usage.merge_into(ctx.usage)
        return report, ctx.case_file, ctx.usage, ctx.stats

    return None


__all__ = ["run_round"]

