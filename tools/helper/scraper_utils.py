"""
tools/scraper_utils.py

Shared helpers for web scraping and contact extraction (scraper.py).
"""

from __future__ import annotations

import re
import io
from urllib.parse import urljoin, urlparse
import zipfile

import phonenumbers

DEFAULT_USER_AGENT = "Mozilla/5.0 (compatible; OSINT-MCP/1.0)"
HTML_SNIFF_BYTES = 2048
INTERNAL_LINK_SCHEMES = ("http", "https")
CONTACT_LINK_KEYWORDS = (
    "contact",
    "about",
    "imprint",
    "impressum",
    "team",
    "people",
    "kontakt",
)
HREF_RE = re.compile(r'href=["\']([^"\']+)["\']', re.IGNORECASE)
HTML_HINT_TAG_RE = re.compile(r"<(html|!doctype|body|head)[>\s]", re.IGNORECASE)
HTML_STRUCTURE_RE = re.compile(
    r"<\s*(div|p|span|a|br|ul|li|table|h[1-6])[^>]*>", re.IGNORECASE
)
HTML_MARKERS = ("<html", "<!doctype", "<body")
TEXT_FALLBACK_ENCODINGS = ("utf-8", "latin-1")
MINIMAL_PRE_WRAPPER_TAG_COUNT = 5
MAX_CONTACT_LINKS = 5
MIME_KIND_HTML = "html"
MIME_KIND_TEXT = "text"
MIME_KIND_UNKNOWN = "unknown"
MIME_KIND_PDF = "pdf"
MIME_KIND_DOCX = "docx"
FETCH_METHOD_HTTPX = "httpx"
FETCH_METHOD_BROWSER = "browser"

# ── Payload helpers (PDF / DOCX / HTML sniffing & extraction) ────────────────


def looks_like_html_bytes(data: bytes) -> bool:
    """Quick heuristic to tell if bytes likely contain HTML."""
    if not data:
        return False
    sample = data[:HTML_SNIFF_BYTES].lower()
    return any(marker.encode() in sample for marker in HTML_MARKERS)


def _decode_with_fallback(data: bytes, *, errors: str = "replace") -> str:
    for encoding in TEXT_FALLBACK_ENCODINGS:
        try:
            return data.decode(encoding, errors=errors)
        except Exception:
            continue
    return data.decode("latin-1", errors="replace")


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

    ua = user_agent or DEFAULT_USER_AGENT

    def should_use_browser(visible_text: str) -> bool:
        return len(visible_text) < js_threshold and _browser.session_ok()

    async def fetch_bytes_http() -> tuple[bytes, str]:
        payload = await _http.get_bytes(url, headers={"user-agent": ua})
        resolved_url = url
        try:
            _, resolved_url = await _http.get_text_with_url(url, user_agent=ua)
        except Exception:
            pass
        return payload, resolved_url

    try:
        if return_bytes:
            data, final_url = await fetch_bytes_http()
            if looks_like_html_bytes(data):
                visible_text = to_text(_decode_with_fallback(data)).strip()
                if should_use_browser(visible_text):
                    try:
                        browser_payload, browser_url = await fetch_via_browser(
                            url, return_bytes=True
                        )
                        return browser_payload, browser_url, FETCH_METHOD_BROWSER
                    except Exception:
                        pass
            return data, final_url, FETCH_METHOD_HTTPX

        text, final_url = await _http.get_text_with_url(url, user_agent=ua)
        if should_use_browser(to_text(text).strip()):
            try:
                browser_html, browser_url = await fetch_via_browser(url, return_bytes=False)
                return browser_html, browser_url, FETCH_METHOD_BROWSER
            except Exception:
                pass
        return text, final_url, FETCH_METHOD_HTTPX
    except Exception:
        if _browser.session_ok():
            try:
                payload, final_url = await fetch_via_browser(
                    url, return_bytes=return_bytes
                )
                return payload, final_url, FETCH_METHOD_BROWSER
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
        headers={"User-Agent": DEFAULT_USER_AGENT},
    ) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        return resp.text, str(resp.url)


def is_html_string(text: str) -> bool:
    if not text:
        return False

    sample = text[:HTML_SNIFF_BYTES].lower()
    has_marker = any(marker in sample for marker in ("<html", "<!doctype", "<body", "<head"))
    if has_marker and HTML_HINT_TAG_RE.search(sample):
        return True
    return bool(HTML_STRUCTURE_RE.search(sample))


def _collapse_horizontal_spacing(text: str) -> str:
    normalized_lines = [
        re.sub(r"[ \t]+", " ", line).strip() for line in text.splitlines()
    ]
    return "\n".join(line for line in normalized_lines if line).strip()


def _to_text_with_bs4(content: str) -> str:
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(content, "html.parser")
    pre_tag = soup.find("pre")
    if pre_tag and len(soup.find_all(True)) <= MINIMAL_PRE_WRAPPER_TAG_COUNT:
        return pre_tag.get_text().strip()

    for removable_tag in soup(
        ["script", "style", "noscript", "meta", "head", "title", "link"]
    ):
        removable_tag.decompose()

    text = soup.get_text(separator="\n")
    return _collapse_horizontal_spacing(text)


def _to_text_with_regex(content: str) -> str:
    cleaned = re.sub(
        r"<(script|style|head)[^>]*>.*?</\1>",
        " ",
        content,
        flags=re.IGNORECASE | re.DOTALL,
    )
    cleaned = re.sub(r"<[^>]+>", "\n", cleaned)
    return _collapse_horizontal_spacing(cleaned)


def to_text(content: str) -> str:
    if not content:
        return ""

    # If it's definitely not HTML, return it as-is to preserve formatting (.txt, .md)
    if not is_html_string(content):
        return content.strip()

    try:
        return _to_text_with_bs4(content)
    except ImportError:
        return _to_text_with_regex(content)


def _extract_pdf_text(data: bytes) -> str:
    from PyPDF2 import PdfReader

    reader = PdfReader(io.BytesIO(data))
    return "\n".join(page.extract_text() or "" for page in reader.pages).strip()


def _extract_docx_text(data: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        if "word/document.xml" not in archive.namelist():
            return ""
        xml = archive.read("word/document.xml").decode("utf-8", errors="ignore")
    return re.sub(r"<[^>]+>", " ", xml).strip()


def payload_to_text(payload: bytes | str, *, strict: bool = False) -> tuple[str, str]:
    _ = strict  # preserved for API compatibility

    if isinstance(payload, str):
        kind = MIME_KIND_HTML if is_html_string(payload) else MIME_KIND_TEXT
        return to_text(payload), kind

    data: bytes = payload or b""
    if not data:
        return "", MIME_KIND_UNKNOWN

    # --- PDF EXTRACTION (Untouched) ---
    if data.startswith(b"%PDF-"):
        try:
            return _extract_pdf_text(data), MIME_KIND_PDF
        except Exception:
            return "", MIME_KIND_PDF

    # --- DOCX EXTRACTION (Untouched) ---
    if data.startswith(b"PK"):
        try:
            text = _extract_docx_text(data)
            if text:
                return text, MIME_KIND_DOCX
        except Exception:
            pass

    # --- TEXT / HTML FALLBACK ---
    text = _decode_with_fallback(data, errors="replace")
    kind = MIME_KIND_HTML if is_html_string(text) else MIME_KIND_TEXT
    return to_text(text), kind


# ── Extraction helpers ─────────────────────────────────────────────────────────


def extract_emails(text: str) -> list[str]:
    found = set(_EMAIL_RE.findall(text))
    return sorted(
        e.lower()
        for e in found
        if e.split("@")[-1].lower() not in _EMAIL_IGNORE
        and not e.endswith((".png", ".jpg", ".gif", ".svg", ".css", ".js"))
    )


PHONE_CANDIDATE_RE = re.compile(r"\+[\d \-().]{7,20}")
# Backward-compatible alias for existing imports.
CANDIDATE_REGEX = PHONE_CANDIDATE_RE


def extract_phones(text: str) -> list[str]:
    results = []

    for match in PHONE_CANDIDATE_RE.finditer(text):
        raw = match.group(0)
        try:
            parsed = phonenumbers.parse(raw, None)
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


def _iter_internal_links(html: str, base_url: str, base_domain: str):
    """Yield normalized internal links found in HTML."""
    for raw_link in HREF_RE.findall(html):
        normalized_link = urljoin(base_url, raw_link).split("?")[0].split("#")[0]
        parsed_link = urlparse(normalized_link)
        if (
            parsed_link.netloc == base_domain
            and parsed_link.scheme in INTERNAL_LINK_SCHEMES
        ):
            yield normalized_link


def find_contact_links(html: str, base_url: str, base_domain: str) -> list[str]:
    """Find internal contact/about/imprint pages — max 5."""
    found: set[str] = set()
    for internal_link in _iter_internal_links(html, base_url, base_domain):
        if any(keyword in internal_link.lower() for keyword in CONTACT_LINK_KEYWORDS):
            found.add(internal_link)
    return list(found)[:MAX_CONTACT_LINKS]


def find_all_links(html: str, base_url: str, base_domain: str) -> list[str]:
    """Extract all unique internal links from a page."""
    found = set(_iter_internal_links(html, base_url, base_domain))
    return sorted(found)
