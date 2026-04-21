"""agent_runtime/scanner/scanner.py - Root scan-loop entry point."""

from __future__ import annotations

from typing import Any

from shared.config import DEFAULT_MAX_TOOL_CALLS, DEFAULT_SCOPE_MODE

from ..llm import ConfidenceLog, LLMUsage
from ..models import CaseFile, ScanStats, UsageStats
from .context import (
    init_scan_state,
    make_scan_context,
)
from ..reporting.finalize import finalize_scan
from .round_runner import run_round

try:
    from mcp import ClientSession
except ImportError as exc:
    raise RuntimeError("Missing dependency. Install mcp.") from exc

ScanResult = tuple[str | None, CaseFile, UsageStats, ScanStats]


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
    max_tool_calls: int = DEFAULT_MAX_TOOL_CALLS,
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
        if (scan_result := await run_round(ctx, round_num)) is not None:
            return scan_result

    return await finalize_scan(ctx, agent_chain_depth, use_confidence_log)


__all__ = ["run_scan"]
