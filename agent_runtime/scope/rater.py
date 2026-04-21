"""
agent_runtime/scope/rater.py  -  LLM-based artifact scope scoring

Rates discovered OSINT artifacts (emails, domains, usernames, etc.) for
attribution confidence - i.e. how likely they belong to the investigation
target. Used as a gate before promoting artifacts into the scope policy.

Also rates pending tool calls for scope in open/ai modes.

Uses llm.py for all AI calls - retry, usage tracking, and JSON parsing
are handled there. ConfidenceLog context is injected into prompts when
available and enabled to keep decisions consistent across rounds.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .constants import (
    SCOPE_AI_APPROVAL_THRESHOLD_EXPLORE,
    SCOPE_AI_APPROVAL_THRESHOLDS,
    SCOPE_AI_APPROVAL_THRESHOLD_DEFAULT,
    SCOPE_AI_APPROVAL_THRESHOLDS_EXPLORE,
)

if TYPE_CHECKING:
    from ..llm import ConfidenceLog, LLMUsage
    from .models import ScopePolicy


def _prior_section(confidence_log: Any) -> str:
    prior_block = confidence_log.as_context_block() if confidence_log else ""
    return f"\n\n{prior_block}" if prior_block else ""


def _artifact_lines(artifacts: list[tuple[str, str]]) -> str:
    return "\n".join(f"  - kind: {kind}, value: {value}" for kind, value in artifacts)


def _is_unsure(score_raw: Any) -> bool:
    return score_raw == "unsure" or (
        isinstance(score_raw, str) and score_raw.lower() == "unsure"
    )


def _clamped_score(score_raw: Any) -> float:
    try:
        return max(0.0, min(1.0, float(score_raw)))
    except (TypeError, ValueError):
        return 0.0


def _build_rated_map(parsed_raw: list[Any]) -> dict[tuple[str, str], tuple[Any, str]]:
    rated: dict[tuple[str, str], tuple[Any, str]] = {}
    for item in parsed_raw:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("kind", "")).strip().lower()
        value = str(item.get("value", "")).strip().lower()
        rated[(kind, value)] = (
            item.get("score", 0.0),
            str(item.get("reason", "")).strip(),
        )
    return rated


def _evaluate_artifact_score(
    kind: str,
    score_raw: Any,
    reason: str,
    mode: str,
) -> tuple[Any, str, bool]:
    """
    Returns: (score, reason, approved)

    score is "unsure" or a float 0.0-1.0.
    approved is False when unsure — caller owns any further policy on that signal.
    """
    if _is_unsure(score_raw):
        return "unsure", reason or "unsure — insufficient context to rate", False

    score = _clamped_score(score_raw)
    threshold = (
        SCOPE_AI_APPROVAL_THRESHOLDS_EXPLORE.get(
            kind, SCOPE_AI_APPROVAL_THRESHOLD_EXPLORE
        )
        if mode == "explore"
        else SCOPE_AI_APPROVAL_THRESHOLDS.get(kind, SCOPE_AI_APPROVAL_THRESHOLD_DEFAULT)
    )
    return score, reason, score >= threshold


async def rate_artifacts_for_scope(
    *,
    artifacts: list[tuple[str, str]],  # list of (kind, value)
    scope_policy: "ScopePolicy",
    findings_excerpt: str,
    model: str,
    round_num: int,
    subagent_name: str,
    mode: str,
    confidence_log: "ConfidenceLog | None" = None,
    usage: "LLMUsage | None" = None,
) -> list[dict]:
    """
    Rate a mixed list of artifacts for scope promotion using a single LLM call.

    For each artifact the rater returns one of:
      - score 0.0-1.0 + reason  -> approved if score >= per-kind threshold
      - "unsure"                -> approved=False; caller decides how to handle

    Returns a list of result dicts:
      {
        "kind": str, "value": str, "score": float | "unsure",
        "reason": str, "approved": bool, "round": int
      }

    Raises LLMError / LLMParseError on failure — callers own the error policy.
    """
    if not artifacts:
        return []

    from ..llm import complete_json, ConfidenceEntry
    from .guards.shared import build_scope_evidence_summary

    scope_summary = build_scope_evidence_summary(scope_policy)
    excerpt = (findings_excerpt or "")[:1500].strip()
    prior_section = _prior_section(confidence_log)
    mode_note = (
        "You are rating in explore mode — follow threads liberally. "
        "Allow when there is any plausible link to the target, even indirect. "
        "Only reject clear infrastructure noise or values with zero conceivable connection.\n\n"
        if mode == "explore"
        else ""
    )
    system_prompt = (
        "You are a scope analyst for an OSINT investigation. Your job is to rate whether "
        "each discovered artifact is directly attributable to the investigation target - "
        "meaning it belongs to them, they control it, or it is a direct identifier for them.\n\n"
        'For each artifact respond with a score from 0.0 to 1.0, OR the string "unsure" '
        "if you genuinely cannot determine attribution from the available context.\n\n"
        f"{mode_note}"
        "Score guide:\n"
        "  0.9-1.0 : Strongly target-owned/attributed (explicit match, confirmed in findings)\n"
        "  0.7-0.8 : Likely attributed (good contextual match, consistent with findings)\n"
        "  0.5-0.6 : Plausible but uncertain (weak or indirect link)\n"
        "  0.2-0.4 : Probably incidental (shared service, infrastructure noise, low relevance)\n"
        "  0.0-0.1 : Almost certainly not the target's (CDN, generic platform, unrelated)\n"
        '  "unsure" : Cannot determine - not enough context to rate this artifact\n\n'
        "Respond ONLY with a JSON array. No preamble, no markdown fences.\n"
        'Format: [{"kind": "email", "value": "x@y.com", "score": 0.85, "reason": "..."}]\n'
        'Use "unsure" as the score value (a string) when you cannot determine attribution.'
        f"{prior_section}"
    )

    user_prompt = (
        f"{scope_summary}\n"
        f"Source tool / subagent: {subagent_name}\n\n"
        f"Tool output (the text these artifacts were found in):\n{excerpt}\n\n"
        f"Artifacts to rate:\n{_artifact_lines(artifacts)}\n\n"
        "Rate each artifact's likelihood of being directly attributable to the investigation target. "
        "Use the confirmed scope above as your primary reference — artifacts matching or directly "
        "derived from confirmed identifiers/domains should score high. "
        "Judge solely on the tool output and confirmed scope; ignore how or why the tool was called."
    )

    parsed_raw = await complete_json(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        expect=list,
        usage=usage,
    )

    rated = _build_rated_map(parsed_raw)

    results: list[dict] = []
    for kind, value in artifacts:
        key = (kind.lower(), value.lower())
        score_raw, reason = rated.get(key, ("unsure", "not rated by model"))

        score, reason, approved = _evaluate_artifact_score(
            kind, score_raw, reason, mode
        )

        entry = {
            "kind": kind,
            "value": value,
            "score": score,
            "reason": reason,
            "approved": approved,
            "round": round_num,
        }
        results.append(entry)

        # Feed result into the confidence log so future calls have context.
        if confidence_log is not None:
            confidence_log.add(
                ConfidenceEntry(
                    kind=kind,
                    value=value,
                    score=score,
                    approved=approved,
                    reason=reason,
                    round=round_num,
                )
            )

    return results


# ---------------------------------------------------------------------------
# Tool-call scope rater  (used by guided + ai scope modes)
# ---------------------------------------------------------------------------


def _mode_role_note(mode: str) -> str:
    if mode == "explore":
        return (
            "You are the sole judge for this tool call in an open-ended exploration. "
            "The investigator is following threads, not confirming known identifiers. "
            "Allow when there is any plausible link to the target — even indirect or inferred. "
            "Only block when the value has no conceivable connection to the target whatsoever, "
            "or when it is clearly infrastructure noise (CDN, analytics, generic platform with "
            "no target-specific path)."
        )
    if mode == "guided":
        return (
            "Rule-based checks have already cleared obvious allows and blocks. "
            "You are the tiebreaker for genuinely ambiguous cases. "
            "You may allow when the link to the target is plausible but you must be "
            "able to state a concrete, specific attribution chain - not just 'found on a page'."
        )
    return (
        "You are the sole judge for this tool call. Apply full scrutiny. "
        "Only allow calls that have a concrete, traceable link to the investigation target. "
        "Vague or assumed attribution is not sufficient to allow."
    )


def _rejection_rules_block(mode: str) -> str:
    if mode == "explore":
        return (
            "REJECTION RULES — score 0.1 or below if any of these apply:\n"
            "  - The argument is a bare platform name with no path (e.g. 'facebook', 'youtube' "
            "as a standalone value — not a URL containing the target's profile path)\n"
            "  - The argument is a platform-generated numeric ID with no explicit link to the "
            "target in the source evidence (e.g. a raw user ID like 147977)\n"
            "  - The argument is a generic file or path resource "
            "(e.g. apple-touch-icon.png, user.aspx, favicon.ico)\n"
            "  - The argument is a CDN, analytics, or tracking domain with no target-specific path\n\n"
            "LOOSENED IN EXPLORE MODE — these are valid reasons to allow:\n"
            "  - A single inferred step is sufficient attribution — two concrete steps not required\n"
            "  - Absence of prior evidence does not cap the score at 0.5 if the link is plausible\n"
            "  - A URL on an out-of-scope domain is acceptable if the path contains the target identifier\n"
            "  - 'Found on a target-attributed page' is sufficient if that page itself is confirmed\n"
            "  - An unconfirmed identifier (new email, username variant) may score 0.5+ on reason alone\n"
        )
    return (
        "REJECTION RULES — score 0.1 or below if any of these apply:\n"
        "  - The argument value IS a platform name (e.g. roblox, facebook, youtube, steam)\n"
        "  - The argument value is a purely numeric ID generated by a platform (e.g. 147977)\n"
        "  - The argument value is a generic file or path resource (e.g. apple-touch-icon.png, user.aspx)\n"
        "  - The caller rationale says 'found on a page' or 'found on a profile' with no specific "
        "evidence ID or tool output tying the value to the target\n"
        "  - The argument is a platform or infrastructure domain (CDN, analytics, tracker)\n"
    )


def _args_preview(tool_args: dict) -> str:
    return ", ".join(f"{k}={str(v)[:40]}" for k, v in list(tool_args.items())[:2])


def _image_scope_note(tool_args: dict) -> str:
    """Extra guidance for image URL pivots to improve attribution judgement."""
    image_url = tool_args.get("image_url")
    if not isinstance(image_url, str) or not image_url.strip():
        return ""

    return (
        "\n\nImage URL guidance:\n"
        "- Do NOT auto-allow just because the host is a large platform/CDN (for example Google, GitHub, Gravatar).\n"
        "- Allow only when there is a concrete link between this exact image URL and the target (same handle, profile page linkage, or prior evidence).\n"
        "- If the URL is only generic infrastructure with no target linkage, score low."
    )


async def rate_tool_call_for_scope(
    *,
    tool_name: str,
    tool_args: dict,
    scope_reason: str = "",
    scope_policy: "ScopePolicy",
    source_evidence_context: str = "",
    mode: str = "open",
    model: str,
    round_num: int = 0,
    confidence_log: "ConfidenceLog | None" = None,
    usage: "LLMUsage | None" = None,
) -> tuple[float, str, dict]:
    """
    Ask the LLM whether a pending tool call is in scope for this investigation.

    Returns (score, reason, audit):
      score  - float 0.0-1.0. Caller compares against the mode-appropriate threshold.
      reason - short human-readable justification.
      audit  - structured metadata including input prompts used for this decision.

    On any error returns (0.0, error_message, audit) - fail-closed.

    mode influences the AI's role:
      open    - AI is a fallback for ambiguous cases; requires concrete attribution chain.
      ai      - AI is the sole judge; apply full scrutiny.
      explore - AI is the sole judge; permissive for plausible threads, blocks only clear noise.
    """
    import json as _json
    from ..llm import LLMError, LLMParseError, complete_json, ConfidenceEntry
    from .guards.shared import build_scope_evidence_summary

    scope_summary = build_scope_evidence_summary(scope_policy)

    try:
        args_text = _json.dumps(tool_args, ensure_ascii=False)
    except Exception:
        args_text = str(tool_args)

    reason_text = (scope_reason or "").strip()
    reason_section = (
        f"\nRoot agent rationale (evaluate this critically, do not accept it at face value): {reason_text}"
        if reason_text
        else ""
    )
    evidence_ref = (source_evidence_context or "").strip()
    evidence_section = f"\nSource evidence from prior results:\n  {evidence_ref}"
    image_scope_note = _image_scope_note(tool_args)

    prior_section = _prior_section(confidence_log)
    role_note = _mode_role_note(mode)
    rejection_rules = _rejection_rules_block(mode)

    system_prompt = (
        "You are a scope analyst for an OSINT investigation. "
        "Your sole job is to decide whether a pending tool call is on-scope - "
        "i.e. is it concretely investigating the stated target and not drifting to "
        f"an unrelated subject, platform, or third party.\n\n{role_note}\n\n"
        f"{rejection_rules}\n"
        "Score 0.0-1.0:\n"
        "  0.9-1.0 : Clearly about the target — explicit match, confirmed in prior evidence\n"
        "  0.7-0.8 : Likely the target — specific contextual link with named evidence\n"
        "  0.5-0.6 : Plausible — indirect link but attribution chain is traceable\n"
        "  0.2-0.4 : Probably drifting — vague or assumed link, no concrete evidence\n"
        "  0.0-0.1 : Not the target — platform noise, infrastructure, or unrelated\n\n"
        "Respond ONLY with a JSON object. No preamble, no markdown fences.\n"
        "Format:\n"
        '  {"score": 0.85, "source_evidence": "EV-0012 or tool name", '
        '"attribution_chain": "step-by-step from primary target to this value", '
        '"infrastructure_ruled_out": "why this is not platform noise", '
        '"reason": "one sentence summary"}\n'
        "All four fields are required. "
        + (
            "In explore mode a single inferred step is acceptable for attribution_chain."
            if mode == "explore"
            else "If you cannot fill in attribution_chain with at least two concrete steps, "
            "your score must be below 0.5."
        )
        + f"{prior_section}"
    )

    call_summary = f"{tool_name}({_args_preview(tool_args)})"

    if mode == "explore":
        internal_questions = (
            "Before scoring, answer these internally:\n"
            "  1. Is there any plausible link between this value and the target — "
            "even indirect or inferred from a target-attributed page?\n"
            "  2. Does this argument directly match the primary target or a confirmed identifier/domain?\n"
            "  3. Is this a bare platform name, a platform-generated numeric ID, a generic "
            "file/path resource, or a CDN domain? If yes and there is no target-specific path, "
            "score 0.1 or below.\n\n"
            "If source evidence is absent but the value plausibly derives from the target "
            "based on the investigation context, you may score 0.4–0.6."
        )
    else:
        internal_questions = (
            "Before scoring, answer these internally:\n"
            "  1. Does the source evidence above confirm this value belongs to the target, "
            "or is it incidental/infrastructure noise found in a result?\n"
            "  2. Does this argument directly match the primary target or a confirmed identifier/domain?\n"
            "  3. Is this value a platform name, a platform-generated numeric ID, a generic "
            "file/path resource, or infrastructure? If yes, score 0.1 or below.\n\n"
            "If source evidence is '(none — values not found in prior evidence)' and the value "
            "is not a direct match for the primary target or confirmed scope, your score must "
            "be below 0.5."
        )

    user_prompt = (
        f"{scope_summary}\n\n"
        f"Pending tool call:\n"
        f"  tool: {tool_name}\n"
        f"  args: {args_text}"
        f"{reason_section}"
        f"{evidence_section}"
        f"{image_scope_note}\n\n"
        "Is this tool call on-scope for the investigation?\n\n"
        "The confirmed scope and source evidence above are the authoritative references. "
        "A tool call is on-scope if its arguments directly reference the primary target, "
        "a confirmed identifier, a confirmed domain, or a value that is concretely traced "
        "to those in the source evidence. The caller rationale is a CLAIM — verify it "
        "against the confirmed scope and evidence, do not accept it at face value.\n\n"
        f"{internal_questions}"
    )

    ai_input = {
        "model": model,
        "mode": mode,
        "tool_name": tool_name,
        "tool_args": tool_args,
        "scope_reason": reason_text,
        "primary_target": scope_policy.primary_target,
        "target_type": scope_policy.primary_type,
        "scope_summary": scope_summary,
        "source_evidence_context": evidence_ref,
        # Keep decision context metadata but redact full prompt chat content from persisted audit.
    }

    try:
        parsed = await complete_json(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            expect=dict,
            usage=usage,
        )
        score = _clamped_score(parsed["score"])
        # Combine structured fields into a single auditable reason string.
        source_evidence = str(parsed.get("source_evidence", "")).strip()
        attribution_chain = str(parsed.get("attribution_chain", "")).strip()
        infra_ruled_out = str(parsed.get("infrastructure_ruled_out", "")).strip()
        summary = str(parsed.get("reason", "")).strip() or "no reason provided"
        reason_parts = [summary]
        if attribution_chain:
            reason_parts.append(f"chain: {attribution_chain}")
        if source_evidence:
            reason_parts.append(f"source: {source_evidence}")
        if infra_ruled_out:
            reason_parts.append(f"infra check: {infra_ruled_out}")
        reason = " | ".join(reason_parts)
    except (LLMError, LLMParseError, KeyError, TypeError, ValueError) as exc:
        error_type = (
            "parse_error" if isinstance(exc, LLMParseError) else "runtime_error"
        )
        raw_excerpt = ""
        if isinstance(exc, LLMParseError):
            raw_excerpt = (exc.raw or "")[:240]
        return (
            0.0,
            f"AI rating error: {exc}",
            {
                "input": ai_input,
                "response": None,
                "error": str(exc),
                "error_type": error_type,
                "raw_excerpt": raw_excerpt,
            },
        )

    if confidence_log is not None:
        log_threshold = (
            SCOPE_AI_APPROVAL_THRESHOLD_EXPLORE
            if mode == "explore"
            else SCOPE_AI_APPROVAL_THRESHOLD_DEFAULT
        )
        confidence_log.add(
            ConfidenceEntry(
                kind="tool_call",
                value=call_summary,
                score=score,
                approved=score >= log_threshold,
                reason=reason,
                scope_request=scope_reason,
                round=round_num,
            )
        )

    return score, reason, {"input": ai_input, "response": parsed, "error": ""}


__all__ = [
    "rate_artifacts_for_scope",
    "rate_tool_call_for_scope",
]
