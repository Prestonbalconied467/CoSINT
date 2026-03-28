"""
agent_runtime/scope/guards/strict.py  –  Strict mode scope guard functions

All guard functions called exclusively by the strict mode pipeline in scope.py.

Strict pipeline order:
  precheck → crypto guard → domain/url blockers →
  artifact guard → identifier match → default block
"""

from __future__ import annotations

from typing import Any

from shared.url_utils import extract_domain
from ..constants import (
    SCOPE_ALLOW_IDENTIFIER_MATCH,
    SCOPE_BLOCK_DOMAIN_IN_ARG,
    SCOPE_BLOCK_VALUE_IN_ARG,
)
from .shared import (
    collect_string_values,
    domain_url_blocker_impl,
    has_in_scope_identifier_or_domain_match,
    is_domain_in_scope,
)
from ..models import ScopeDecision, ScopePolicy
from ...targeting import extract_artifact_observations, normalize_target_value


def check_strict_domain_url_blockers(
    *,
    tool_name: str,
    tool_args: dict[str, Any],
    scope_policy: ScopePolicy,
) -> ScopeDecision | None:
    """
    Strict: block explicit domain/host/url arguments that are out of scope.

    Only osint_scraper_fetch_url gets a URL-term carve-out (handled inside
    domain_url_blocker_impl via fetch_url_term_allowed). All other URL args
    are blocked if their host is not in allowed_domains. Plain domain/host
    keys are always blocked outright if not in scope.

    check_url_terms is False here because the strict artifact guard that
    follows scans every string value for domain artifacts anyway — a broader
    URL-term carve-out at this step would be overridden immediately.
    """
    return domain_url_blocker_impl(
        tool_name=tool_name,
        tool_args=tool_args,
        scope_policy=scope_policy,
        check_url_terms=False,
    )


def check_strict_artifact_guard(
    *,
    tool_name: str,
    tool_args: dict[str, Any],
    scope_policy: ScopePolicy,
) -> ScopeDecision | None:
    """
    Strict: block any argument containing a detected artifact that is out of scope.

    Scans all keys including identity fields (email, username, ip, phone).
    Any domain artifact not in allowed_domains, or any non-domain artifact
    not in allowed_terms, causes a block — unconditionally. There is no
    in-scope term carve-out here; that is open mode behaviour only.
    """
    for key, raw in tool_args.items():
        key_name = key.strip().lower() if isinstance(key, str) else "arg"
        for candidate in collect_string_values(raw):
            if not candidate:
                continue
            observations = extract_artifact_observations(
                text=candidate, source=f"arg:{key_name}"
            )
            for obs in observations:
                normalized = normalize_target_value(obs.value).strip().lower()
                if not normalized:
                    continue
                if obs.kind == "domain":
                    obs_domain = extract_domain(normalized)
                    if obs_domain and not is_domain_in_scope(
                        obs_domain, scope_policy.allowed_domains
                    ):
                        return ScopeDecision(
                            False,
                            SCOPE_BLOCK_DOMAIN_IN_ARG,
                            f"blocked out-of-scope domain '{obs_domain}' "
                            f"in argument '{key_name}' for {tool_name}",
                        )
                elif normalized not in scope_policy.allowed_terms:
                    return ScopeDecision(
                        False,
                        SCOPE_BLOCK_VALUE_IN_ARG,
                        f"blocked out-of-scope value '{obs.value}' "
                        f"in argument '{key_name}' for {tool_name}",
                    )
    return None


def check_strict_identifier_match(
    *,
    all_string_values: list[str],
    scope_policy: ScopePolicy,
) -> ScopeDecision | None:
    """Strict: allow if any argument contains a known in-scope identifier or domain."""
    # Block if any out-of-scope domain is present, even if an in-scope identifier is present
    for value in all_string_values:
        domain = extract_domain(value)
        if domain and not is_domain_in_scope(domain, scope_policy.allowed_domains):
            return None  # Out-of-scope domain present, do not allow
    if has_in_scope_identifier_or_domain_match(
        all_string_values=all_string_values,
        scope_policy=scope_policy,
    ):
        return ScopeDecision(
            True,
            SCOPE_ALLOW_IDENTIFIER_MATCH,
            "arguments contain an in-scope target identifier",
        )
    return None


__all__ = [
    "check_strict_artifact_guard",
    "check_strict_domain_url_blockers",
    "check_strict_identifier_match",
]
