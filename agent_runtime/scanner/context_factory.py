"""
agent_runtime/scanner/context_factory.py

Factory for constructing ScanContext instances.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from ..llm import ConfidenceLog, LLMUsage
from ..models import CaseFile, ScanStats, UsageStats
from .context import ScanCacheState, ScanContext


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
    extra = list(extra_targets or [])
    flags = list(policy_flags or [])

    cache_state = ScanCacheState(
        evidence_by_id=evidence_by_id or {},
        cached_call_results=cached_call_results or {},
        cached_evidence_ids=cached_evidence_ids or {},
        seen_call_signatures=seen_call_signatures or set(),
        confidence_approved_domains=confidence_approved_domains or set(),
        scope_blocked_domains=scope_blocked_domains or set(),
    )

    return ScanContext(
        session=session,
        target=target,
        target_type=target_type,
        depth=depth,
        model=model,
        verbose=verbose,
        instruction=instruction,
        hypothesis=hypothesis,
        extra_targets=extra,
        correlate_targets=correlate_targets,
        policy_flags=flags,
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
            policies=flags,
            related_targets=extra,
        ),
        usage=usage or UsageStats(),
        stats=stats or ScanStats(),
        llm_usage=llm_usage or LLMUsage(),
        confidence_log=confidence_log or ConfidenceLog(enabled=use_confidence_log),
        events=events or [],
        cache_state=cache_state,
    )


__all__ = ["make_scan_context"]
