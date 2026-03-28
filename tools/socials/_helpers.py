"""
tools/social/_helpers.py  –  Shared constants and utility functions.
"""

import datetime
import re

# ── Constants ──────────────────────────────────────────────────────────────

_BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


# ── Helpers ────────────────────────────────────────────────────────────────


def _slug(path: str) -> str:
    """First non-empty path segment, leading @ stripped."""
    parts = [p for p in path.strip("/").split("/") if p]
    return parts[0].lstrip("@") if parts else ""


def _ts(epoch: int | float) -> str:
    """Unix timestamp → YYYY-MM-DD, or 'N/A'."""
    if not epoch:
        return "N/A"
    return datetime.datetime.fromtimestamp(epoch).strftime("%Y-%m-%d")


def _clean_html(text: str, maxlen: int = 300) -> str:
    """Strip HTML tags and truncate."""
    return re.sub(r"<[^>]+>", " ", text or "").strip()[:maxlen]
