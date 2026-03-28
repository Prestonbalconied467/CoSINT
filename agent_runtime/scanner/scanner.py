"""
agent_runtime/scanner/scanner.py — Root agent scan loop.

Public entry point: run_scan()

All sub-steps are implemented in the scanner sub-modules:
    context.py         — ScanContext, make_scan_context, init_scan_state,
                              maybe_compress_context
    tool_calls.py   — route_tool_calls, execute_round
    subagents.py    — dispatch_evidence_linkers, append_subagent_call_records
    flow.py — handle_interactive_pause, handle_no_tools, finalize_scan
"""

from __future__ import annotations

import logging
from typing import Any, Final

from shared.config import DEFAULT_MAX_TOOL_CALLS, DEFAULT_SCOPE_MODE

from ..display import print_skills_confirmed, print_warn
from ..llm import ConfidenceLog, Conversation, LLMUsage, is_system_role_error
from ..mcp_runtime import build_call_ledger
from ..models import CaseFile, ScanStats, UsageStats
from ..prompting import build_initial_messages
from .context import (
    ScanContext,
    init_scan_state,
    make_scan_context,
    maybe_compress_context,
)
from .flow import (
    capture_worklog_snapshots,
    finalize_scan,
    handle_interactive_pause,
    handle_no_tools,
    record_event,
    extract_qa_verdict,
    handle_qa_verdict,
)
from .pivot_tracker import build_plan_check_prompt
from ..subagents import dispatch_evidence_linkers
from .tool_calls import DEFAULT_TOOL_CALLS_CAP, execute_round, route_tool_calls

try:
    from mcp import ClientSession
except ImportError as exc:
    raise RuntimeError("Missing dependency. Install mcp.") from exc


log = logging.getLogger(__name__)

_PLAN_CHECK_INTERVAL: Final[dict[str, int]] = {"quick": 4, "deep": 3}
_DEFAULT_PLAN_CHECK_INTERVAL: Final[int] = 3
_VERDICT_RANK = {"FAIL": 0, "PASS WITH NOTES": 1, "PASS": 2}

ScanResult = tuple[str | None, CaseFile, UsageStats, ScanStats]


# ---------------------------------------------------------------------------
# LLM call with system-role fallback
# ---------------------------------------------------------------------------


async def _build_ledger_extras(ctx: ScanContext) -> list[dict[str, str]] | None:
    """Build extra system messages from the call ledger, if any content exists.

    Args:
        ctx: The active scan context.

    Returns:
        A single-element list containing a system-role ledger message, or None
        if the ledger is empty.
    """
    ledger_content = build_call_ledger(ctx.seen_call_signatures)
    if not ledger_content:
        return None
    return [{"role": "system", "content": ledger_content}]


def _rebuild_conversation_as_user_role(ctx: ScanContext, exc: Exception) -> None:
    """Rebuild the conversation using user-role fallback after a system-role rejection.

    Mutates *ctx* in place: replaces ``ctx.convo`` and ``ctx.role_label``.

    Args:
        ctx: The active scan context (mutated).
        exc: The original exception that triggered the fallback.
    """
    messages, ctx.role_label = build_initial_messages(
        system_prompt=ctx.system_prompt,
        reference_injection=ctx.reference_injection,
        opening_parts=ctx.opening_parts,
        prefer_system=False,
        model=ctx.model,
    )
    ctx.convo = Conversation(model=ctx.model, messages=messages, usage=ctx.usage)

    print_warn(f"System role rejected — retrying as {ctx.role_label}")
    record_event(
        ctx.events,
        ctx.event_log_size,
        1,
        "system-role-fallback",
        str(exc)[:120],
    )


async def _get_llm_response(ctx: ScanContext, round_num: int) -> Any:
    """Call the LLM, retrying with a user-role fallback on round 0 if needed.

    On the very first round (``round_num == 0``), some providers reject
    ``system`` role messages. When that happens the conversation is rebuilt
    and the call is retried exactly once.

    Args:
        ctx: The active scan context.
        round_num: Zero-based index of the current agent loop round.

    Returns:
        The raw LLM message object returned by ``ctx.convo.complete``.

    Raises:
        Exception: Re-raises any exception that is not a system-role rejection
            on round 0, or any exception on rounds > 0.
    """
    extras = await _build_ledger_extras(ctx)

    # Consume the QA-verdict pending flag: when handle_qa_verdict has already
    # queued a report prompt we pass tools=None so the LLM writes the report
    # rather than calling more tools.
    qa_pending = getattr(ctx, "qa_verdict_pending", False)
    if qa_pending:
        ctx.qa_verdict_pending = False  # type: ignore[attr-defined]

    tools = None if (qa_pending or ctx.report_requested) else ctx.root_tools

    try:
        msg = await ctx.convo.complete(tools=tools, extra_messages=extras)
        if round_num == 0:
            print_skills_confirmed(ctx.role_label)
        return msg

    except Exception as exc:
        if round_num == 0 and is_system_role_error(exc):
            _rebuild_conversation_as_user_role(ctx, exc)
            return await ctx.convo.complete(tools=tools, extra_messages=extras)
        raise


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _plan_check_interval(depth: str) -> int:
    """Return the plan-check interval for *depth*, defaulting gracefully.

    Args:
        depth: Scan depth string, e.g. ``"quick"`` or ``"deep"``.

    Returns:
        Number of rounds between plan-check injections.
    """
    return _PLAN_CHECK_INTERVAL.get(depth, _DEFAULT_PLAN_CHECK_INTERVAL)


def _should_inject_plan_check(round_num: int, depth: str, ctx: ScanContext) -> bool:
    """Return True when a plan-check message should be injected this round.

    Args:
        round_num: Zero-based round index.
        depth: Scan depth string.

    Returns:
        True if a plan-check prompt should be appended before the LLM call.
    """
    if ctx.directive_pending:
        return False
    return round_num > 0 and round_num % _plan_check_interval(depth) == 0


def _inject_plan_check(ctx: ScanContext, round_num: int) -> None:
    """Append a plan-check user message to the conversation and log the event.

    Args:
        ctx: The active scan context (mutated — message is appended to convo).
        round_num: Zero-based round index.
    """
    ctx.convo.append(
        {
            "role": "user",
            "content": build_plan_check_prompt(
                evidence=ctx.case_file.evidence_list(),
                seen_signatures=ctx.seen_call_signatures,
                round_num=round_num,
                depth=ctx.depth,
            ),
        }
    )
    record_event(
        ctx.events,
        ctx.event_log_size,
        round_num + 1,
        "plan-check",
        f"round {round_num}",
    )


async def _run_round(ctx: ScanContext, round_num: int) -> ScanResult | None:
    """Execute one full agent loop round and return a result if the loop should stop.

    This covers:
    - Optional plan-check injection
    - LLM call (with system-role fallback)
    - Tool-call routing and execution
    - Sub-agent dispatch
    - Interactive pause handling
    - No-tool / terminal-report detection

    Args:
        ctx: The active scan context.
        round_num: Zero-based round index.

    Returns:
        A ``ScanResult`` tuple when the loop should terminate (report produced
        or interactive pause requested a stop), or ``None`` to continue.
    """
    ctx.stats.rounds += 1
    maybe_compress_context(ctx, round_num)

    if _should_inject_plan_check(round_num, ctx.depth, ctx):
        _inject_plan_check(ctx, round_num)
    ctx.directive_pending = False

    msg = await _get_llm_response(ctx, round_num)

    qa_newly_handled = False
    verdict = extract_qa_verdict(msg.content)
    if verdict:
        existing = getattr(ctx, "qa_verdict_seen", None)
        if not existing or _VERDICT_RANK.get(verdict, 0) > _VERDICT_RANK.get(
            existing, 0
        ):
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

    report_winding_down = ctx.report_requested or getattr(
        ctx, "qa_verdict_pending", False
    )
    if not report_winding_down:
        await dispatch_evidence_linkers(
            ctx, result.pending_linker_dispatches, round_num
        )

    round_tool_names: list[str] = [name for _, name, _, _ in result.round_tool_results]

    # Post-execution pause: show results and let operator steer after tools ran.
    if ctx.interactive_root:
        if await handle_interactive_pause(
            ctx, msg, routing, round_tool_names, round_num
        ):
            return None

    report = handle_no_tools(ctx, msg, routing, round_num, qa_newly_handled)
    if report is not None:
        await capture_worklog_snapshots(ctx.session, ctx.case_file)
        ctx.llm_usage.merge_into(ctx.usage)
        return report, ctx.case_file, ctx.usage, ctx.stats

    return None


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


async def run_scan(
    session: ClientSession,
    target: str,
    target_type: str,
    depth: str,
    model: str,
    verbose: bool,
    instruction: str | None,
    hypothesis: str | None,
    extra_targets: list[str] | None,
    correlate_targets: bool,
    policy_flags: list[str] | None,
    interactive_root: bool,
    max_context_tokens: int,
    compression_threshold: float,
    event_log_size: int,
    scope_mode: str = DEFAULT_SCOPE_MODE,
    max_tool_calls: int = DEFAULT_TOOL_CALLS_CAP,
    open_ended: bool = False,
    use_confidence_log: bool = True,
    agent_chain_depth: int = 0,
    case_file: CaseFile | None = None,
    usage: UsageStats | None = None,
    stats: ScanStats | None = None,
    llm_usage: LLMUsage | None = None,
    confidence_log: ConfidenceLog | None = None,
    evidence_by_id: dict[str, Any] | None = None,
    events: list[Any] | None = None,
    cached_call_results: dict[str, Any] | None = None,
    cached_evidence_ids: dict[str, Any] | None = None,
    seen_call_signatures: set[str] | None = None,
    confidence_approved_domains: set[str] | None = None,
    _scope_blocked_domains: set[str] | None = None,
) -> ScanResult:
    """Run the root agent scan loop for a single target.

    Builds a ``ScanContext``, initialises state, then drives the agent loop
    until either a terminal report is produced or ``DEFAULT_MAX_TOOL_CALLS``
    rounds are exhausted (at which point ``finalize_scan`` is called).

    Args:
        session: Active MCP ``ClientSession`` providing tool access.
        target: Primary scan target (domain, IP, email, …).
        target_type: Semantic type of *target* (e.g. ``"domain"``).
        depth: Scan depth — ``"quick"`` or ``"deep"``.
        model: LLM model identifier string.
        verbose: When True, emit detailed progress output.
        instruction: Optional free-text instruction appended to the system
            prompt.
        hypothesis: Optional free-text hypothesis appended to the system prompt.
        extra_targets: Additional targets to include in scope alongside
            *target*.
        correlate_targets: When True, targets are verified before scanning.
        policy_flags: Optional list of policy flag strings forwarded to the
            scope engine.
        interactive_root: When True, pause for user confirmation after each
            round.
        max_context_tokens: Hard cap on conversation context tokens before
            compression is triggered.
        compression_threshold: Fraction of *max_context_tokens* at which
            compression is attempted.
        event_log_size: Maximum number of events retained in ``ctx.events``.
        scope_mode: Scope enforcement mode — forwarded to ``make_scan_context``.
            Defaults to ``DEFAULT_SCOPE_MODE``.
        max_tool_calls: Maximum tool calls allowed per scan.
            Defaults to ``DEFAULT_TOOL_CALLS_CAP``.
        open_ended: When True, disable convergence heuristics and allow the
            agent to run until the cap is reached.
        use_confidence_log: When True, maintain a ``ConfidenceLog`` across
            rounds.
        agent_chain_depth: Nesting depth when called from a sub-agent (0 for
            the root agent).
        case_file: Optional pre-populated ``CaseFile`` to continue.
        usage: Optional ``UsageStats`` accumulator to continue from.
        stats: Optional ``ScanStats`` accumulator to continue from.
        llm_usage: Optional ``LLMUsage`` accumulator.
        confidence_log: Optional existing ``ConfidenceLog``.
        evidence_by_id: Optional mapping of evidence ID → evidence object.
        events: Optional pre-populated event list.
        cached_call_results: Optional cache of previous tool-call results.
        cached_evidence_ids: Optional cache of seen evidence IDs.
        seen_call_signatures: Optional set of already-executed call signatures.
        confidence_approved_domains: Optional set of domains pre-approved by
            the confidence gate.
        _scope_blocked_domains: Optional set of domains that are unconditionally
            blocked (private, injected by the caller).

    Returns:
        A four-tuple of ``(report, case_file, usage, stats)`` where *report*
        is the final markdown/text report (or ``None`` if the agent gave up),
        *case_file* is the accumulated evidence file, *usage* is token/cost
        stats, and *stats* is operational scan metrics.
    """
    ctx = make_scan_context(
        session=session,
        target=target,
        target_type=target_type,
        depth=depth,
        model=model,
        verbose=verbose,
        instruction=instruction,
        hypothesis=hypothesis,
        extra_targets=extra_targets,
        correlate_targets=correlate_targets,
        policy_flags=policy_flags,
        interactive_root=interactive_root,
        scope_mode=scope_mode,
        max_tool_calls=max_tool_calls,
        open_ended=open_ended,
        max_context_tokens=max_context_tokens,
        compression_threshold=compression_threshold,
        event_log_size=event_log_size,
        use_confidence_log=use_confidence_log,
        case_file=case_file,
        usage=usage,
        stats=stats,
        llm_usage=llm_usage,
        confidence_log=confidence_log,
        evidence_by_id=evidence_by_id,
        events=events,
        cached_call_results=cached_call_results,
        cached_evidence_ids=cached_evidence_ids,
        seen_call_signatures=seen_call_signatures,
        confidence_approved_domains=confidence_approved_domains,
        scope_blocked_domains=_scope_blocked_domains,
    )

    await init_scan_state(ctx)

    for round_num in range(DEFAULT_MAX_TOOL_CALLS):
        if (scan_result := await _run_round(ctx, round_num)) is not None:
            return scan_result

    return await finalize_scan(ctx, agent_chain_depth, use_confidence_log)


__all__ = ["run_scan"]
