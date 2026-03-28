"""
tools/pivot_extractor.py

Pre-extracts typed OSINT pivot artifacts from URLs found in search results.
Called by format_results() in search_utils.py so the agent always receives
structured pivots rather than raw URLs it has to parse itself.

Design rules:
- Pure functions only. No I/O, no async, no side-effects.
- A Pivot is a named tuple so callers can pattern-match on .pivot_type.
- extract_pivots() never raises — bad URLs produce an empty list.
- Platform patterns are ordered: more specific patterns before catch-alls.
- Only actionable pivot types are extracted (username, domain).
  Paths / query params / fragments are preserved as context only.
"""

from __future__ import annotations

import re
from typing import NamedTuple
from urllib.parse import urlparse


# ---------------------------------------------------------------------------
# Data type
# ---------------------------------------------------------------------------


class Pivot(NamedTuple):
    pivot_type: str  # "username" | "domain"
    value: str  # the extracted identifier, normalised (lowercased where safe)
    platform: str  # human-readable platform label, e.g. "GitHub"
    context: str | None  # extra context, e.g. repo name, subreddit — never investigated


# ---------------------------------------------------------------------------
# Platform registry
# ---------------------------------------------------------------------------
# Each entry: (compiled_regex, platform_label, value_group, context_group_or_None)
# Groups are 1-indexed to match re.Match.group(n).
#
# Patterns are tried in order — put more specific patterns above catch-alls
# for the same domain.

_PLATFORM_PATTERNS: list[tuple[re.Pattern[str], str, int, int | None]] = [
    # ── GitHub ────────────────────────────────────────────────────────────
    # /username/repo  →  username + repo as context
    (
        re.compile(r"github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)", re.I),
        "GitHub",
        1,
        2,
    ),
    # /username  (org pages, profile)
    (re.compile(r"github\.com/([A-Za-z0-9_.-]+)/?$", re.I), "GitHub", 1, None),
    # ── Twitter / X ───────────────────────────────────────────────────────
    (
        re.compile(r"(?:^x\.com|twitter\.com)/([A-Za-z0-9_]+)(?:/.*)?", re.I),
        "Twitter/X",
        1,
        None,
    ),
    # ── Reddit ────────────────────────────────────────────────────────────
    # /user/name  or  /u/name  — subreddit posts as context
    (
        re.compile(
            r"reddit\.com/u(?:ser)?/([A-Za-z0-9_-]+)(?:/comments/[^/]+/([^/?#]+))?",
            re.I,
        ),
        "Reddit",
        1,
        2,
    ),
    # ── LinkedIn ──────────────────────────────────────────────────────────
    (re.compile(r"linkedin\.com/in/([A-Za-z0-9_%-]+)", re.I), "LinkedIn", 1, None),
    (
        re.compile(r"linkedin\.com/company/([A-Za-z0-9_%-]+)", re.I),
        "LinkedIn (company)",
        1,
        None,
    ),
    # ── Instagram ─────────────────────────────────────────────────────────
    (
        re.compile(r"instagram\.com/([A-Za-z0-9_.]+)/?(?:\?.*)?$", re.I),
        "Instagram",
        1,
        None,
    ),
    # ── TikTok ────────────────────────────────────────────────────────────
    (re.compile(r"tiktok\.com/@([A-Za-z0-9_.]+)", re.I), "TikTok", 1, None),
    # ── Telegram ──────────────────────────────────────────────────────────
    (re.compile(r"t\.me/([A-Za-z0-9_]+)", re.I), "Telegram", 1, None),
    # ── YouTube ───────────────────────────────────────────────────────────
    (re.compile(r"youtube\.com/@([A-Za-z0-9_.]+)", re.I), "YouTube", 1, None),
    (
        re.compile(r"youtube\.com/(?:channel|user)/([A-Za-z0-9_-]+)", re.I),
        "YouTube",
        1,
        None,
    ),
    # ── Mastodon / generic fediverse  ─────────────────────────────────────
    # Matches instance.social/@username
    (
        re.compile(r"([a-z0-9.-]+\.[a-z]{2,})/@([A-Za-z0-9_.]+)", re.I),
        "Fediverse",
        2,
        None,
    ),
    # ── GitLab ────────────────────────────────────────────────────────────
    (
        re.compile(r"gitlab\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)", re.I),
        "GitLab",
        1,
        2,
    ),
    (re.compile(r"gitlab\.com/([A-Za-z0-9_.-]+)/?$", re.I), "GitLab", 1, None),
    # ── HackerNews ────────────────────────────────────────────────────────
    (
        re.compile(r"news\.ycombinator\.com/user\?id=([A-Za-z0-9_-]+)", re.I),
        "HackerNews",
        1,
        None,
    ),
    # ── Keybase ───────────────────────────────────────────────────────────
    (re.compile(r"keybase\.io/([A-Za-z0-9_]+)", re.I), "Keybase", 1, None),
    # ── npm ───────────────────────────────────────────────────────────────
    (re.compile(r"npmjs\.com/~([A-Za-z0-9_-]+)", re.I), "npm", 1, None),
    # ── PyPI ──────────────────────────────────────────────────────────────
    (re.compile(r"pypi\.org/user/([A-Za-z0-9_.-]+)", re.I), "PyPI", 1, None),
    # ── Pastebin / paste sites ────────────────────────────────────────────
    # No useful username in path — skip; these are raw content pivots not identity
]

# Platforms that are pure hosting infrastructure.
# URLs from these hosts produce a domain pivot only if no username was found,
# and even then the domain itself is the platform — not target-owned.
_PLATFORM_HOSTS: frozenset[str] = frozenset(
    {
        "github.com",
        "twitter.com",
        "x.com",
        "reddit.com",
        "linkedin.com",
        "instagram.com",
        "tiktok.com",
        "t.me",
        "telegram.org",
        "youtube.com",
        "gitlab.com",
        "news.ycombinator.com",
        "keybase.io",
        "npmjs.com",
        "pypi.org",
        "facebook.com",
        "vk.com",
        "ok.ru",
        "tumblr.com",
        "medium.com",
        "pastebin.com",
        "paste.ee",
        "ghostbin.com",
        "dpaste.com",
        "haveibeenpwned.com",
        "hunter.io",
        "emailrep.io",
        "google.com",
        "bing.com",
        "duckduckgo.com",
        "amazonaws.com",
        "cloudfront.net",
        "fastly.net",
        "cloudflare.com",
    }
)


# ---------------------------------------------------------------------------
# Core extractor
# ---------------------------------------------------------------------------


def extract_pivots(url: str) -> list[Pivot]:
    """
    Given a single URL string, return a list of Pivot objects.

    Rules:
    1. Try every platform pattern in order.  First match wins.
    2. If a username is extracted, emit it — do NOT also emit the domain.
    3. If no username matched AND the host is not a known platform or CDN,
       emit the bare domain as a domain pivot (potential target-owned asset).
    4. Never emit a pivot for a known platform host without a username.
    5. Silently return [] on any parse error.
    """
    try:
        url = url.strip()
        if not url:
            return []
        parsed = urlparse(url if "://" in url else f"https://{url}")
        host = (parsed.netloc or "").lower()
        if host.startswith("www."):
            host = host[4:]
        path_and_query = parsed.path + ("?" + parsed.query if parsed.query else "")
        full = host + path_and_query
    except Exception:
        return []

    # Try platform patterns
    for pattern, platform, val_group, ctx_group in _PLATFORM_PATTERNS:
        m = pattern.search(full)
        if m:
            value = m.group(val_group).lower().strip("/")
            if not value or value in ("undefined", "null"):
                continue
            context: str | None = None
            if ctx_group:
                try:
                    ctx_raw = m.group(ctx_group)
                    if ctx_raw:
                        context = ctx_raw.strip("/").replace("-", " ")
                except IndexError:
                    pass
            return [
                Pivot(
                    pivot_type="username",
                    value=value,
                    platform=platform,
                    context=context,
                )
            ]

    # No username extracted — emit domain only if it looks target-owned
    if host and host not in _PLATFORM_HOSTS:
        # Strip port if present
        domain = host.split(":")[0]
        return [Pivot(pivot_type="domain", value=domain, platform="web", context=None)]

    return []


def extract_pivots_from_results(results: list[dict]) -> list[Pivot]:
    """
    Run extract_pivots over every URL in a list of search result dicts.
    Deduplicates by (pivot_type, value) — first occurrence wins.
    """
    seen: set[tuple[str, str]] = set()
    pivots: list[Pivot] = []
    for r in results:
        url = r.get("url", "")
        for pivot in extract_pivots(url):
            key = (pivot.pivot_type, pivot.value)
            if key not in seen:
                seen.add(key)
                pivots.append(pivot)
    return pivots


# ---------------------------------------------------------------------------
# Formatter
# ---------------------------------------------------------------------------


def format_pivots(pivots: list[Pivot]) -> str:
    """
    Render extracted pivots as a structured block appended to tool output.
    Returns an empty string if there are no pivots.
    """
    if not pivots:
        return ""

    usernames = [p for p in pivots if p.pivot_type == "username"]
    domains = [p for p in pivots if p.pivot_type == "domain"]

    lines = ["\n── Extracted Pivots ──────────────────────────────────────────"]

    if usernames:
        lines.append("Usernames/handles (investigate these first):")
        for p in usernames:
            ctx = f"  [context: {p.context}]" if p.context else ""
            lines.append(
                f"  PIVOT: username → {p.value}  (platform: {p.platform}){ctx}"
            )

    if domains:
        lines.append("Potential target-owned domains:")
        for p in domains:
            lines.append(f"  PIVOT: domain → {p.value}")

    lines.append("──────────────────────────────────────────────────────────────")
    return "\n".join(lines)