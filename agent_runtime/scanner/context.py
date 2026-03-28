"""
agent_runtime/scanner/context.py

Shared scan state container and the two functions that operate on it
before and during the main loop: init_scan_state and maybe_compress_context.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


from ..context_utils import estimate_tokens, get_model_max_tokens
from ..display import print_context_note, print_scan_startup, print_token_note
from ..llm import Conversation, ConfidenceLog, LLMUsage
from ..models import CaseFile, ScanStats, UsageStats, ScopeInclusion
from ..mcp_runtime import get_mcp_tools
from ..prompting import (
    build_initial_messages,
    build_instruction_block,
    build_multi_target_block,
    build_opening_parts,
    build_policy_block,
    build_reference_injection,
    build_system_prompt,
    build_hypothesis_block,
)
from ..skills import load_skill
from ..subagents import RootCoordinator, build_subagent_tool_definitions
from ..targeting import detect_type
from .flow import record_event
from shared.config import (
    COMPRESSOR_KEEP_LAST_MAX,
    COMPRESSOR_KEEP_LAST_MIN,
    COMPRESSOR_MAX_COMPRESSION_PASSES,
    COMPRESSOR_PRESSURE,
)


# ---------------------------------------------------------------------------
# Shared mutable state for one scan session
# ---------------------------------------------------------------------------


@dataclass
class ScanContext:
    """All mutable state shared across rounds of a single scan."""

    # ── Fixed scan parameters ────────────────────────────────────────────
    session: Any
    target: str
    target_type: str
    depth: str
    model: str
    verbose: bool
    instruction: str | None
    hypothesis: str | None
    extra_targets: list[str]
    correlate_targets: bool
    policy_flags: list[str]
    interactive_root: bool
    scope_mode: str
    max_tool_calls: int
    open_ended: bool
    max_context_tokens: int
    compression_threshold: float
    event_log_size: int

    # ── Populated by init_scan_state ─────────────────────────────────────
    all_mcp_tools: list = field(default_factory=list)
    root_tools: list = field(default_factory=list)
    root: Any = None
    convo: Any = None
    system_prompt: str = ""
    opening_parts: list = field(default_factory=list)
    reference_injection: str = ""
    role_label: str = ""

    # ── Accumulated across rounds ─────────────────────────────────────────
    case_file: CaseFile = None
    usage: UsageStats = None
    stats: ScanStats = None
    llm_usage: LLMUsage = None
    confidence_log: ConfidenceLog = None
    evidence_by_id: dict = field(default_factory=dict)
    events: list = field(default_factory=list)
    cached_call_results: dict = field(default_factory=dict)
    cached_evidence_ids: dict = field(default_factory=dict)
    seen_call_signatures: set = field(default_factory=set)
    confidence_approved_domains: set = field(default_factory=set)
    scope_blocked_domains: set = field(default_factory=set)

    # ── Per-round state ────────────────────────────────────────────────────
    current_phase_label: str = ""
    report_requested: bool = False
    report_request_count: int = 0
    pivot_followup_requests: int = 0
    estimate_fallback_announced: bool = False
    already_paused: bool = False
    directive_pending: bool = False
    last_assistant_content: str | None = None


# ---------------------------------------------------------------------------
# Step 1: Initialise everything before the loop
# ---------------------------------------------------------------------------


async def init_scan_state(ctx: ScanContext) -> None:
    """Load skills, build prompts, create all tracking objects, print startup.

    Also resolves ``ctx.max_context_tokens`` against the model's *real* context
    window so that compression fires before the provider rejects the request.
    The configured value is treated as an upper-bound; if the model's actual
    limit is smaller, the smaller value wins.
    """
    ctx.all_mcp_tools = await get_mcp_tools(ctx.session, scope_mode=ctx.scope_mode)

    # ── Resolve the effective context ceiling ────────────────────────────
    # Two modes controlled by the configured value:
    #
    #   max_context_tokens == 0  →  AUTO: ask LiteLLM for the model's real
    #                               input-token limit. Falls back to the
    #                               module-level _FALLBACK_MAX_TOKENS (8 192)
    #                               when LiteLLM doesn't know the model —
    #                               the user can override that by setting a
    #                               non-zero value instead.
    #
    #   max_context_tokens  > 0  →  MANUAL: use exactly what the user entered,
    #                               no clamping. The user takes responsibility
    #                               for knowing their model's real limit.
    if ctx.max_context_tokens == 0:
        resolved = get_model_max_tokens(ctx.model)
        ctx.max_context_tokens = resolved
        record_event(
            ctx.events,
            ctx.event_log_size,
            0,
            "context",
            f"max_context_tokens auto-resolved: model={resolved}",
        )
        print_token_note(f"{resolved:,}")
    else:
        record_event(
            ctx.events,
            ctx.event_log_size,
            0,
            "context",
            f"max_context_tokens manual: {ctx.max_context_tokens}",
        )
        print_token_note(f"{ctx.max_context_tokens:,}")
    general_skill = load_skill("general") or ""
    reasoning_skill = load_skill("reasoning") or ""
    correlation_skill = load_skill("correlation") or ""
    depth_skill = load_skill(ctx.depth) or ""

    ctx.reference_injection = build_reference_injection(
        general_skill=general_skill,
        reasoning_skill=reasoning_skill,
        depth_skill=depth_skill,
        correlation_skill=correlation_skill,
        correlate_targets=ctx.correlate_targets,
    )

    ctx.root = RootCoordinator(
        target_type=ctx.target_type,
        has_multi_targets=bool(ctx.extra_targets),
        correlate_targets=ctx.correlate_targets,
    )

    ctx.system_prompt = build_system_prompt(
        target=ctx.target,
        target_type=ctx.target_type,
        depth=ctx.depth,
        dispatch_hint=ctx.root.build_dispatch_hint(),
        instruction_block=build_instruction_block(ctx.instruction),
        hypothesis_block=build_hypothesis_block(ctx.hypothesis),
        policy_block=build_policy_block(ctx.policy_flags),
        multi_target_block=build_multi_target_block(
            ctx.extra_targets, ctx.correlate_targets
        ),
        interactive=ctx.interactive_root,
        instruction_text=ctx.instruction or "",
        hypothesis_text=ctx.hypothesis or "",
        correlate_targets=ctx.correlate_targets,
        open_ended=ctx.open_ended,
    )
    ctx.root_tools = ctx.all_mcp_tools + build_subagent_tool_definitions()

    ctx.opening_parts = build_opening_parts(
        target=ctx.target,
        target_type=ctx.target_type,
        depth=ctx.depth,
        extra_targets=ctx.extra_targets,
        correlate_targets=ctx.correlate_targets,
        policy_flags=ctx.policy_flags,
        instruction=ctx.instruction,
        hypothesis=ctx.hypothesis,
    )

    messages, ctx.role_label = build_initial_messages(
        system_prompt=ctx.system_prompt,
        reference_injection=ctx.reference_injection,
        opening_parts=ctx.opening_parts,
        prefer_system=True,
        model=ctx.model,
    )
    ctx.convo = Conversation(model=ctx.model, messages=messages, usage=ctx.usage)

    ctx.case_file.scope_inclusions.append(
        ScopeInclusion(value=ctx.target, kind=ctx.target_type, reason="primary_target")
    )
    for rel in ctx.extra_targets:
        rel_type = detect_type(rel)
        ctx.case_file.scope_inclusions.append(
            ScopeInclusion(value=rel, kind=rel_type, reason="related_target")
        )

    print_scan_startup(
        target=ctx.target,
        target_type=ctx.target_type,
        depth=ctx.depth,
        role_label=ctx.role_label,
        num_tools=len(ctx.all_mcp_tools),
        initial_agent_names=ctx.root.initial_agent_names(),
    )
    record_event(
        ctx.events,
        ctx.event_log_size,
        0,
        "root",
        f"initial subagents: {', '.join(ctx.root.initial_agent_names())}",
    )


# ---------------------------------------------------------------------------
# Step 2: Context compression check (top of every round)
# ---------------------------------------------------------------------------
# todo change this to be an llm compressing and later use this as fallback
def maybe_compress_context(ctx: ScanContext, round_num: int) -> None:
    """Estimate token usage and compress history if above threshold.

    Multi-pass adaptive loop:
    - Re-estimates tokens after every compression pass.
    - Stops as soon as the estimate drops below the threshold.
    - On each successive pass ``keep_last`` shrinks proportionally to how far
      over the ceiling we still are, so the window tightens automatically
      rather than always compressing with a fixed tail size.
    - Gives up gracefully if the history is too short to compress further.
    """
    threshold = int(ctx.max_context_tokens * ctx.compression_threshold)

    for pass_num in range(COMPRESSOR_MAX_COMPRESSION_PASSES):
        est, used_fallback = estimate_tokens(ctx.convo.history, model=ctx.model)

        if used_fallback and not ctx.estimate_fallback_announced:
            ctx.estimate_fallback_announced = True
            print_context_note("token estimate fallback active")
            record_event(
                ctx.events,
                ctx.event_log_size,
                round_num + 1,
                "context",
                "token estimate fallback",
            )

        if est < threshold:
            break  # already within budget — nothing to do

        # Adaptive tail: the further over the ceiling we are, the fewer
        # recent messages we keep, down to the hard minimum. Apply a mild
        # pressure factor so the tail tightens slightly faster on each pass.
        ratio = est / max(ctx.max_context_tokens, 1)
        adjusted = max(ratio * COMPRESSOR_PRESSURE, 0.001)
        keep_last = max(
            COMPRESSOR_KEEP_LAST_MIN, int(COMPRESSOR_KEEP_LAST_MAX / adjusted)
        )

        # Diagnostic note before attempting compression
        record_event(
            ctx.events,
            ctx.event_log_size,
            round_num + 1,
            "context",
            f"compression_attempt pass={pass_num + 1} est={est:,} used_fallback={used_fallback} "
            f"ratio={ratio:.2f} adjusted={adjusted:.2f} keep_last={keep_last} history_len={len(ctx.convo.history)}",
        )

        before_len = len(ctx.convo.history)
        changed = ctx.convo.compress(keep_last=keep_last)
        after_len = len(ctx.convo.history)
        if changed:
            ctx.usage.compressed_events += 1
            # measure summary length (approx chars) for diagnostics
            summary_chars = 0
            try:
                # summary is at index 1 after compression: [system, summary, *tail]
                summary = (
                    ctx.convo.history[1].get("content", "")
                    if len(ctx.convo.history) > 1
                    else ""
                )
                summary_chars = len(str(summary))
            except Exception:
                summary_chars = 0
            record_event(
                ctx.events,
                ctx.event_log_size,
                round_num + 1,
                "context",
                f"compressed pass={pass_num + 1} est={est:,} "
                f"keep_last={keep_last} threshold={threshold:,} before={before_len} after={after_len} summary_chars={summary_chars}",
            )
            print_context_note(
                f"context compressed (pass {pass_num + 1}/{COMPRESSOR_MAX_COMPRESSION_PASSES}, "
                f"was ≈{est:,} tokens, keep_last={keep_last}, removed={before_len - after_len} msgs)"
            )
        else:
            # compress_messages returned False — history is already minimal
            print_context_note(
                f"context compression exhausted after {pass_num} pass(es) "
                f"— history too short to compress further (est≈{est:,})"
            )
            record_event(
                ctx.events,
                ctx.event_log_size,
                round_num + 1,
                "context",
                f"compression exhausted at pass={pass_num} est={est:,}",
            )
            break


# ---------------------------------------------------------------------------
# Factory: build a fresh ScanContext from run_scan arguments
# ---------------------------------------------------------------------------


def make_scan_context(
    *,
    session: Any,
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
    scope_mode: str,
    max_tool_calls: int,
    open_ended: bool,
    max_context_tokens: int,
    compression_threshold: float,
    event_log_size: int,
    use_confidence_log: bool,
    # Optional carry-over from a previous chain link
    case_file: CaseFile | None = None,
    usage: UsageStats | None = None,
    stats: ScanStats | None = None,
    llm_usage: LLMUsage | None = None,
    confidence_log: ConfidenceLog | None = None,
    evidence_by_id: dict | None = None,
    events: list | None = None,
    cached_call_results: dict | None = None,
    cached_evidence_ids: dict | None = None,
    seen_call_signatures: set | None = None,
    confidence_approved_domains: set | None = None,
    scope_blocked_domains: set | None = None,
) -> ScanContext:
    """Build a ScanContext, creating fresh tracking objects for anything not supplied."""
    _extra = list(extra_targets or [])
    _flags = list(policy_flags or [])

    return ScanContext(
        session=session,
        target=target,
        target_type=target_type,
        depth=depth,
        model=model,
        verbose=verbose,
        instruction=instruction,
        hypothesis=hypothesis,
        extra_targets=_extra,
        correlate_targets=correlate_targets,
        policy_flags=_flags,
        interactive_root=interactive_root,
        scope_mode=scope_mode,
        max_tool_calls=max_tool_calls,
        open_ended=open_ended,
        max_context_tokens=max_context_tokens,
        compression_threshold=compression_threshold,
        event_log_size=event_log_size,
        case_file=case_file
        or CaseFile(
            created_at=datetime.now(timezone.utc).isoformat(),
            primary_target=target,
            primary_target_type=target_type,
            depth=depth,
            model=model,
            instruction=instruction,
            hypothesis=hypothesis,
            correlate_targets=correlate_targets,
            scope_mode=scope_mode,
            policies=_flags,
            related_targets=_extra,
        ),
        usage=usage or UsageStats(),
        stats=stats or ScanStats(),
        llm_usage=llm_usage or LLMUsage(),
        confidence_log=confidence_log or ConfidenceLog(enabled=use_confidence_log),
        evidence_by_id=evidence_by_id or {},
        events=events or [],
        cached_call_results=cached_call_results or {},
        cached_evidence_ids=cached_evidence_ids or {},
        seen_call_signatures=seen_call_signatures or set(),
        confidence_approved_domains=confidence_approved_domains or set(),
        scope_blocked_domains=scope_blocked_domains or set(),
    )


__all__ = [
    "ScanContext",
    "init_scan_state",
    "make_scan_context",
    "maybe_compress_context",
]
