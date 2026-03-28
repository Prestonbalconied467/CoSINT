"""
agent_runtime/scope/guards/guided.py  –  Guided mode scope guard functions

All guard functions called exclusively by the guided mode pipeline in scope.py.

Guided pipeline order:
  precheck → crypto guard → domain/url blockers → email format guard →
  username format guard → phone format guard → identity key guard (ip only) →
  identifier match → domain artifact guard → AI

Personal identifier handling in guided mode:
  email, username, and phone are format-checked only — a structurally valid
  value passes through to AI regardless of whether it matches allowed_terms.
  The AI judges attribution using the agent's scope_reason. This allows newly
  discovered personal identifiers to enter scope via AI approval rather than
  requiring a prior hardcoded term match.

  ip remains hardcoded: IP attribution should flow through the evidence and
  confidence system from prior tool results, not ad-hoc AI judgment mid-call.
"""

from __future__ import annotations

import re
from typing import Any, Callable

from shared.url_utils import extract_domain
from ..constants import (
    SCOPE_ALLOW_IDENTIFIER_MATCH,
    SCOPE_BLOCK_DOMAIN_IN_ARG,
    SCOPE_BLOCK_VALUE_IN_ARG,
)
from .shared import (
    collect_string_values,
    contains_allowed_term,
    domain_url_blocker_impl,
    fetch_url_term_allowed,
    has_in_scope_identifier_or_domain_match,
    is_domain_in_scope,
)
from ..models import ScopeDecision, ScopePolicy
from ...targeting import extract_artifact_observations, normalize_target_value

# email, username, phone are intentionally excluded — each has its own format
# guard that passes structurally valid values to AI for attribution judgment.
# Only ip remains in the hardcoded identity key guard.
_IDENTITY_KEYS: frozenset[str] = frozenset({"ip"})

# Requires: starts with alphanumeric, TLD is letters-only (min 2).
# Rejects NPM package strings like react@18.3.1 or pkg@1.0.0_react.
_EMAIL_RE = re.compile(r"^[a-zA-Z0-9][^@\s]*@[^@\s]+\.[a-zA-Z]{2,}$")
_USERNAME_RE = re.compile(r"^[a-zA-Z0-9](?:[a-zA-Z0-9._\-]{0,48}[a-zA-Z0-9])?$")
_PHONE_RE = re.compile(r"^\+?[0-9\s\-().]{7,20}$")


def check_guided_domain_url_blockers(
    *,
    tool_name: str,
    tool_args: dict[str, Any],
    scope_policy: ScopePolicy,
) -> ScopeDecision | None:
    """
    Guided: block explicit domain/host/url arguments that are out of scope.

    URLs are checked for in-scope terms before blocking — a URL like
    ebay.com/usr/champmq is allowed through if 'champmq' is an in-scope
    identifier, even though ebay.com is not in allowed_domains.
    """
    return domain_url_blocker_impl(
        tool_name=tool_name,
        tool_args=tool_args,
        scope_policy=scope_policy,
        check_url_terms=True,
    )


def _check_format_guard(
    *,
    field: str,
    regex: re.Pattern[str],
    tool_name: str,
    tool_args: dict[str, Any],
    pre_process: Callable[[str], str] = str.strip,
) -> ScopeDecision | None:
    for key, raw in tool_args.items():
        if not isinstance(key, str) or field not in key.strip().lower():
            continue
        for candidate in collect_string_values(raw):
            candidate = pre_process(candidate.strip())
            if not candidate:
                continue
            if not regex.match(candidate):
                return ScopeDecision(
                    False,
                    SCOPE_BLOCK_VALUE_IN_ARG,
                    f"blocked malformed {field} value '{candidate}' "
                    f"in argument '{field}' for {tool_name}",
                )
    return None


def check_guided_email_format_guard(*, tool_name, tool_args):
    return _check_format_guard(
        field="email", regex=_EMAIL_RE, tool_name=tool_name, tool_args=tool_args
    )


def check_guided_username_format_guard(*, tool_name, tool_args):
    return _check_format_guard(
        field="username",
        regex=_USERNAME_RE,
        tool_name=tool_name,
        tool_args=tool_args,
        pre_process=lambda s: s.strip().lstrip("@"),
    )


def check_guided_phone_format_guard(*, tool_name, tool_args):
    return _check_format_guard(
        field="phone", regex=_PHONE_RE, tool_name=tool_name, tool_args=tool_args
    )


def check_guided_identity_key_guard(
    *,
    tool_name: str,
    tool_args: dict[str, Any],
    scope_policy: ScopePolicy,
) -> ScopeDecision | None:
    """
    Guided: block ip arguments that are out of scope.

    email, username, and phone are handled by their own format guards above.
    Must run before check_guided_identifier_match to prevent an in-scope term
    elsewhere in the args from masking an out-of-scope ip.
    """
    for key, raw in tool_args.items():
        if not isinstance(key, str):
            continue
        key_name = key.strip().lower()
        if key_name not in _IDENTITY_KEYS:
            continue

        for candidate in collect_string_values(raw):
            if not candidate:
                continue
            observations = extract_artifact_observations(
                text=candidate, source=f"arg:{key_name}"
            )
            for obs in observations:
                normalized = normalize_target_value(obs.value).strip().lower()
                if obs.kind == "domain":
                    obs_domain = extract_domain(normalized)
                    if obs_domain and not is_domain_in_scope(
                        obs_domain, scope_policy.allowed_domains
                    ):
                        return ScopeDecision(
                            False,
                            SCOPE_BLOCK_DOMAIN_IN_ARG,
                            f"blocked out-of-scope domain '{obs_domain}' "
                            f"in argument '{key}' for {tool_name}",
                        )
                elif normalized and normalized not in scope_policy.allowed_terms:
                    return ScopeDecision(
                        False,
                        SCOPE_BLOCK_VALUE_IN_ARG,
                        f"blocked out-of-scope value '{obs.value}' "
                        f"in argument '{key}' for {tool_name}",
                    )
    return None


def check_guided_identifier_match(
    *,
    tool_args: dict[str, Any],
    scope_policy: ScopePolicy,
) -> ScopeDecision | None:
    """
    Guided: allow if any non-identity argument contains a known in-scope identifier or domain.

    Identity fields (email, username, phone) are deliberately excluded — those
    fields must always reach AI after their format guard, even if the value
    happens to match an allowed term. The identifier match only fires on
    arguments like query, target, search, handle, etc. that are not personal
    identifiers being directly asserted as the investigation subject.
    """
    # Collect string values only from non-identity keys.
    # Exclude any key whose name contains "email", "username", or "phone" —
    # those fields must always reach AI after their format guard, even if
    # the value happens to match an allowed term.
    non_identity_values: list[str] = []
    for key, raw in tool_args.items():
        if isinstance(key, str):
            key_lower = key.strip().lower()
            if any(token in key_lower for token in ("email", "username", "phone")):
                continue
        non_identity_values.extend(collect_string_values(raw))

    if not non_identity_values:
        return None

    if has_in_scope_identifier_or_domain_match(
        all_string_values=non_identity_values,
        scope_policy=scope_policy,
    ):
        # Before allowing, verify no non-identity arg also contains an out-of-scope
        # domain artifact. Without this check, a call like:
        #   osint_web_search(query="kostyagladkikh", target_site="evilsite.com")
        # would be allowed purely because the query matched an in-scope term,
        # with evilsite.com never examined. The domain artifact guard that follows
        # only runs if we return None here, so we must catch it now.
        for value in non_identity_values:
            domain = extract_domain(value)
            if domain and not is_domain_in_scope(domain, scope_policy.allowed_domains):
                return None  # fall through to domain artifact guard

        return ScopeDecision(
            True,
            SCOPE_ALLOW_IDENTIFIER_MATCH,
            "arguments contain an in-scope target identifier",
        )
    return None


def check_guided_domain_artifact_guard(
    *,
    tool_name: str,
    tool_args: dict[str, Any],
    scope_policy: ScopePolicy,
) -> ScopeDecision | None:
    """
    Guided: block non-identity arguments containing a detected out-of-scope domain artifact.

    Runs after check_guided_identifier_match as a last deterministic check before
    the AI fallback. Skips ip, email, username, and phone — all handled earlier
    in the pipeline.

    URLs are treated leniently: if the full candidate string (URL or otherwise)
    contains an in-scope term, the out-of-scope domain artifact is not blocked.
    This covers both URL-keyed args (url=https://ebay.com/usr/target) and
    embedded URLs in free-text args (query="check https://github.com/target").
    """
    # Skip ip, and any key whose name contains email, username, or phone —
    # all handled earlier in the pipeline. Use substring match to cover
    # non-standard names like target_email, user_phone, email_address, etc.
    for key, raw in tool_args.items():
        key_name = key.strip().lower() if isinstance(key, str) else "arg"
        if key_name in _IDENTITY_KEYS:
            continue
        if any(token in key_name for token in ("email", "username", "phone")):
            continue
        for candidate in collect_string_values(raw):
            if not candidate:
                continue
            # URL-keyed args: allow through only when osint_scraper_fetch_url
            # is fetching a URL that contains an in-scope term. Any broader
            # term match in the URL would have already fired check_guided_identifier_match
            # upstream, so this branch is only reachable for scraper fetch carve-outs.
            if key_name in {"url", "repo_url", "image_url"} and fetch_url_term_allowed(
                tool_name, candidate, scope_policy.allowed_terms
            ):
                continue
            observations = extract_artifact_observations(
                text=candidate, source=f"arg:{key_name}"
            )
            for obs in observations:
                if obs.kind != "domain":
                    continue
                obs_domain = extract_domain(
                    normalize_target_value(obs.value).strip().lower()
                )
                if obs_domain and not is_domain_in_scope(
                    obs_domain, scope_policy.allowed_domains
                ):
                    # Also allow through if the candidate string itself contains an in-scope
                    # term — covers embedded URLs in free-text args like query or search.
                    if contains_allowed_term(candidate, scope_policy.allowed_terms):
                        continue
                    return ScopeDecision(
                        False,
                        SCOPE_BLOCK_DOMAIN_IN_ARG,
                        f"blocked out-of-scope domain '{obs_domain}' "
                        f"in argument '{key_name}' for {tool_name}",
                    )
    return None


def check_guided_username_attributed_domain_guard(
    tool_args: dict,
    scope_policy: ScopePolicy,
) -> ScopeDecision | None:
    """
    Domains promoted via username attribution are only valid when the tool call
    references the target's profile URL — i.e. the URL path contains the primary
    target username.  Bare domain args (domain=, host=, hostname=) and URLs whose
    path does not contain the username are blocked here rather than passed to AI.

    This prevents "example.com was found in a profile URL" from quietly becoming
    "example.com is now an in-scope investigation target" that the agent can
    enumerate, scrape at the root, or run domain-investigation tools against.
    """
    if not scope_policy.username_attributed_domains:
        return None

    username = scope_policy.primary_target.lower().strip()

    for key, raw in tool_args.items():
        key_lower = key.strip().lower() if isinstance(key, str) else ""

        # Bare domain/host keys: block outright — there is no path that can
        # contain the username when the argument is just a domain string.
        if key_lower in {"domain", "host", "hostname"}:
            for candidate in collect_string_values(raw):
                candidate = candidate.strip().lower()
                if not candidate:
                    continue
                d = extract_domain(candidate) or candidate
                if d in scope_policy.username_attributed_domains:
                    return ScopeDecision(
                        False,
                        SCOPE_BLOCK_DOMAIN_IN_ARG,
                        f"domain '{d}' was promoted via username attribution but is being used "
                        f"as a bare domain argument — only profile URLs containing "
                        f"'{username}' are permitted for this domain",
                    )

        # URL keys: allow only when the path contains the username.
        if key_lower in {"url", "target_url", "repo_url"}:
            for url_val in collect_string_values(raw):
                url_val = url_val.strip().lower()
                if not url_val:
                    continue

                domain = extract_domain(url_val)
                if not domain or domain not in scope_policy.username_attributed_domains:
                    continue

                try:
                    from urllib.parse import urlparse

                    path = urlparse(url_val).path.lower().strip("/")
                except Exception:
                    path = ""

                if username in path:
                    return None  # passes — let the normal allow flow continue

                return ScopeDecision(
                    False,
                    SCOPE_BLOCK_DOMAIN_IN_ARG,
                    f"domain '{domain}' was promoted via username attribution but URL path "
                    f"does not contain the target username '{username}' — "
                    f"scrape the profile URL ({domain}/{username}) not the homepage",
                )

    return None


__all__ = [
    "check_guided_domain_artifact_guard",
    "check_guided_domain_url_blockers",
    "check_guided_email_format_guard",
    "check_guided_identifier_match",
    "check_guided_identity_key_guard",
    "check_guided_phone_format_guard",
    "check_guided_username_format_guard",
    "check_guided_username_attributed_domain_guard",
]
