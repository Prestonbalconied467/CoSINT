"""
agent_runtime/scanner/case_log.py  –  Centralised CaseFile log writers

This module previously handled all writes to CaseFile.scope_confidence_log. That log has been removed in favor of richer scope_ai_evaluation records.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..scope.models import ScopeDecision


# ---------------------------------------------------------------------------
# Internal: sanitise scope AI audit dicts before storing
# ---------------------------------------------------------------------------


def sanitize_audit(audit: Any) -> Any:
    """Strip raw prompt messages from AI audit dicts to keep logs compact."""
    if not isinstance(audit, dict):
        return audit
    sanitized = dict(audit)
    raw_input = sanitized.get("input")
    if isinstance(raw_input, dict):
        input_copy = dict(raw_input)
        input_copy.pop("messages", None)
        sanitized["input"] = input_copy
    return sanitized


# ---------------------------------------------------------------------------
# 1. log_scope_decision
#    Used for: root MCP tool-call gate, root subagent gate (allow + block)
# ---------------------------------------------------------------------------


def log_scope_decision(
    *,
    round_num: int,
    source: str,
    tested: str,
    scope_decision: "ScopeDecision",
    requested_reason: str = "",
) -> dict | None:
    """
    Append one scope allow/block decision to the case log.

    Returns the appended dict (useful for callers that want to embed it in an
    evidence record's scope_ai_audit field), or None when the decision carried
    no AI signal (i.e. it was a pure rule decision with no ai_input).
    """
    if scope_decision.ai_input is None:
        return None

    entry = {
        "round": round_num + 1,
        "source": source,
        "tested": tested,
        "decision": "yes" if scope_decision.allow else "no",
        "score": scope_decision.ai_score,
        "decision_reason": scope_decision.ai_reason or scope_decision.reason,
        "requested_reason": requested_reason,
    }
    return entry


# ---------------------------------------------------------------------------
# 2. log_artifact_promotion
#    Used for: SCOPE PROMOTE: blocks parsed from subagent findings
# ---------------------------------------------------------------------------


def log_artifact_promotion(
    confidence_log: Any,
    *,
    kind: str,
    value: str,
    conf_level: str,
    reason: str,
    round_num: int,
) -> None:
    """
    Record a subagent-declared scope promotion in the ConfidenceLog (so future AI scope decisions have this context).
    """
    score = 1.0 if conf_level.upper() == "HIGH" else 0.7
    if confidence_log is not None:
        from ..llm import ConfidenceEntry

        confidence_log.add(
            ConfidenceEntry(
                kind=kind,
                value=value,
                score=score,
                approved=True,
                reason=reason,
                round=round_num,
            )
        )


# ---------------------------------------------------------------------------
# 3. log_artifact_ratings
#    Used for: ai-mode artifact attribution scoring (rate_artifacts_for_scope)
# ---------------------------------------------------------------------------


def log_artifact_ratings(
    confidence_log: Any,
    *,
    ratings: list[dict],
    round_num: int,
) -> None:
    """
    Append one entry per rated artifact to the ConfidenceLog.
    """
    for r in ratings:
        kind = r.get("kind", "")
        value = r.get("value", "")
        score = r.get("score")
        approved = r.get("approved", False)
        reason = r.get("reason", "")
        if confidence_log is not None:
            from ..llm import ConfidenceEntry

            confidence_log.add(
                ConfidenceEntry(
                    kind=kind,
                    value=value,
                    score=score if isinstance(score, (int, float)) else 0.0,
                    approved=approved,
                    reason=reason,
                    round=round_num,
                )
            )


__all__ = [
    "log_artifact_promotion",
    "log_artifact_ratings",
    "log_scope_decision",
]
