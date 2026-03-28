"""
tools/scraper_utils.py

Shared helpers for web scraping and contact extraction (scraper.py).
"""

from __future__ import annotations

import re
from urllib.parse import urljoin, urlparse
import io
import zipfile
import phonenumbers

# ── Payload helpers (PDF / DOCX / HTML sniffing & extraction) ────────────────


def looks_like_html_bytes(data: bytes) -> bool:
    """Quick heuristic to tell if bytes likely contain HTML."""
    if not data:
        return False
    sample = data[:2048].lower()
    return b"<html" in sample or b"<!doctype" in sample or b"<body" in sample


async def fetch_via_browser(
    url: str, *, return_bytes: bool = False
) -> tuple[str | bytes, str]:
    """Fetch a URL via the shared Playwright browser session.

    Returns (html_or_bytes, final_url). Raises RuntimeError if browser not running.
    """
    try:
        from agent_runtime import browser as _browser
    except Exception:
        raise RuntimeError("Browser module not available")

    if not _browser.session_ok():
        raise RuntimeError(
            "Browser session is not available for JS fallback. "
            "Ensure agent_runtime.browser.start() was called at scan start."
        )
    return await _browser.fetch_page(
        url, wait_until="networkidle", timeout=30_000, return_bytes=return_bytes
    )


async def fetch_smart(
    url: str,
    *,
    return_bytes: bool = False,
    user_agent: str | None = None,
    js_threshold: int = 200,
) -> tuple[str | bytes, str, str]:
    """Smart fetch that prefers http_client and falls back to browser for JS-rendered pages.

    Returns (payload, final_url, method) where payload is str (when return_bytes=False)
    or bytes (when return_bytes=True). Method is 'httpx' or 'browser'.
    """
    try:
        from shared import http_client as _http
        from agent_runtime import browser as _browser
    except Exception:
        raise RuntimeError("Required modules for fetching are not available")

    ua = user_agent or "Mozilla/5.0 (compatible; OSINT-MCP/1.0)"

    # Try HEAD to get content-type
    try:
        headers = await _http.head(url)
    except Exception:
        headers = {}

    ct = (headers.get("content-type") or "").lower()

    try:
        if return_bytes:
            data = await _http.get_bytes(url, headers={"user-agent": ua})
            final_url = url
            try:
                _, final_url = await _http.get_text_with_url(url, user_agent=ua)
            except Exception:
                pass

            # If bytes look like HTML, consider browser fallback
            if looks_like_html_bytes(data):
                try:
                    # quick decode to estimate visible text
                    try:
                        html = data.decode("utf-8", errors="replace")
                    except Exception:
                        html = data.decode("latin-1", errors="replace")
                    visible = to_text(html).strip()
                    if len(visible) < js_threshold and _browser.session_ok():
                        try:
                            payload, final_url = await fetch_via_browser(
                                url, return_bytes=True
                            )
                            return payload, final_url, "browser"
                        except Exception:
                            pass
                except Exception:
                    pass
            return data, final_url, "httpx"
        else:
            text, final_url = await _http.get_text_with_url(url, user_agent=ua)
            visible = to_text(text).strip()
            if len(visible) < js_threshold and _browser.session_ok():
                try:
                    html, final_url = await fetch_via_browser(url, return_bytes=False)
                    return html, final_url, "browser"
                except Exception:
                    pass
            return text, final_url, "httpx"
    except Exception:
        if _browser.session_ok():
            try:
                payload, final_url = await fetch_via_browser(
                    url, return_bytes=return_bytes
                )
                return payload, final_url, "browser"
            except Exception:
                pass
        raise


# ── Regex patterns ─────────────────────────────────────────────────────────────

_EMAIL_RE = re.compile(
    r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}",
    re.IGNORECASE,
)


_SOCIAL_PATTERNS: dict[str, re.Pattern] = {
    "twitter/x": re.compile(
        r"https?://(?:www\.)?(?:twitter|x)\.com/([A-Za-z0-9_]{1,50})", re.IGNORECASE
    ),
    "linkedin": re.compile(
        r"https?://(?:www\.)?linkedin\.com/(?:in|company)/([A-Za-z0-9_\-]+)",
        re.IGNORECASE,
    ),
    "facebook": re.compile(
        r"https?://(?:www\.)?facebook\.com/([A-Za-z0-9_.]+)", re.IGNORECASE
    ),
    "instagram": re.compile(
        r"https?://(?:www\.)?instagram\.com/([A-Za-z0-9_.]+)", re.IGNORECASE
    ),
    "youtube": re.compile(
        r"https?://(?:www\.)?youtube\.com/(?:c/|channel/|@)([A-Za-z0-9_\-]+)",
        re.IGNORECASE,
    ),
    "github": re.compile(
        r"https?://(?:www\.)?github\.com/([A-Za-z0-9_\-]+)", re.IGNORECASE
    ),
    "tiktok": re.compile(
        r"https?://(?:www\.)?tiktok\.com/@([A-Za-z0-9_.]+)", re.IGNORECASE
    ),
    "telegram": re.compile(
        r"https?://(?:t\.me|telegram\.me)/([A-Za-z0-9_]+)", re.IGNORECASE
    ),
    "xing": re.compile(
        r"https?://(?:www\.)?xing\.com/profile/([A-Za-z0-9_]+)", re.IGNORECASE
    ),
    "mastodon": re.compile(r"https?://[a-z0-9.\-]+/@([A-Za-z0-9_]+)", re.IGNORECASE),
}

# Generic slugs that appear in social share buttons / nav — not real profiles
_SOCIAL_NOISE = {
    "share",
    "sharer",
    "intent",
    "home",
    "about",
    "login",
    "signup",
    "search",
    "explore",
    "watch",
    "feed",
    "ads",
}

_EMAIL_IGNORE = {
    "example.com",
    "sentry.io",
    "domain.com",
    "yourdomain.com",
    "email.com",
    "test.com",
    "placeholder.com",
    "wixpress.com",
}


# ── URL / fetch helpers ────────────────────────────────────────────────────────


def normalize_url(url: str) -> str:
    url = url.strip()
    if not url.startswith("http"):
        url = f"https://{url}"
    return url


async def fetch_html(url: str) -> tuple[str, str]:
    """Fetch a URL and return (html, final_url)."""
    import httpx

    async with httpx.AsyncClient(
        follow_redirects=True,
        timeout=20,
        headers={"User-Agent": "Mozilla/5.0 (compatible; OSINT-MCP/1.0)"},
    ) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        return resp.text, str(resp.url)


def is_html_string(text: str) -> bool:
    if not text:
        return False

    sample = text[:2048].lower()
    # Check for actual tag starts, not just substrings (prevents matching ".html" in URLs)
    if any(tag in sample for tag in ["<html", "<!doctype", "<body", "<head"]):
        # Verify it's not just a URL or a mention by checking for a closing bracket or space
        if re.search(r"<(html|!doctype|body|head)[>\s]", sample):
            return True

    # Structural tags check
    if re.search(r"<\s*(div|p|span|a|br|ul|li|table|h[1-6])[^>]*>", sample):
        return True
    return False


def to_text(content: str) -> str:
    if not content:
        return ""

    # If it's definitely not HTML, return it as-is to preserve formatting (.txt, .md)
    if not is_html_string(content):
        return content.strip()

    try:
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(content, "html.parser")

        # SPECIAL CASE: Browser-wrapped .txt files usually put text inside a <pre> tag.
        # If the body contains ONLY a <pre> tag, we treat it as plain text.
        pre_tag = soup.find("pre")
        if pre_tag and len(soup.find_all(True)) <= 5:  # Minimal tags suggest a wrapper
            return pre_tag.get_text().strip()

        # Normal HTML cleanup
        for tag in soup(
            ["script", "style", "noscript", "meta", "head", "title", "link"]
        ):
            tag.decompose()

        # Use \n as separator to preserve structure, then clean up horizontal spacing
        # This keeps lists and paragraphs readable
        text = soup.get_text(separator="\n", strip=True)

        # Collapse multiple horizontal spaces, but keep single newlines
        lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.splitlines()]
        return "\n".join(line for line in lines if line).strip()

    except ImportError:
        # Fallback regex logic
        content = re.sub(
            r"<(script|style|head)[^>]*>.*?</\1>",
            " ",
            content,
            flags=re.IGNORECASE | re.DOTALL,
        )
        content = re.sub(r"<[^>]+>", "\n", content)  # Replace tags with newlines
        lines = [re.sub(r"[ \t]+", " ", line).strip() for line in content.splitlines()]
        return "\n".join(line for line in lines if line).strip()


def payload_to_text(payload: bytes | str, *, strict: bool = False) -> tuple[str, str]:
    if isinstance(payload, str):
        kind = "html" if is_html_string(payload) else "text"
        return to_text(payload), kind

    data: bytes = payload or b""
    if not data:
        return "", "unknown"

    # --- PDF EXTRACTION (Untouched) ---
    if data.startswith(b"%PDF-"):
        try:
            from PyPDF2 import PdfReader

            reader = PdfReader(io.BytesIO(data))
            text = "\n".join(p.extract_text() or "" for p in reader.pages).strip()
            return text, "pdf"
        except Exception:
            return "", "pdf"

    # --- DOCX EXTRACTION (Untouched) ---
    if data.startswith(b"PK"):
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                if "word/document.xml" in z.namelist():
                    xml = z.read("word/document.xml").decode("utf-8", errors="ignore")
                    text = re.sub(r"<[^>]+>", " ", xml)
                    return text.strip(), "docx"
        except Exception:
            pass

    # --- TEXT / HTML FALLBACK ---
    try:
        text = data.decode("utf-8")
    except Exception:
        text = data.decode("latin-1", errors="replace")

    kind = "html" if is_html_string(text) else "text"
    return (
        to_text(text),
        kind,
    )  # ── Extraction helpers ─────────────────────────────────────────────────────────


def extract_emails(text: str) -> list[str]:
    found = set(_EMAIL_RE.findall(text))
    return sorted(
        e.lower()
        for e in found
        if e.split("@")[-1].lower() not in _EMAIL_IGNORE
        and not e.endswith((".png", ".jpg", ".gif", ".svg", ".css", ".js"))
    )


CANDIDATE_REGEX = re.compile(r"\+[\d \-().]{7,20}")


def extract_phones(text: str) -> list[str]:
    results = []

    for match in CANDIDATE_REGEX.finditer(text):
        raw = match.group(0)
        try:
            parsed = phonenumbers.parse(raw, None)
            print(parsed)
            if phonenumbers.is_valid_number(parsed):
                formatted = phonenumbers.format_number(
                    parsed, phonenumbers.PhoneNumberFormat.E164
                )
                results.append(formatted)

        except phonenumbers.NumberParseException:
            continue

    return sorted(set(results))


def extract_socials(html: str) -> dict[str, list[str]]:
    results: dict[str, list[str]] = {}
    for platform, pattern in _SOCIAL_PATTERNS.items():
        matches = {m for m in pattern.findall(html) if m.lower() not in _SOCIAL_NOISE}
        if matches:
            results[platform] = sorted(matches)
    return results


def find_contact_links(html: str, base_url: str, base_domain: str) -> list[str]:
    """Find internal contact/about/imprint pages — max 5."""
    link_re = re.compile(r'href=["\']([\'"]+)["\']', re.IGNORECASE)
    found: set[str] = set()
    for link in re.findall(r'href=["\']([^"\']+)["\']', html):
        full = urljoin(base_url, link).split("?")[0].split("#")[0]
        parsed = urlparse(full)
        if parsed.netloc == base_domain and parsed.scheme in ("http", "https"):
            if any(
                k in full.lower()
                for k in (
                    "contact",
                    "about",
                    "imprint",
                    "impressum",
                    "team",
                    "people",
                    "kontakt",
                )
            ):
                found.add(full)
    return list(found)[:5]


def find_all_links(html: str, base_url: str, base_domain: str) -> list[str]:
    """Extract all unique internal links from a page."""
    found: set[str] = set()
    for link in re.findall(r'href=["\']([^"\']+)["\']', html):
        full = urljoin(base_url, link).split("?")[0].split("#")[0]
        parsed = urlparse(full)
        if parsed.netloc == base_domain and parsed.scheme in ("http", "https"):
            found.add(full)
    return sorted(found)
