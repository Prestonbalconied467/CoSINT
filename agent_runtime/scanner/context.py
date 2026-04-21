"""
agent_runtime/scanner/context.py

Shared scan-state models and compatibility wrappers for setup/compression/factory
helpers now split into focused modules.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from ..llm import ConfidenceLog, LLMUsage
from ..models import CaseFile, ScanStats, UsageStats, ScopeInclusion


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
    cache_state: "ScanCacheState" = field(default_factory=lambda: ScanCacheState())
    events: list = field(default_factory=list)
    round_state: "ScanRoundState" = field(default_factory=lambda: ScanRoundState())

    @property
    def evidence_by_id(self) -> dict[str, Any]:
        return self.cache_state.evidence_by_id

    @property
    def cached_call_results(self) -> dict[str, Any]:
        return self.cache_state.cached_call_results

    @property
    def cached_evidence_ids(self) -> dict[str, Any]:
        return self.cache_state.cached_evidence_ids

    @property
    def seen_call_signatures(self) -> set[str]:
        return self.cache_state.seen_call_signatures

    @property
    def confidence_approved_domains(self) -> set[str]:
        return self.cache_state.confidence_approved_domains

    @property
    def scope_blocked_domains(self) -> set[str]:
        return self.cache_state.scope_blocked_domains

    @property
    def current_phase_label(self) -> str:
        return self.round_state.current_phase_label

    @current_phase_label.setter
    def current_phase_label(self, value: str) -> None:
        self.round_state.current_phase_label = value

    @property
    def report_requested(self) -> bool:
        return self.round_state.report_requested

    @report_requested.setter
    def report_requested(self, value: bool) -> None:
        self.round_state.report_requested = value

    @property
    def report_request_count(self) -> int:
        return self.round_state.report_request_count

    @report_request_count.setter
    def report_request_count(self, value: int) -> None:
        self.round_state.report_request_count = value

    @property
    def pivot_followup_requests(self) -> int:
        return self.round_state.pivot_followup_requests

    @pivot_followup_requests.setter
    def pivot_followup_requests(self, value: int) -> None:
        self.round_state.pivot_followup_requests = value

    @property
    def estimate_fallback_announced(self) -> bool:
        return self.round_state.estimate_fallback_announced

    @estimate_fallback_announced.setter
    def estimate_fallback_announced(self, value: bool) -> None:
        self.round_state.estimate_fallback_announced = value

    @property
    def already_paused(self) -> bool:
        return self.round_state.already_paused

    @already_paused.setter
    def already_paused(self, value: bool) -> None:
        self.round_state.already_paused = value

    @property
    def directive_pending(self) -> bool:
        return self.round_state.directive_pending

    @directive_pending.setter
    def directive_pending(self, value: bool) -> None:
        self.round_state.directive_pending = value

    @property
    def last_assistant_content(self) -> str | None:
        return self.round_state.last_assistant_content

    @last_assistant_content.setter
    def last_assistant_content(self, value: str | None) -> None:
        self.round_state.last_assistant_content = value

    @property
    def report_subagent_attempted(self) -> bool:
        return self.round_state.report_subagent_attempted

    @report_subagent_attempted.setter
    def report_subagent_attempted(self, value: bool) -> None:
        self.round_state.report_subagent_attempted = value

    @property
    def report_subagent_failed(self) -> bool:
        return self.round_state.report_subagent_failed

    @report_subagent_failed.setter
    def report_subagent_failed(self, value: bool) -> None:
        self.round_state.report_subagent_failed = value


@dataclass
class ScanCacheState:
    evidence_by_id: dict[str, Any] = field(default_factory=dict)
    cached_call_results: dict[str, Any] = field(default_factory=dict)
    cached_evidence_ids: dict[str, Any] = field(default_factory=dict)
    seen_call_signatures: set[str] = field(default_factory=set)
    confidence_approved_domains: set[str] = field(default_factory=set)
    scope_blocked_domains: set[str] = field(default_factory=set)


@dataclass
class ScanRoundState:
    current_phase_label: str = ""
    report_requested: bool = False
    report_request_count: int = 0
    pivot_followup_requests: int = 0
    estimate_fallback_announced: bool = False
    already_paused: bool = False
    directive_pending: bool = False
    last_assistant_content: str | None = None
    report_subagent_attempted: bool = False
    report_subagent_failed: bool = False


async def init_scan_state(ctx: ScanContext) -> None:
    from .context_init import init_scan_state as _impl

    await _impl(ctx)


def maybe_compress_context(ctx: ScanContext, round_num: int) -> None:
    from .context_compression import maybe_compress_context as _impl

    _impl(ctx, round_num)


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
    from .context_factory import make_scan_context as _impl

    return _impl(
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
        scope_blocked_domains=scope_blocked_domains,
    )


__all__ = [
    "ScanContext",
    "ScanCacheState",
    "ScanRoundState",
    "init_scan_state",
    "make_scan_context",
    "maybe_compress_context",
]
