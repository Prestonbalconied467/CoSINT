"""
agent_runtime/reporting/finalize.py

Finalization path after investigation rounds complete.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from shared.config import DEFAULT_MAX_REPORT_GRACE_ROUNDS, DEFAULT_MAX_TOOL_CALLS

from ..display import dim, print_narrative
from ..investigation.events import append_case_relation, capture_worklog_snapshots, record_event
from ..prompting import looks_like_final_report
from ..scanner.constants import MAX_AGENT_CHAIN_DEPTH
from .dispatch import maybe_generate_report_via_subagent
from .prompting import decide_max_round_action

if TYPE_CHECKING:
    from ..scanner.context import ScanContext


def _teardown_and_return(
    ctx: "ScanContext",
    report: str,
) -> tuple[str, Any, Any, Any]:
    ctx.llm_usage.merge_into(ctx.usage)
    return report, ctx.case_file, ctx.usage, ctx.stats


async def finalize_scan(
    ctx: "ScanContext",
    agent_chain_depth: int,
    use_confidence_log: bool,
) -> tuple[str | None, Any, Any, Any]:
    append_case_relation(ctx)

    ctx.report_requested = True
    report_from_subagent = await maybe_generate_report_via_subagent(
        ctx, round_num=ctx.stats.rounds
    )
    if report_from_subagent and looks_like_final_report(report_from_subagent):
        print(f"\n  {dim('Report complete. Saving...')}\n")
        await capture_worklog_snapshots(ctx.session, ctx.case_file)
        return _teardown_and_return(ctx, report_from_subagent)

    for attempt in range(DEFAULT_MAX_REPORT_GRACE_ROUNDS):
        max_round = decide_max_round_action(
            msg_content=ctx.last_assistant_content,
            extra_targets=ctx.extra_targets,
            correlate_targets=ctx.correlate_targets,
        )

        if max_round.action == "return_report" and looks_like_final_report(
            max_round.report or ""
        ):
            print(f"\n  {dim('Report complete. Saving...')}\n")
            await capture_worklog_snapshots(ctx.session, ctx.case_file)
            return _teardown_and_return(
                ctx, max_round.report or "(no report generated)"
            )

        if max_round.action != "request_report":
            break

        round_label = DEFAULT_MAX_TOOL_CALLS + attempt + 1
        print(
            f"\n  {dim(f'Round limit reached -- finalizing report (attempt {attempt + 1})...')}\n"
        )
        record_event(
            ctx.events,
            ctx.event_log_size,
            round_label,
            "report-request",
            f"max-round attempt {attempt + 1}",
        )
        ctx.convo.append(
            {
                "role": "user",
                "content": max_round.prompt or "Write the final report now.",
            }
        )
        ctx.stats.rounds += 1
        msg = await ctx.convo.complete(tools=None)
        ctx.last_assistant_content = msg.content
        ctx.convo.append({"role": "assistant", "content": msg.content})
        if msg.content and msg.content.strip():
            print_narrative(msg.content)
        if looks_like_final_report(msg.content or ""):
            print(f"\n  {dim('Report complete. Saving...')}\n")
            await capture_worklog_snapshots(ctx.session, ctx.case_file)
            return _teardown_and_return(ctx, msg.content or "(no report generated)")

    if agent_chain_depth < MAX_AGENT_CHAIN_DEPTH:
        from ..scanner.scanner import run_scan

        print(
            f"\n  {dim(f'Continuing investigation (pass {agent_chain_depth + 1})...')}\n"
        )

        record_event(
            ctx.events,
            ctx.event_log_size,
            ctx.stats.rounds + 1,
            "agent-chain",
            f"Spawning agent chain depth {agent_chain_depth + 1}",
        )
        return await run_scan(
            session=ctx.session,
            target=ctx.target,
            target_type=ctx.target_type,
            depth=ctx.depth,
            model=ctx.model,
            verbose=ctx.verbose,
            instruction=ctx.instruction,
            hypothesis=ctx.hypothesis,
            extra_targets=ctx.extra_targets,
            correlate_targets=ctx.correlate_targets,
            policy_flags=ctx.policy_flags,
            interactive_root=ctx.interactive_root,
            max_context_tokens=ctx.max_context_tokens,
            compression_threshold=ctx.compression_threshold,
            event_log_size=ctx.event_log_size,
            scope_mode=ctx.scope_mode,
            max_tool_calls=ctx.max_tool_calls,
            use_confidence_log=use_confidence_log,
            agent_chain_depth=agent_chain_depth + 1,
            case_file=ctx.case_file,
            usage=ctx.usage,
            stats=ctx.stats,
            llm_usage=ctx.llm_usage,
            confidence_log=ctx.confidence_log,
            evidence_by_id=ctx.evidence_by_id,
            events=ctx.events,
            cached_call_results=ctx.cached_call_results,
            cached_evidence_ids=ctx.cached_evidence_ids,
            seen_call_signatures=ctx.seen_call_signatures,
            confidence_approved_domains=ctx.confidence_approved_domains,
            _scope_blocked_domains=ctx.scope_blocked_domains,
        )

    print(f"\n  {dim('Max rounds reached -- saving partial results...')}\n")

    await capture_worklog_snapshots(ctx.session, ctx.case_file)
    stub_report = (
        "## Executive Summary\n(max rounds reached - partial results above)\n"
        "## Key Findings\nnone\n"
        "## Evidence Chains\nnone\n"
        "## Pivots Taken\nnone\n"
        "## Scope Decisions\nnone\n"
        "## Recommendations\nnone\n"
        "## Tools Used\nnone\n"
    )
    return _teardown_and_return(ctx, stub_report)


__all__ = ["finalize_scan"]

