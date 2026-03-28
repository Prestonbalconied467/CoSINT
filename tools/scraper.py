"""
tools/scraper.py  –  Web Scraping & Contact Extraction
Tools: osint_scraper_extract, osint_scraper_fetch

Thin registration layer. All extraction helpers (regex patterns, HTML
parsing, link finding) live in tools/helper/scraper_utils.py.
Fetch helpers (httpx, browser fallback) are defined locally here because
they depend on the agent_runtime browser session.
"""

from typing import Annotated
from urllib.parse import urlparse

from mcp.server.fastmcp import FastMCP
from pydantic import Field

from shared.rate_limiter import rate_limit
from tools.helper.scraper_utils import (
    extract_emails,
    extract_phones,
    extract_socials,
    find_all_links,
    find_contact_links,
    normalize_url,
    payload_to_text,
    fetch_smart,
    fetch_via_browser,
)


# Maximum characters to return when extracting large documents
_MAX_OUTPUT_CHARS = 30000

# A page is considered JS-rendered (and worth re-fetching via browser) when
# visible text is shorter than this after stripping tags.
_JS_THRESHOLD = 200  # characters

# Browsers present a generic Mozilla UA; sites often block or thin-serve
# non-browser UAs, so the scraper uses one too. API modules keep the default
# osint-mcp UA so integrations can identify the caller.
_SCRAPER_UA = "Mozilla/5.0 (compatible; OSINT-MCP/1.0)"


# ── Fetch helpers ─────────────────────────────────────────────────────────────


async def _fetch_browser(
    url: str, return_bytes: bool = False
) -> tuple[str | bytes, str]:
    """Thin wrapper over helper.fetch_via_browser to keep scraper.py minimal."""
    return await fetch_via_browser(url, return_bytes=return_bytes)


async def _fetch(
    url: str, *, return_bytes: bool = False
) -> tuple[str | bytes, str, str]:
    """
    Smart fetch: attempt to fetch bytes (or text) via http_client; fall back to
    browser when JS rendering is suspected. Returns (payload, final_url, method)
    where payload is bytes when return_bytes=True else str, and method is
    'httpx' or 'browser'.
    """
    # Delegate to helper in scraper_utils for the real logic
    return await fetch_smart(
        url,
        return_bytes=return_bytes,
        user_agent=_SCRAPER_UA,
        js_threshold=_JS_THRESHOLD,
    )


# ── Tool registration ─────────────────────────────────────────────────────────


def register(mcp: FastMCP) -> None:

    @mcp.tool(annotations={"readOnlyHint": True})
    async def osint_scraper_extract(
        url: Annotated[
            str, Field(description="URL or domain to scrape, e.g. 'example.com'")
        ],
        crawl_depth: Annotated[
            int,
            Field(
                description="0 = single page only, 1 = also follow internal contact/about/imprint pages (max 5)",
                ge=0,
                le=5,
            ),
        ] = 0,
    ) -> str:
        """Extract emails, phone numbers, social media handles, and internal links from a website.

        Returns: emails, phones, social_links, internal_links, and page_title.
        Page priority — hit these before broader crawling:
          /impressum, /legal-notice → legally required in EU; names the operator (T1 source)
          /contact → emails, phones, sometimes physical address
          /about, /team → real names, roles, sometimes direct emails
          /privacy, /privacy-policy → names the data controller (GDPR-required operator ID)
        Use crawl_depth=1 to automatically follow internal contact/about/imprint pages.
        If no contact data found: use osint_scraper_fetch on the same URL to read raw
          text and extract manually (obfuscated addresses, JS-rendered content).
        Uses httpx first; falls back to Playwright browser for JS-rendered pages automatically.
        """
        url = normalize_url(url)
        base_domain = urlparse(url).netloc

        urls_to_scrape = [url]
        if crawl_depth == 1:
            try:
                await rate_limit("default")
                root_payload, _, _ = await _fetch(url)
                # Ensure we have a string HTML for link finding
                if isinstance(root_payload, bytes):
                    try:
                        root_html = root_payload.decode("utf-8")
                    except Exception:
                        root_html = root_payload.decode("latin-1", errors="replace")
                else:
                    root_html = root_payload
                urls_to_scrape += find_contact_links(root_html, url, base_domain)
            except Exception:
                pass

        all_emails: set[str] = set()
        all_phones: set[str] = set()
        all_socials: dict[str, set[str]] = {}
        found_links: set[str] = set()
        scraped: list[str] = []
        methods: list[str] = []

        for target in urls_to_scrape:
            try:
                await rate_limit("default")
                payload, final_url, method = await _fetch(target)

                text, kind = payload_to_text(payload, strict=True)

                # Truncate overly large text
                if text and len(text) > _MAX_OUTPUT_CHARS:
                    text = text[:_MAX_OUTPUT_CHARS] + "\n... (truncated)"

                all_emails.update(extract_emails(text))
                all_phones.update(extract_phones(text))

                # Only run HTML-specific extraction on HTML-like content
                if kind == "html":
                    html = text
                    for platform, handles in extract_socials(html).items():
                        all_socials.setdefault(platform, set()).update(handles)
                    found_links.update(find_all_links(html, final_url, base_domain))

                scraped.append(final_url)
                methods.append(method)
            except Exception:
                continue

        if not scraped:
            return f"Failed to fetch {url}."

        used_browser = "browser" in methods
        lines = [f"Website scrape: {url}"]
        if len(scraped) > 1:
            lines.append(f"Pages scraped:  {len(scraped)}")
        if used_browser:
            lines.append("Fetch method:   browser (JS fallback used for ≥1 page)")

        lines.append(f"\n── Emails ({len(all_emails)}) ──")
        lines += [f"  {e}" for e in sorted(all_emails)] or ["  None found."]

        lines.append(f"\n── Phone Numbers ({len(all_phones)}) ──")
        lines += [f"  {p}" for p in sorted(all_phones)] or ["  None found."]

        lines.append(
            f"\n── Social Media ({sum(len(v) for v in all_socials.values())}) ──"
        )
        if all_socials:
            for platform, handles in sorted(all_socials.items()):
                for handle in sorted(handles):
                    lines.append(f"  {platform.capitalize()}: @{handle}")
        else:
            lines.append("  None found.")

        if found_links:
            lines.append(f"\n── Internal Links ({len(found_links)}) ──")
            for link in sorted(found_links):
                lines.append(f"  {link}")

        if len(scraped) > 1:
            lines.append("\n── Pages Scraped ──")
            lines += [f"  {u}" for u in scraped]

        return "\n".join(lines)

    @mcp.tool(annotations={"readOnlyHint": True})
    async def osint_scraper_fetch(
        url: Annotated[
            str,
            Field(
                description="URL to fetch and return as raw text for manual analysis"
            ),
        ],
        force_browser: Annotated[
            bool,
            Field(
                description=(
                    "Skip httpx and go straight to the Playwright browser. "
                    "Use when you know the page is JS-rendered."
                ),
            ),
        ] = False,
    ) -> str:
        """Fetch a page and return its full visible text for manual analysis.

        Returns: full visible text, page title, and final URL after redirects.
        Use beyond just scraper fallback — fetch these paths proactively on any target domain:
          /robots.txt → disallowed paths are often the most interesting ones to investigate
          /sitemap.xml → full URL inventory; reveals content structure and hidden sections
          /.well-known/security.txt → security contact; often a real email address
          /ads.txt → publisher ID with ad networks; confirms site identity
          /humans.txt → informal credits; sometimes lists developer names and emails
          JS bundle files → scan for hardcoded API keys, internal endpoints, analytics IDs
        Also use for: Wayback Machine snapshot URLs, JS-rendered pages, and any URL where
          osint_scraper_extract missed data due to obfuscation.
        Tries httpx first; upgrades to Playwright automatically if needed.
        """
        url = normalize_url(url)
        try:
            await rate_limit("default")
            if force_browser:
                payload, final_url = await _fetch_browser(url, return_bytes=True)
                method = "browser"
            else:
                payload, final_url, method = await _fetch(url, return_bytes=True)

            text, kind = payload_to_text(payload, strict=True)
            lines = text.splitlines()
            cleaned_text = "\n".join(
                " ".join(line.split())  # normalize spaces inside line
                for line in lines
                if line.strip()  # ❗ remove empty/whitespace-only lines
            )
            if cleaned_text and len(cleaned_text) > _MAX_OUTPUT_CHARS:
                cleaned_text = cleaned_text[:_MAX_OUTPUT_CHARS] + "\n... (truncated)"

            if not text:
                return f"Page returned no readable text content: {final_url}"

            header = f"Page content: {final_url}  [fetched via {method}]\n"
            return header + f"\n{cleaned_text}"
        except Exception as e:
            return f"Failed to fetch {url}: {e}"
