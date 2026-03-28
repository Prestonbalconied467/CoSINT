"""
agent_runtime/scope/guards/ai.py  –  AI-mode scope guard functions

Contains everything that makes a live LLM call as part of scope evaluation:

  _require_scope_reason   — shared guard used by both open and ai pipelines;
                            rejects calls that reached the AI step without a
                            reason field (should not happen in normal operation)

  ai_scope_check          — calls rate_tool_call_for_scope, handles the error
                            fallback, and converts the result into a ScopeDecision

  evaluate_ai_mode        — full ai-mode pipeline:
                              worklog allow → no-string allow → reason guard → AI

These are called from scope.py and are separated here so that scope.py stays a
clean pipeline orchestrator with no LLM call logic of its own.
"""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from ..constants import (
    SCOPE_AI_APPROVAL_THRESHOLDS,
    SCOPE_AI_APPROVAL_THRESHOLD_DEFAULT,
    SCOPE_AI_APPROVAL_THRESHOLD_EXPLORE,
    SCOPE_ALLOW_AI_APPROVED,
    SCOPE_ALLOW_IDENTIFIER_MATCH,
    SCOPE_BLOCK_AI_ERROR,
    SCOPE_BLOCK_AI_REJECTED,
)
from .shared import (
    collect_all_string_values,
    has_in_scope_identifier_or_domain_match,
)
from ..models import ScopeDecision, ScopePolicy

if TYPE_CHECKING:
    pass


# ---------------------------------------------------------------------------
# Shared: scope_reason guard
# ---------------------------------------------------------------------------


def require_scope_reason(tool_name: str, scope_reason: str) -> ScopeDecision | None:
    """
    Return a blocking ScopeDecision when scope_reason is absent.

    'reason' is enforced as a required schema field by mcp_runtime, so this
    should not trigger in normal operation.  When it does (e.g. a subagent call
    bypassed the schema) it surfaces a clear message so the agent can retry the
    same call with a reason rather than reaching the AI with nothing to evaluate.

    Returns None when the reason is present — callers continue to the AI step.
    """
    if not scope_reason or not scope_reason.strip():
        return ScopeDecision(
            False,
            SCOPE_BLOCK_AI_REJECTED,
            f"scope check for {tool_name} requires a 'reason' argument explaining "
            "why this call is relevant to the investigation target — "
            "retry the same call and include a reason field",
        )
    return None


# ---------------------------------------------------------------------------
# Core: AI scope check
# ---------------------------------------------------------------------------


async def ai_scope_check(
    tool_name: str,
    tool_args: dict[str, Any],
    scope_reason: str,
    scope_policy: ScopePolicy,
    mode: str,
    model: str,
    source_evidence_context: str = "",
    confidence_log: "ConfidenceLog | None" = None,
    usage: "LLMUsage | None" = None,
) -> ScopeDecision:
    """
    Call the AI rater and convert the result into a ScopeDecision.

    On AI error, degrades gracefully to strict-mode identifier match:
      - Allow if arguments contain a confirmed in-scope identifier/domain.
      - Block otherwise.
    This ensures open/ai mode never silently drops to allow-all on outage.
    """
    if not model:
        return ScopeDecision(
            False,
            SCOPE_BLOCK_AI_ERROR,
            "ai/open scope mode requires a model string",
        )

    from ..rater import rate_tool_call_for_scope

    score, reason, audit = await rate_tool_call_for_scope(
        tool_name=tool_name,
        tool_args=tool_args,
        scope_reason=scope_reason,
        scope_policy=scope_policy,
        source_evidence_context=source_evidence_context,
        mode=mode,
        model=model,
        confidence_log=confidence_log,
        usage=usage,
    )

    if (audit or {}).get("error"):
        # AI unavailable — degrade to deterministic identifier match.
        all_string_values, has_any_string = collect_all_string_values(tool_args)
        if has_any_string and has_in_scope_identifier_or_domain_match(
            all_string_values=all_string_values,
            scope_policy=scope_policy,
        ):
            fallback_reason = (
                "AI scope rating failed; falling back to strict-mode identifier match — "
                "allowed because arguments contain a confirmed in-scope identifier/domain"
            )
            return ScopeDecision(
                True,
                SCOPE_ALLOW_IDENTIFIER_MATCH,
                f"{fallback_reason}. Original AI error: {reason}",
                ai_score=None,
                ai_reason=fallback_reason,
                ai_input=audit,
            )
        return ScopeDecision(
            False,
            SCOPE_BLOCK_AI_ERROR,
            f"AI scope rating failed; falling back to strict-mode identifier match — "
            f"blocked because no in-scope identifier could be confirmed. Original error: {reason}",
            ai_score=None,
            ai_reason=reason,
            ai_input=audit,
        )

    threshold = (
        SCOPE_AI_APPROVAL_THRESHOLD_EXPLORE
        if mode == "explore"
        else SCOPE_AI_APPROVAL_THRESHOLDS.get(
            scope_policy.primary_type, SCOPE_AI_APPROVAL_THRESHOLD_DEFAULT
        )
    )

    if score >= threshold:
        return ScopeDecision(
            True,
            SCOPE_ALLOW_AI_APPROVED,
            f"AI approved (score={score:.2f}): {reason}",
            ai_score=score,
            ai_reason=reason,
            ai_input=audit,
        )

    return ScopeDecision(
        False,
        SCOPE_BLOCK_AI_REJECTED,
        f"AI rejected (score={score:.2f}): {reason}",
        ai_score=score,
        ai_reason=reason,
        ai_input=audit,
    )


# ---------------------------------------------------------------------------
# Pipeline: ai mode
# ---------------------------------------------------------------------------


async def evaluate_ai_mode(
    *,
    tool_name: str,
    tool_args: dict[str, Any],
    scope_reason: str,
    scope_policy: ScopePolicy,
    model: str,
    source_evidence_context: str = "",
    confidence_log: "ConfidenceLog | None" = None,
    usage: "LLMUsage | None" = None,
) -> ScopeDecision:
    """
    AI-mode pipeline:
      worklog allow → no-args/no-string allow → reason guard → AI

    The worklog allow is handled by evaluate_tool_scope before dispatching here.
    The no-args/no-string short-circuits are repeated here so this function is
    self-contained and callable independently (e.g. in tests).
    """
    from .shared import _precheck_tool_args  # avoid circular at import time

    _, early = _precheck_tool_args(tool_args)
    if early:
        return early

    blocked = require_scope_reason(tool_name, scope_reason)
    if blocked:
        return blocked

    return await ai_scope_check(
        tool_name,
        tool_args,
        scope_reason,
        scope_policy,
        mode="ai",
        model=model,
        source_evidence_context=source_evidence_context,
        confidence_log=confidence_log,
        usage=usage,
    )


__all__ = [
    "ai_scope_check",
    "evaluate_ai_mode",
    "require_scope_reason",
]
