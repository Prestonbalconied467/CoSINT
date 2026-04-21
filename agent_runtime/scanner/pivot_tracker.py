"""
agent_runtime/pivot_tracker.py  –  Pivot tracking and plan check prompts

Extracted from scanner_flow_helpers.py.

Three responsibilities:
  1. find_unfollowed_pivots  — scan evidence for artifacts not yet used as tool args
  2. build_plan_check_prompt — periodic state nudge injected into the agent loop
  3. build_continue_pivot_prompt — prompt injected when unfollowed pivots are found
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from ..scope import CRYPTO_EXPLORER_DOMAINS
from ..targeting import extract_artifact_observations, normalize_target_value

if TYPE_CHECKING:
    from ..models import ToolEvidenceRecord

# ---------------------------------------------------------------------------
# Domains that are never valid investigation pivots
# ---------------------------------------------------------------------------
_NEVER_PIVOT: frozenset[str] = CRYPTO_EXPLORER_DOMAINS | frozenset(
    {
        # Common web infrastructure that appears in scraped content
        "fontawesome.com",
        "getbootstrap.com",
        "jquery.com",
        "w3.org",
        "cloudflare.com",
        "googleapis.com",
        "gstatic.com",
        "jsdelivr.net",
        "unpkg.com",
        "cdnjs.cloudflare.com",
        "bootstrapcdn.com",
        # File-like artifacts that get misidentified as domains
        "history.txt",
        "leaks.logs",
        "robots.txt",
    }
)

_ALLOWED_PIVOT_KINDS = {"email", "domain", "ip", "username", "phone", "crypto"}


def _normalize_artifact_key(kind: str, value: str) -> tuple[str, str]:
    return kind, normalize_target_value(value).strip().lower()


# ---------------------------------------------------------------------------
# find_unfollowed_pivots
# ---------------------------------------------------------------------------


def find_unfollowed_pivots(
    *,
    evidence: list[ToolEvidenceRecord],
    max_pivots: int = 4,
) -> list[tuple[str, str]]:
    """Return recently discovered artifacts that have not yet been used as tool arguments."""
    from shared.url_utils import extract_domain
    from ..scope import is_generic_platform_domain

    investigated: set[tuple[str, str]] = set()
    for record in evidence:
        joined_args = " ".join(str(v) for v in record.tool_args.values())
        for obs in extract_artifact_observations(text=joined_args, source="tool_args"):
            key = _normalize_artifact_key(obs.kind, obs.value)
            if key[1] and key[0] in _ALLOWED_PIVOT_KINDS:
                investigated.add(key)

    pending: list[tuple[str, str]] = []
    seen_pending: set[tuple[str, str]] = set()
    for record in reversed(evidence):
        for obs in record.observed_artifacts:
            key = _normalize_artifact_key(obs.kind, obs.value)
            if not key[1] or key in investigated or key in seen_pending:
                continue
            if obs.kind not in _ALLOWED_PIVOT_KINDS:
                continue
            if obs.kind == "domain":
                _dom = extract_domain(obs.value)
                if not _dom:
                    continue
                if _dom in _NEVER_PIVOT or is_generic_platform_domain(_dom):
                    continue
            seen_pending.add(key)
            pending.append(key)
            if len(pending) >= max_pivots:
                return pending
    return pending


# ---------------------------------------------------------------------------
# build_plan_check_prompt
# ---------------------------------------------------------------------------


def build_plan_check_prompt(
    *,
    evidence: list[ToolEvidenceRecord],
    seen_signatures: set[str],
    round_num: int,
    depth: str,
) -> str:
    """
    Python-generated plan check injected periodically (depth-aware interval).
    Gives the root agent a reliable state nudge independent of system-prompt memory.
    """
    from collections import Counter

    # What has been investigated (appeared as tool args)
    investigated: set[tuple[str, str]] = set()
    for record in evidence:
        joined = " ".join(str(v) for v in record.tool_args.values())
        for obs in extract_artifact_observations(text=joined, source="args"):
            k = (obs.kind, normalize_target_value(obs.value).lower())
            if k[1] and k[0] in _ALLOWED_PIVOT_KINDS:
                investigated.add(k)

    # What was discovered but not yet used as a pivot
    pending: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for record in reversed(evidence):
        for obs in record.observed_artifacts:
            k = (obs.kind, normalize_target_value(obs.value).lower())
            if (
                not k[1]
                or k in investigated
                or k in seen
                or k[0] not in _ALLOWED_PIVOT_KINDS
            ):
                continue
            seen.add(k)
            pending.append(k)
            if len(pending) >= 5:
                break

    # Tool frequency summary
    tool_counts: Counter = Counter()
    for sig in seen_signatures:
        tool_counts[sig.split(":")[0]] += 1
    top_tools = ", ".join(f"{n}({c})" for n, c in tool_counts.most_common(5)) or "none"

    open_pivot_lines = (
        "\n".join(f"  - {kind}: {value}" for kind, value in pending)
        if pending
        else "  None — investigation may be nearing completion."
    )

    if pending:
        directive = (
            "Continue: investigate open pivots or dispatch appropriate subagents."
        )
    elif depth == "deep":
        directive = (
            "No open pivots remain. Dispatch budget_guard to confirm wrap-up, "
            "then write PRE-REPORT QA and dispatch report_synthesizer "
            "(fallback to root report writing only if synthesizer fails)."
        )
    else:
        directive = (
            "No open pivots remain. Write PRE-REPORT QA, then dispatch "
            "report_synthesizer for the final report "
            "(fallback to root report writing only if synthesizer fails)."
        )

    return (
        f"[ROOT PLAN CHECK — round {round_num}]\n"
        f"Unique tool calls so far: {len(seen_signatures)}. Top: {top_tools}\n"
        f"Open artifacts not yet investigated:\n{open_pivot_lines}\n"
        f"Action: {directive}"
    )


# ---------------------------------------------------------------------------
# build_continue_pivot_prompt
# ---------------------------------------------------------------------------


def build_continue_pivot_prompt(pivots: list[tuple[str, str]]) -> str:
    pivot_lines = "\n".join(f"- {kind}: {value}" for kind, value in pivots)
    return (
        "You discovered fresh actionable pivots that have not been investigated yet. "
        "You are NOT done. Before writing the final report, pursue the strongest next pivot(s) "
        "with relevant tools. Only stop if no applicable tool exists for a pivot, and say why.\n\n"
        "Unfollowed pivots:\n"
        f"{pivot_lines}\n\n"
        "Continue the investigation now. Do not write the final report yet."
    )


def count_pivot_mentions(content: str) -> int:
    """Count PIVOT: lines in agent narrative — used to update stats.pivots_found."""
    return len(re.findall(r"(?i)^pivot[\s:]", content, re.MULTILINE))


__all__ = [
    "build_continue_pivot_prompt",
    "build_plan_check_prompt",
    "find_unfollowed_pivots",
    "count_pivot_mentions",
]
