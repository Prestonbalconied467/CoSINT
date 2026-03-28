"""
shared/url_utils.py

Shared URL and domain normalisation helpers.
Import these instead of repeating the same one-liner across tool modules.
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit

_DOMAIN_LABEL_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
_HASHY_BASENAME_RE = re.compile(r"^[a-z0-9]{8,64}$")
_FILELIKE_TLDS = {
    "png",
    "jpg",
    "jpeg",
    "gif",
    "svg",
    "webp",
    "ico",
    "bmp",
    "tiff",
    "avif",
    "js",
    "mjs",
    "css",
    "map",
    "json",
    "xml",
    "txt",
    "csv",
    "log",
    "pdf",
    "zip",
    "gz",
    "tgz",
    "rar",
    "7z",
    "tar",
    "woff",
    "woff2",
    "ttf",
    "otf",
    "eot",
}


def is_likely_domain(domain: str) -> bool:
    """Return True when *domain* looks like a real host/domain value."""
    d = (domain or "").strip().lower().strip(".")
    if not d or " " in d or len(d) > 253:
        return False
    if d.startswith("*"):
        d = d.lstrip("*.")

    labels = d.split(".")
    if len(labels) < 2:
        return False
    if any(not label or len(label) > 63 for label in labels):
        return False
    if not all(_DOMAIN_LABEL_RE.match(label) for label in labels):
        return False

    tld = labels[-1]
    if len(tld) < 2 or not tld.isalpha():
        return False

    # Bare "hash.ext" artifacts from static asset URLs are not pivot domains.
    if (
        len(labels) == 2
        and tld in _FILELIKE_TLDS
        and _HASHY_BASENAME_RE.fullmatch(labels[0])
    ):
        return False
    return True


def _strip_to_host(raw: str) -> str:
    value = (raw or "").strip().lower()
    if not value:
        return ""

    if "://" in value or value.startswith("//"):
        parsed = urlsplit(value if "://" in value else f"https:{value}")
        host = (parsed.hostname or "").strip().lower()
    else:
        host = value.split("/")[0].split("?")[0].split("#")[0].strip().lower()
        if host.count(":") == 1:
            host = host.split(":", 1)[0]

    return host.removeprefix("www.").strip(".")


def extract_domain(raw: str) -> str:
    """Strip scheme, path and whitespace from a URL or bare domain.

    Examples:
        "https://example.com/path" -> "example.com"
        "http://example.com"       -> "example.com"
        "example.com"              -> "example.com"
    """
    host = _strip_to_host(raw)
    return host if is_likely_domain(host) else ""
