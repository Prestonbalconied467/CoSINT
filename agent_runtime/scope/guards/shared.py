"""
agent_runtime/scope/guards/shared.py  –  Shared scope utility functions

String/value helpers, domain/email helpers, tool call argument helpers,
and infrastructure guards used by both strict and open mode pipelines.

Nothing in this file is mode-specific.
"""

from __future__ import annotations

import json
import re
from typing import Any

from shared.url_utils import extract_domain
from ..constants import (
    SCOPE_ALLOW_IDENTIFIER_MATCH,
    SCOPE_BLOCK_DOMAIN,
    SCOPE_BLOCK_URL_HOST,
    SCOPE_ALLOW_NO_ARGS,
    SCOPE_ALLOW_NON_STRING_ARGS,
)
from ..models import ScopeDecision, ScopePolicy


# ---------------------------------------------------------------------------
# String / value helpers
# ---------------------------------------------------------------------------


def collect_string_values(d: Any) -> list[str]:
    """Recursively collect all string leaf values from a dict/list/scalar."""
    if isinstance(d, str):
        return [d]
    values: list[str] = []
    if isinstance(d, dict):
        for v in d.values():
            values.extend(collect_string_values(v))
    elif isinstance(d, (list, tuple)):
        for item in d:
            values.extend(collect_string_values(item))
    return values


def collect_all_string_values(tool_args: dict[str, Any]) -> tuple[list[str], bool]:
    """Return flattened string leaves and whether any string argument exists."""
    all_string_values: list[str] = []
    has_any_string = False
    for value in tool_args.values():
        leaves = collect_string_values(value)
        if leaves:
            has_any_string = True
            all_string_values.extend(leaves)
    return all_string_values, has_any_string


# ---------------------------------------------------------------------------
# Domain / email helpers
# ---------------------------------------------------------------------------


def domain_from_email(email: str) -> str:
    """Extract domain part from an email address."""
    email = email.strip().lower()
    if "@" not in email:
        return ""
    return email.split("@")[-1]


_FREE_EMAIL_PROVIDERS: frozenset[str] = frozenset(
    {
        "gmail.com",
        "yahoo.com",
        "outlook.com",
        "hotmail.com",
        "aol.com",
        "protonmail.com",
        "tutanota.com",
        "yandex.com",
        "mail.com",
        "zoho.com",
        "icloud.com",
        "fastmail.com",
        "mailbox.org",
        "gmx.com",
        "web.de",
        "t-online.de",
        "example.com",
        "test.com",
        "localhost",
    }
)


def is_free_email_provider(domain: str) -> bool:
    """Return True if the domain is a well-known free email provider."""
    d = domain.strip().lower()
    return d in _FREE_EMAIL_PROVIDERS or any(
        d.endswith(f".{p}") for p in _FREE_EMAIL_PROVIDERS
    )


_GENERIC_PLATFORMS: frozenset[str] = frozenset(
    {
        "github.com",
        "gitlab.com",
        "bitbucket.org",
        "twitter.com",
        "x.com",
        "facebook.com",
        "instagram.com",
        "linkedin.com",
        "reddit.com",
        "pastebin.com",
        "medium.com",
        "stackoverflow.com",
        "discord.com",
        "telegram.org",
        "whatsapp.com",
        "signal.org",
        "youtube.com",
        "vimeo.com",
        "twitch.tv",
    }
)


def is_generic_platform_domain(domain: str) -> bool:
    """Return True if the domain is a large shared platform (not an investigation target)."""
    d = domain.strip().lower()
    return d in _GENERIC_PLATFORMS or any(
        d.endswith(f".{p}") for p in _GENERIC_PLATFORMS
    )


def contains_allowed_term(text: str, allowed_terms: set[str]) -> bool:
    """Return True when an allowed term appears as a distinct token/value."""
    text_lower = text.lower()
    for term in allowed_terms:
        token = term.strip().lower()
        if not token:
            continue
        if any(ch in token for ch in {".", "@", ":", "/", "_", "-"}):
            if token in text_lower:
                return True
            continue
        if re.search(rf"(?<![a-z0-9]){re.escape(token)}(?![a-z0-9])", text_lower):
            return True
    return False


def is_domain_in_scope(domain: str, allowed_domains: set[str]) -> bool:
    """Return True for exact domain match or subdomain of any allowed domain."""
    d = domain.strip().lower()
    if d in allowed_domains:
        return True
    return any(d.endswith(f".{allowed}") for allowed in allowed_domains)


def has_in_scope_identifier_or_domain_match(
    *,
    all_string_values: list[str],
    scope_policy: ScopePolicy,
) -> bool:
    """Return True if any string value matches an in-scope term or domain."""
    joined = " ".join(all_string_values)
    if contains_allowed_term(joined, scope_policy.allowed_terms):
        return True
    for value in all_string_values:
        domain = extract_domain(value)
        if domain and is_domain_in_scope(domain, scope_policy.allowed_domains):
            return True
    return False


# ---------------------------------------------------------------------------
# Tool call argument helpers
# ---------------------------------------------------------------------------


def parse_tool_call_args(tool_call: Any) -> dict[str, Any] | None:
    """
    Parse tool call arguments from JSON.

    Returns None on parse failure so callers can distinguish 'no args'
    from 'unparseable/non-object args' and handle the latter as a block.
    """
    function_obj = getattr(tool_call, "function", None)
    if function_obj is None:
        return None

    raw_args = getattr(function_obj, "arguments", "")
    if raw_args is None:
        return {}
    if isinstance(raw_args, dict):
        return raw_args
    if not isinstance(raw_args, str):
        return None

    raw = raw_args.strip()
    if not raw:
        return {}

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return None

    return parsed if isinstance(parsed, dict) else None


def split_scope_meta_args(
    tool_args: dict[str, Any] | None,
) -> tuple[dict[str, Any], str]:
    """Return (execution_args, scope_reason) extracted from parsed tool args."""
    if not isinstance(tool_args, dict):
        return {}, ""

    execution_args = dict(tool_args)
    raw_reason = execution_args.pop("reason", "")
    scope_reason = raw_reason.strip() if isinstance(raw_reason, str) else ""
    return execution_args, scope_reason


def summarize_tool_call(name: str, args: dict[str, Any] | None) -> str:
    """Return a short human-readable summary of a tool call."""
    if not args:
        return f"{name}()"
    preview = [f"{k}={v}" for k, v in list(args.items())[:2]]
    suffix = ", ..." if len(args) > 2 else ""
    return f"{name}({', '.join(preview)}{suffix})"


# ---------------------------------------------------------------------------
# General scope helpers
# ---------------------------------------------------------------------------


def normalize_scope_mode(scope_mode: str, default_scope_mode: str) -> str:
    """Normalize scope mode string to one of the supported runtime modes."""
    return (scope_mode or default_scope_mode).strip().lower()


def is_internal_worklog_tool(tool_name: str) -> bool:
    """Return True for internal note/todo tools that are always in scope."""
    return tool_name.startswith("osint_todo_") or tool_name.startswith("osint_notes_")


# ---------------------------------------------------------------------------
# Shared infrastructure guard
# ---------------------------------------------------------------------------


def check_crypto_explorer_guard(
    *,
    tool_name: str,
    all_string_values: list[str],
    scope_policy: ScopePolicy,
) -> ScopeDecision | None:
    """
    Allow crypto explorer domains for blockchain tools; block domain investigation on them.

    This is infrastructure classification, not a scope rule judgment — crypto
    explorer domains are never valid investigation targets regardless of mode.
    Runs in both strict and open modes.
    """
    crypto_tool_prefixes = (
        "osint_crypto_",
        "osint_blockchain_",
        "osint_wallet_",
        "osint_tx_",
    )
    domain_invest_prefixes = (
        "osint_domain_",
        "osint_fetch_",
        "osint_scrape_",
        "osint_web_",
        "osint_public_",
    )

    if not scope_policy.crypto_explorer_domains:
        return None

    for value in all_string_values:
        domain = extract_domain(value)
        if not domain or domain not in scope_policy.crypto_explorer_domains:
            continue
        if any(tool_name.startswith(prefix) for prefix in crypto_tool_prefixes):
            return ScopeDecision(
                True,
                SCOPE_ALLOW_IDENTIFIER_MATCH,
                f"crypto explorer domain '{domain}' allowed for blockchain tool",
            )
        if any(tool_name.startswith(prefix) for prefix in domain_invest_prefixes):
            return ScopeDecision(
                False,
                SCOPE_BLOCK_DOMAIN,
                f"blocked domain investigation on crypto explorer '{domain}' — "
                "explorer domains are tool infrastructure, not investigation targets",
            )
    return None


# ---------------------------------------------------------------------------
# Private: fetch-url term carve-out (used by both strict and open url blockers)
# ---------------------------------------------------------------------------


def fetch_url_term_allowed(
    tool_name: str, candidate: str, allowed_terms: set[str]
) -> bool:
    """
    Return True when a fetch-url tool contains an in-scope term in the URL.

    osint_scraper_fetch_url may legitimately follow links that live on
    third-party domains (CDNs, aggregators) as long as the URL itself
    contains an in-scope identifier.
    """
    return tool_name == "osint_scraper_fetch_url" and contains_allowed_term(
        candidate, allowed_terms
    )


# ---------------------------------------------------------------------------
# Private: domain/url blocker implementation (used by both strict and open)
# ---------------------------------------------------------------------------


def domain_url_blocker_impl(
    *,
    tool_name: str,
    tool_args: dict[str, Any],
    scope_policy: ScopePolicy,
    check_url_terms: bool = False,
) -> ScopeDecision | None:
    """
    Block calls whose explicit domain/host/url arguments are out of scope.

    Handles keys: domain, host, hostname, url, repo_url, image_url.

    check_url_terms — when True (open mode), any URL whose full value contains
    an in-scope term is allowed through regardless of the host domain. This
    covers cases like ebay.com/usr/username where the host is generic but the
    path contains the target identifier.
    When False (strict mode), only osint_scraper_fetch_url gets this carve-out.
    """
    for key, raw in tool_args.items():
        if not isinstance(key, str):
            continue
        lowered_key = key.strip().lower()
        string_leaves = collect_string_values(raw)
        if not string_leaves:
            continue

        candidates = string_leaves if not isinstance(raw, str) else [raw.strip()]
        for candidate in candidates:
            if not candidate:
                continue

            domain = extract_domain(candidate)

            if lowered_key in {"domain", "host", "hostname"} and domain:
                if not is_domain_in_scope(domain, scope_policy.allowed_domains):
                    return ScopeDecision(
                        False,
                        SCOPE_BLOCK_DOMAIN,
                        f"blocked out-of-scope domain '{domain}' for {tool_name}",
                    )

            if lowered_key in {"url", "repo_url", "image_url"} and domain:
                if not is_domain_in_scope(domain, scope_policy.allowed_domains):
                    url_has_term = (
                        check_url_terms
                        and contains_allowed_term(candidate, scope_policy.allowed_terms)
                    ) or fetch_url_term_allowed(
                        tool_name, candidate, scope_policy.allowed_terms
                    )
                    if url_has_term:
                        continue
                    return ScopeDecision(
                        False,
                        SCOPE_BLOCK_URL_HOST,
                        f"blocked out-of-scope URL host '{domain}' for {tool_name}",
                    )
    return None


# ---------------------------------------------------------------------------
# Arg pre-check (shared early-exit logic, used by all three mode pipelines)
# ---------------------------------------------------------------------------


def _precheck_tool_args(
    tool_args: dict,
) -> tuple[list[str] | None, "ScopeDecision | None"]:
    """
    Handle early allow cases before any mode-specific guard runs.

    Returns (all_string_values, None) when further checks are needed.
    Returns (None, ScopeDecision) when the call can be resolved immediately.

    These are practical short-circuits, not scope rule judgments:
      - No args → nothing to evaluate → allow
      - No string values → AI and rules have nothing to reason about → allow
    """

    if not tool_args:
        return None, ScopeDecision(
            True,
            SCOPE_ALLOW_NO_ARGS,
            "tool has no arguments — no targetable identity information to evaluate",
        )

    all_string_values, has_any_string = collect_all_string_values(tool_args)
    if not has_any_string:
        return None, ScopeDecision(
            True,
            SCOPE_ALLOW_NON_STRING_ARGS,
            "arguments contain no string values at any nesting level — "
            "non-string arguments carry no targetable identity information",
        )

    return all_string_values, None


# ---------------------------------------------------------------------------
# Scope evidence summary (structured context for AI raters)
# ---------------------------------------------------------------------------


def build_scope_evidence_summary(scope_policy: "ScopePolicy") -> str:
    """
    Build a compact, structured summary of what has been confirmed in scope
    so far.  Used by both AI raters instead of a raw findings-excerpt blob.

    The scope_policy already encodes the authoritative output of
    build_scope_policy() — every term and domain it contains was promoted
    from accepted evidence.  Presenting it in a labelled, readable form gives
    the AI exactly what it needs without raw text noise or arbitrary truncation.
    """
    lines: list[str] = [
        f"Primary target : {scope_policy.primary_target}  (type: {scope_policy.primary_type})"
    ]

    if scope_policy.related_targets:
        lines.append(f"Related targets: {', '.join(scope_policy.related_targets)}")

    # Separate domains from non-domain terms for readability
    domains = sorted(scope_policy.allowed_domains)
    # Keep @ variants alongside their base term — drop bare duplicates
    display_terms = sorted(scope_policy.allowed_terms)

    if domains:
        lines.append(f"Confirmed domains     : {', '.join(domains)}")
    if display_terms:
        lines.append(
            f"Confirmed identifiers : {', '.join(display_terms[:40])} and {len(display_terms) - 40} more"
        )
    if scope_policy.username_attributed_domains:
        uad = sorted(scope_policy.username_attributed_domains)
        lines.append(f"Platform domains (profile-path only): {', '.join(uad)}")

    if not domains and not display_terms:
        lines.append(
            "No confirmed scope artifacts yet — only the primary target is in scope."
        )

    return "\n".join(lines)


__all__ = [
    # String / value helpers
    "collect_string_values",
    "collect_all_string_values",
    # Domain / email helpers
    "contains_allowed_term",
    "domain_from_email",
    "has_in_scope_identifier_or_domain_match",
    "is_domain_in_scope",
    "is_free_email_provider",
    "is_generic_platform_domain",
    # Tool call argument helpers
    "parse_tool_call_args",
    "split_scope_meta_args",
    "summarize_tool_call",
    # General scope helpers
    "is_internal_worklog_tool",
    "normalize_scope_mode",
    # Shared infrastructure guard
    "check_crypto_explorer_guard",
    # Shared implementation helpers (consumed by strict/open helper modules)
    "domain_url_blocker_impl",
    "fetch_url_term_allowed",
    "_precheck_tool_args",
    "build_scope_evidence_summary",
]
