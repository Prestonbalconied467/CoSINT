"""
agent_runtime/scope/guards/explore.py  –  Explore mode scope guard functions

Explore mode is designed for open-ended investigations where the goal is to
surface threads rather than gatekeep them.  It applies the minimum viable
filtering before handing off to a permissive AI judge.

Explore pipeline order:
  precheck → crypto guard → reason guard → AI (permissive)

What is intentionally skipped vs open mode:
  - Email format guard      — valid investigative pivot
  - Username format guard   — valid investigative pivot
  - Phone format guard      — valid investigative pivot
  - Identity key guard      — AI judges attribution
  - Identifier match        — would short-circuit threads we want to follow
  - Domain artifact guard   — AI judges whether a domain is relevant

What is kept:
  - Precheck (no args / no strings → allow, universal short-circuit)
  - Reason guard            — AI needs something to reason against
  - AI                      — sole judge, with explore-mode permissive prompts
"""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from .shared import (
    _precheck_tool_args,
)
from .ai import (
    ai_scope_check,
    require_scope_reason,
)
from ..models import ScopeDecision, ScopePolicy

if TYPE_CHECKING:
    from ...llm import ConfidenceLog, LLMUsage


async def evaluate_explore_mode(
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
    Explore-mode pipeline:
      precheck → crypto guard → reason guard → AI (permissive)

    Skips all deterministic format and domain guards used by open mode.
    The AI is the sole judge and uses
    explore-mode prompts that lean toward allowing plausible threads.

    On AI error, degrades to identifier match (same fallback as ai/open modes)
    so an outage never silently becomes allow-all.
    """
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
        mode="explore",
        model=model,
        source_evidence_context=source_evidence_context,
        confidence_log=confidence_log,
        usage=usage,
    )


__all__ = ["evaluate_explore_mode"]
