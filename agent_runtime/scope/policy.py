"""
agent_runtime/scope.py  –  Scope policy building and evaluation

Public async functions:
  build_scope_policy
  evaluate_tool_scope
  classify_scope_preflight

Scope modes:
  strict   — deterministic rules only; block anything without explicit match
  guided   — deterministic rules first, AI fallback for genuinely ambiguous cases
  ai       — AI is the sole judge (worklog allow and no-string-args short-circuit only)
  explore  — minimal filtering, permissive AI for open-ended thread-following investigations

Pipeline order per mode:

  strict:
    worklog allow → no-args/no-string allow → crypto guard →
    strict domain/url blockers → strict artifact guard →
    strict identifier match → default block

  guided:
    worklog allow → no-args/no-string allow → crypto guard →
    guided domain/url blockers → guided email format guard →
    guided username format guard → guided phone format guard →
    guided identity key guard (ip only) → guided identifier match →
    guided domain artifact guard → AI

  ai:
    worklog allow → no-string allow → AI

  explore:
    worklog allow → no-args/no-string allow → crypto guard → AI (permissive)
"""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from shared.config import DEFAULT_SCOPE_MODE
from shared.url_utils import extract_domain
from .constants import (
    ATTRIBUTABLE_DOMAIN_TOOL_PREFIXES,
    CRYPTO_EXPLORER_DOMAINS,
    SCOPE_ALLOW_INTERNAL_WORKLOG,
    SCOPE_BLOCK_AI_ERROR,
    SCOPE_BLOCK_PARSE_FAILURE,
    SCOPE_BLOCK_STRICT_UNMATCHED,
    SCOPE_ALLOW_NO_ARGS,
)
from .guards.guided import (
    check_guided_domain_artifact_guard,
    check_guided_domain_url_blockers,
    check_guided_email_format_guard,
    check_guided_identifier_match,
    check_guided_identity_key_guard,
    check_guided_phone_format_guard,
    check_guided_username_format_guard,
    check_guided_username_attributed_domain_guard,
)
from .guards.shared import (
    _precheck_tool_args,
    check_crypto_explorer_guard,
    domain_from_email,
    is_domain_in_scope,
    is_free_email_provider,
    is_generic_platform_domain,
    is_internal_worklog_tool,
    normalize_scope_mode,
    parse_tool_call_args,
    split_scope_meta_args,
    summarize_tool_call,
)
from .guards.ai import (
    ai_scope_check,
    evaluate_ai_mode,
    require_scope_reason,
)
from .evidence import find_source_evidence
from .guards.explore import evaluate_explore_mode
from .guards.strict import (
    check_strict_artifact_guard,
    check_strict_domain_url_blockers,
    check_strict_identifier_match,
)
from .models import (
    ScopeBlockedCall,
    ScopeDecision,
    ScopePolicy,
    ScopePreflightResult,
)
from ..models import ToolEvidenceRecord
from ..targeting import detect_type, normalize_target_value

if TYPE_CHECKING:
    pass


# ---------------------------------------------------------------------------
# Scope policy construction
# ---------------------------------------------------------------------------


def build_scope_policy(
    *,
    primary_target: str,
    primary_type: str,
    related_targets: list[str],
    evidence: list[ToolEvidenceRecord],
    min_evidence_confidence: float = 0.6,
    approved_domains: set[str] | None = None,
) -> ScopePolicy:
    """Build a ScopePolicy from seed targets and accepted evidence."""
    allowed_terms: set[str] = set()
    allowed_domains: set[str] = set()
    username_attributed_domains: set[str] = set()

    def register_seed(value: str) -> None:
        normalized = normalize_target_value(value).strip().lower()
        if not normalized:
            return

        allowed_terms.add(normalized)
        kind = detect_type(value)

        if kind == "username":
            allowed_terms.add(f"@{normalized}")
            return

        if kind == "domain":
            domain = extract_domain(normalized)
            if domain:
                allowed_domains.add(domain)
            return

        if kind == "email":
            email_domain = domain_from_email(normalized)
            if email_domain and not is_free_email_provider(email_domain):
                allowed_domains.add(email_domain)

    register_seed(primary_target)
    for target in related_targets:
        register_seed(target)

    if approved_domains:
        for domain in approved_domains:
            clean = extract_domain(domain)
            if clean:
                allowed_domains.add(clean)

    for record in evidence:
        if not record.target_scope:
            continue
        if not record.scope_decision_allow:
            continue
        if getattr(record, "confidence", 1.0) < min_evidence_confidence:
            continue

        for obs in record.observed_artifacts:
            value = normalize_target_value(obs.value).strip().lower()
            if not value:
                continue
            # Tool input args never expand scope.
            if obs.source.startswith("arg:"):
                continue
            # Artifacts rejected by ai-mode attribution rating are kept in the
            # evidence record for audit but must not expand scope.
            if not getattr(obs, "scope_approved", True):
                continue

            if obs.kind == "domain":
                domain = extract_domain(value)
                if not domain:
                    continue

                if primary_type in {
                    "domain",
                    "company",
                    "email",
                } and is_domain_in_scope(domain, allowed_domains):
                    allowed_domains.add(domain)
                    continue

                if (
                    primary_type
                    in {
                        "person",
                        "username",
                        "phone",
                        "crypto",
                        "ip",
                        "geo",
                        "media",
                        "company",
                        "email",
                    }
                    and record.tool_name.startswith(ATTRIBUTABLE_DOMAIN_TOOL_PREFIXES)
                    and not is_generic_platform_domain(domain)
                ):
                    # Distinguish between two cases:
                    #
                    # 1. Platform presence — the target has a profile ON this domain
                    #    (e.g. example.com found because exammple.com/username exists).
                    #    → username_attributed_domains: only profile-path URLs allowed.
                    #
                    # 2. Personal ownership — the domain itself is likely owned by the
                    #    target (e.g. example.com found in a Twitter bio).
                    #    The primary target's identifier appears inside the domain name,
                    #    which is a strong ownership signal independent of URL path.
                    #    → allowed_domains: treat as a first-class investigation target.
                    #
                    primary_normalized = (
                        normalize_target_value(primary_target).strip().lower()
                    )
                    if primary_normalized and primary_normalized in domain:
                        allowed_domains.add(domain)
                    else:
                        username_attributed_domains.add(domain)
                continue

            if obs.kind == "username":
                if record.tool_name.startswith(ATTRIBUTABLE_DOMAIN_TOOL_PREFIXES):
                    primary_normalized = (
                        normalize_target_value(primary_target).strip().lower()
                    )
                    if primary_normalized and primary_normalized in value:
                        allowed_terms.add(value)
                        allowed_terms.add(f"@{value}")
                continue

            allowed_terms.add(value)
            if obs.kind == "email":
                email_domain = domain_from_email(value)
                if email_domain and not is_free_email_provider(email_domain):
                    allowed_domains.add(email_domain)

    crypto_explorer = (
        set(CRYPTO_EXPLORER_DOMAINS) if primary_type == "crypto" else set()
    )

    return ScopePolicy(
        primary_target=primary_target,
        primary_type=primary_type,
        related_targets=list(related_targets),
        allowed_terms=allowed_terms,
        allowed_domains=allowed_domains,
        crypto_explorer_domains=crypto_explorer,
        username_attributed_domains=username_attributed_domains,
    )


# ---------------------------------------------------------------------------
# Mode evaluators
# ---------------------------------------------------------------------------


def _evaluate_strict_mode(
    *,
    tool_name: str,
    tool_args: dict[str, Any],
    scope_policy: ScopePolicy,
) -> ScopeDecision:
    """
    Strict pipeline:
      precheck → crypto guard → domain/url blockers →
      artifact guard → identifier match → default block
    """
    all_string_values, early = _precheck_tool_args(tool_args)
    if all_string_values is None:
        return ScopeDecision(
            True, SCOPE_ALLOW_NO_ARGS, "precheck returned no string values"
        )

    decision = check_crypto_explorer_guard(
        tool_name=tool_name,
        all_string_values=all_string_values,
        scope_policy=scope_policy,
    )
    if decision:
        return decision

    decision = check_strict_domain_url_blockers(
        tool_name=tool_name,
        tool_args=tool_args,
        scope_policy=scope_policy,
    )
    if decision:
        return decision

    decision = check_strict_artifact_guard(
        tool_name=tool_name,
        tool_args=tool_args,
        scope_policy=scope_policy,
    )
    if decision:
        return decision

    decision = check_strict_identifier_match(
        all_string_values=all_string_values,
        scope_policy=scope_policy,
    )
    if decision:
        return decision

    return ScopeDecision(
        False,
        SCOPE_BLOCK_STRICT_UNMATCHED,
        f"blocked {tool_name}; arguments did not match the investigation scope",
    )


async def _evaluate_guided_mode(
    *,
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
    Guided pipeline:
      precheck → crypto guard → domain/url blockers → identity key guard →
      identifier match → domain artifact guard → AI
    """
    all_string_values, early = _precheck_tool_args(tool_args)
    if early:
        return early
    assert all_string_values is not None

    decision = check_crypto_explorer_guard(
        tool_name=tool_name,
        all_string_values=all_string_values,
        scope_policy=scope_policy,
    )
    if decision:
        return decision

    decision = check_guided_domain_url_blockers(
        tool_name=tool_name,
        tool_args=tool_args,
        scope_policy=scope_policy,
    )
    if decision:
        return decision

    decision = check_guided_email_format_guard(
        tool_name=tool_name,
        tool_args=tool_args,
    )
    if decision:
        return decision

    decision = check_guided_username_format_guard(
        tool_name=tool_name,
        tool_args=tool_args,
    )
    if decision:
        return decision

    decision = check_guided_phone_format_guard(
        tool_name=tool_name,
        tool_args=tool_args,
    )
    if decision:
        return decision

    decision = check_guided_identity_key_guard(
        tool_name=tool_name,
        tool_args=tool_args,
        scope_policy=scope_policy,
    )
    if decision:
        return decision

    decision = check_guided_username_attributed_domain_guard(
        tool_args=tool_args, scope_policy=scope_policy
    )
    if decision:
        return decision

    decision = check_guided_identifier_match(
        tool_args=tool_args,
        scope_policy=scope_policy,
    )
    if decision:
        return decision

    decision = check_guided_domain_artifact_guard(
        tool_name=tool_name,
        tool_args=tool_args,
        scope_policy=scope_policy,
    )
    if decision:
        return decision

    blocked = require_scope_reason(tool_name, scope_reason)
    if blocked:
        return blocked

    return await ai_scope_check(
        tool_name,
        tool_args,
        scope_reason,
        scope_policy,
        mode=mode,
        model=model,
        source_evidence_context=source_evidence_context,
        confidence_log=confidence_log,
        usage=usage,
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def evaluate_tool_scope(
    *,
    tool_name: str,
    tool_args: dict[str, Any],
    scope_reason: str = "",
    scope_policy: ScopePolicy,
    scope_mode: str = DEFAULT_SCOPE_MODE,
    model: str = "",
    source_evidence_context: str = "",
    confidence_log: "ConfidenceLog | None" = None,
    usage: "LLMUsage | None" = None,
) -> ScopeDecision:
    """
    Async scope decision for a single tool call.

    strict  — deterministic rules only.
    guided  — deterministic rules first, AI fallback for ambiguous cases.
    ai      — AI is the sole judge.
    explore — minimal filtering, permissive AI for open-ended investigations.
    """
    mode = normalize_scope_mode(scope_mode, DEFAULT_SCOPE_MODE)

    if is_internal_worklog_tool(tool_name):
        return ScopeDecision(
            True, SCOPE_ALLOW_INTERNAL_WORKLOG, "internal worklog tool"
        )

    if mode == "strict":
        return _evaluate_strict_mode(
            tool_name=tool_name,
            tool_args=tool_args,
            scope_policy=scope_policy,
        )

    if mode == "guided":
        return await _evaluate_guided_mode(
            tool_name=tool_name,
            tool_args=tool_args,
            scope_reason=scope_reason,
            scope_policy=scope_policy,
            mode=mode,
            model=model,
            source_evidence_context=source_evidence_context,
            confidence_log=confidence_log,
            usage=usage,
        )

    if mode == "ai":
        return await evaluate_ai_mode(
            tool_name=tool_name,
            tool_args=tool_args,
            scope_reason=scope_reason,
            scope_policy=scope_policy,
            model=model,
            source_evidence_context=source_evidence_context,
            confidence_log=confidence_log,
            usage=usage,
        )

    if mode == "explore":
        return await evaluate_explore_mode(
            tool_name=tool_name,
            tool_args=tool_args,
            scope_reason=scope_reason,
            scope_policy=scope_policy,
            model=model,
            source_evidence_context=source_evidence_context,
            confidence_log=confidence_log,
            usage=usage,
        )

    return ScopeDecision(
        False,
        SCOPE_BLOCK_AI_ERROR,
        f"unknown scope mode '{mode}'; expected strict, guided, ai, or explore",
    )


async def classify_scope_preflight(
    *,
    tool_calls: list[Any],
    primary_target: str,
    primary_type: str,
    related_targets: list[str],
    evidence: list[ToolEvidenceRecord],
    scope_mode: str,
    model: str = "",
    findings_excerpt: str = "",
    min_evidence_confidence: float = 0.6,
    approved_domains: set[str] | None = None,
    confidence_log: "ConfidenceLog | None" = None,
    usage: "LLMUsage | None" = None,
) -> ScopePreflightResult:
    scope_policy = build_scope_policy(
        primary_target=primary_target,
        primary_type=primary_type,
        related_targets=related_targets,
        evidence=evidence,
        min_evidence_confidence=min_evidence_confidence,
        approved_domains=approved_domains,
    )

    executable_tool_calls: list[Any] = []
    allowed_scope_decisions: dict[int, ScopeDecision] = {}
    blocked_calls: list[ScopeBlockedCall] = []

    for tc in tool_calls:
        tool_name = getattr(getattr(tc, "function", None), "name", "")
        if not isinstance(tool_name, str) or not tool_name.strip():
            decision = ScopeDecision(
                False,
                SCOPE_BLOCK_PARSE_FAILURE,
                "blocked tool call; missing or invalid function name",
            )
            blocked_calls.append(
                ScopeBlockedCall(tool_call=tc, tool_args=None, decision=decision)
            )
            continue

        args = parse_tool_call_args(tc)
        if args is None:
            decision = ScopeDecision(
                False,
                SCOPE_BLOCK_PARSE_FAILURE,
                f"blocked {tool_name}; could not parse tool call arguments",
            )
            blocked_calls.append(
                ScopeBlockedCall(tool_call=tc, tool_args=None, decision=decision)
            )
            continue

        execution_args, scope_reason = split_scope_meta_args(args)

        source_evidence_context = find_source_evidence(execution_args, evidence)

        decision = await evaluate_tool_scope(
            tool_name=tool_name,
            tool_args=execution_args,
            scope_reason=scope_reason,
            scope_policy=scope_policy,
            scope_mode=scope_mode,
            model=model,
            source_evidence_context=source_evidence_context,
            confidence_log=confidence_log,
            usage=usage,
        )

        if decision.allow:
            executable_tool_calls.append(tc)
            allowed_scope_decisions[id(tc)] = decision
        else:
            blocked_calls.append(
                ScopeBlockedCall(
                    tool_call=tc, tool_args=execution_args, decision=decision
                )
            )

    return ScopePreflightResult(
        executable_tool_calls=executable_tool_calls,
        allowed_scope_decisions=allowed_scope_decisions,
        blocked_calls=blocked_calls,
    )


__all__ = [
    "ScopeBlockedCall",
    "ScopeDecision",
    "ScopePolicy",
    "ScopePreflightResult",
    "build_scope_policy",
    "classify_scope_preflight",
    "evaluate_tool_scope",
    "parse_tool_call_args",
    "summarize_tool_call",
]
