"""
agent_runtime/scope/evidence.py  –  Source evidence lookup for scope checks

Finds which prior evidence records contain the identifier values a tool call is
about to use.  The result is passed to the AI scope rater as a concrete
reference so it doesn't have to guess where values came from.

The key design decision: identifiers are extracted from tool args using
extract_artifact_observations, not by treating each arg string as a literal
search token.  This handles the common case where a subagent context block
contains an email or domain embedded in a paragraph — the whole paragraph
would never match a prior tool output, but the extracted email will.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..models import ToolEvidenceRecord

# Maximum number of matching evidence records to surface per tool call.
_MAX_HITS = 4

# Maximum characters of raw_output to scan per evidence record.
_SCAN_CHARS = 3000


def _extract_identifier_candidates(tool_args: dict[str, Any]) -> list[str]:
    """
    Extract meaningful identifier tokens from tool args.

    Uses extract_artifact_observations to parse emails, domains, usernames,
    IPs, etc. from all string values in the args dict — including values
    embedded inside long context/task strings.  Falls back to raw short
    string values (<=80 chars) for args that contain no recognised artifacts,
    so plain keyword args like username=champmq are still covered.

    Returns a deduplicated list sorted longest-first so specific identifiers
    (emails, full domains) surface before short substrings.
    """
    from ..targeting import extract_artifact_observations

    def _all_strings(value: Any) -> list[str]:
        if isinstance(value, str):
            return [value] if value.strip() else []
        out: list[str] = []
        if isinstance(value, dict):
            for v in value.values():
                out.extend(_all_strings(v))
        elif isinstance(value, (list, tuple)):
            for item in value:
                out.extend(_all_strings(item))
        return out

    candidates: set[str] = set()

    for raw_str in _all_strings(tool_args):
        # Extract recognised artifact kinds from this string
        obs_list = extract_artifact_observations(text=raw_str, source="scope_lookup")
        for obs in obs_list:
            v = obs.value.strip().lower()
            if len(v) > 2:
                candidates.add(v)

        # Also include the raw string itself if it's short and looks like a
        # direct identifier (no spaces, not a URL-shaped string)
        stripped = raw_str.strip().lower()
        if 2 < len(stripped) <= 80 and " " not in stripped:
            candidates.add(stripped)

    return sorted(candidates, key=len, reverse=True)  # longest -> most specific first


def find_source_evidence(
    tool_args: dict[str, Any],
    evidence: "list[ToolEvidenceRecord]",
) -> str:
    """
    Search accepted evidence records for identifiers that appear in tool_args.

    Checks two sources per record:
      - raw_output  — the tool's text output (truncated for speed)
      - observed_artifacts — the structured artifact list extracted from output

    Returns a formatted string ready to embed in the AI rater prompt, e.g.:

        EV-0031 [osint_fetch_page_content]: 'champmq@gmail.com' found in output
        EV-0028 [osint_username_search]: 'champmq' found in artifacts

    Returns "(none — values not found in prior evidence)" when no hits so the
    AI always has an explicit answer to reason against rather than silence.
    """
    candidates = _extract_identifier_candidates(tool_args)

    if not candidates:
        return "(none — no searchable identifiers in arguments)"

    hits: list[str] = []
    # Walk backwards — most recent evidence is most relevant
    for record in reversed(evidence):
        if not record.scope_decision_allow:
            continue  # never cite blocked calls as attribution anchors
        if record.status not in {"success", "duplicate"}:
            continue

        matched_via: list[str] = []

        # 1. Check structured artifacts first — most reliable signal
        artifact_values = {
            obs.value.strip().lower()
            for obs in record.observed_artifacts
            if obs.value.strip()
        }
        for candidate in candidates:
            if candidate in artifact_values:
                matched_via.append(f"'{candidate}' in artifacts")
                break

        # 2. Fall back to raw output text scan
        if not matched_via:
            output_lower = (record.raw_output or "")[:_SCAN_CHARS].lower()
            for candidate in candidates:
                if candidate in output_lower:
                    matched_via.append(f"'{candidate}' in output")
                    break

        if matched_via:
            hits.append(
                f"{record.evidence_id} [{record.tool_name}]: {', '.join(matched_via)}"
            )
        if len(hits) >= _MAX_HITS:
            break

    if not hits:
        return "(none — values not found in prior evidence)"

    return "\n  ".join(hits)


__all__ = ["find_source_evidence"]
