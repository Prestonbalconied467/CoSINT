"""
tools/search_utils.py

Shared helpers for Google search and dork tools (search.py).
Owns: JS extractors, URL builders, engine registry, dork templates,
      result formatter, core browser-based search runner, and BotDetectedError.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from agent_runtime import browser
from shared.rate_limiter import rate_limit
from .pivot_extractor import extract_pivots_from_results, format_pivots


# ── JS extractors ──────────────────────────────────────────────────────────────

GOOGLE_JS = """() => {
    const clean = t => (t || "").trim().replace(/\\s+/g, " ");
    return Array.from(document.querySelectorAll("div.g, div[data-hveid]")).flatMap(g => {
        const h = g.querySelector("h3");
        const a = g.querySelector("a[href]");
        const s = g.querySelector(".VwiC3b, [data-sncf], .lEBKkf");
        if (!h || !a) return [];
        const url = a.href || "";
        if (url.startsWith("https://www.google.com")) return [];
        return [{ title: clean(h.innerText), url, snippet: clean(s?.innerText || "") }];
    }).filter((r, i, arr) => r.title && arr.findIndex(x => x.url === r.url) === i);
}"""

BING_JS = """() => {
    const clean = t => (t || "").trim().replace(/\\s+/g, " ");
    return Array.from(document.querySelectorAll("li.b_algo")).map(el => {
        const h = el.querySelector("h2 a");
        const s = el.querySelector(".b_caption p, .b_algoSlug");
        if (!h) return null;
        return { title: clean(h.innerText), url: h.href, snippet: clean(s?.innerText || "") };
    }).filter(Boolean);
}"""

# DDG lite is near-plaintext — simple table structure, almost never blocks
DDG_JS = """() => {
    const clean = t => (t || "").trim().replace(/\\s+/g, " ");
    return Array.from(document.querySelectorAll("tr")).flatMap(row => {
        const a = row.querySelector("a.result-link");
        const s = row.querySelector(".result-snippet");
        if (!a || !a.href || a.href.includes("duckduckgo.com")) return [];
        return [{ title: clean(a.innerText), url: a.href, snippet: clean(s?.innerText || "") }];
    }).filter((r, i, arr) => r.title && arr.findIndex(x => x.url === r.url) === i);
}"""

BRAVE_JS = """() => {
    const clean = t => (t || "").trim().replace(/\\s+/g, " ");
    return Array.from(document.querySelectorAll("[data-type='web'] .snippet")).map(el => {
        const h = el.querySelector(".snippet-title");
        const a = el.querySelector("a[href]");
        const s = el.querySelector(".snippet-description");
        if (!h || !a) return null;
        return { title: clean(h.innerText), url: a.href, snippet: clean(s?.innerText || "") };
    }).filter(Boolean);
}"""


# ── URL builders ───────────────────────────────────────────────────────────────


def build_google_url(query: str) -> str:
    from urllib.parse import quote_plus

    return f"https://www.google.com/search?q={quote_plus(query)}&hl=en&num=20&safe=off"


def build_bing_url(query: str) -> str:
    from urllib.parse import quote_plus

    return f"https://www.bing.com/search?q={quote_plus(query)}&count=20"


def build_ddg_url(query: str) -> str:
    from urllib.parse import quote_plus

    # lite version — plain HTML table, no JS required, almost never triggers bot detection
    return f"https://lite.duckduckgo.com/lite/?q={quote_plus(query)}"


def build_brave_url(query: str) -> str:
    from urllib.parse import quote_plus

    return f"https://search.brave.com/search?q={quote_plus(query)}&source=web"


# ── Engine registry ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class EngineConfig:
    """All engine-specific constants needed to drive a browser-based search."""

    name: str
    build_url: Callable[[str], str]
    js_extractor: str
    result_selector: str
    captcha_selector: str
    # False for engines that silently ignore site:/filetype: operators.
    # Used to warn the agent when a dork is routed to a weaker engine.
    supports_operators: bool = True


ENGINES: dict[str, EngineConfig] = {
    "google": EngineConfig(
        name="Google",
        build_url=build_google_url,
        js_extractor=GOOGLE_JS,
        result_selector="#search, #rso, #rcnt",
        captcha_selector="iframe[src*='recaptcha'], #captcha-form",
    ),
    "bing": EngineConfig(
        name="Bing",
        build_url=build_bing_url,
        js_extractor=BING_JS,
        result_selector="#b_results",
        captcha_selector="#captcha, [id*='captcha']",
    ),
    "ddg": EngineConfig(
        name="DuckDuckGo",
        build_url=build_ddg_url,
        js_extractor=DDG_JS,
        result_selector="table",  # lite version is a plain <table>
        captcha_selector="",  # DDG lite almost never triggers CAPTCHA
        supports_operators=True,  # Bing-backed — supports site:/filetype:
    ),
    "brave": EngineConfig(
        name="Brave",
        build_url=build_brave_url,
        js_extractor=BRAVE_JS,
        result_selector="[data-type='web']",
        captcha_selector="",
        supports_operators=True,
    ),
}

# Tried in order when the preferred engine raises BotDetectedError.
# DDG lite is last-resort: near-immune to blocking but independent Bing-backed index.
FALLBACK_CHAIN: list[str] = ["google", "bing", "brave", "ddg"]


# ── Selectors (kept as module-level constants for external use if needed) ──────

_GOOGLE_RESULT_SELECTOR = ENGINES["google"].result_selector
_GOOGLE_CAPTCHA_SELECTOR = ENGINES["google"].captcha_selector


# ── Dork templates ─────────────────────────────────────────────────────────────

DORK_TEMPLATES: dict[str, str] = {
    "general": '"{target}"',
    "person": '"{target}" site:linkedin.com OR site:xing.com OR site:github.com OR site:twitter.com OR site:facebook.com',
    "email_exposure": '"{target}" -site:haveibeenpwned.com -site:hunter.io -site:emailrep.io',
    "username": '"{target}" site:reddit.com OR site:twitter.com OR site:github.com OR site:instagram.com OR site:tiktok.com',
    "phone": '"{target}"',
    "domain_mentions": '"{target}" -site:{target}',
    "company": '"{target}" site:northdata.de OR site:handelsregister.de OR site:opencorporates.com OR site:linkedin.com',
    "paste_exposure": '"{target}" site:pastebin.com OR site:paste.ee OR site:ghostbin.com OR site:dpaste.com',
    "document_search": '"{target}" filetype:pdf OR filetype:xlsx OR filetype:docx OR filetype:csv',
    "crypto_mentions": '"{target}" site:etherscan.io OR site:blockchain.com OR site:blockchair.com',
    "news": '"{target}" after:2022-01-01',
    "forum_mentions": '"{target}" site:reddit.com OR site:hackernews.com OR site:stackoverflow.com',
    "image_sources": '"{target}" site:imgur.com OR site:flickr.com OR site:instagram.com',
}

_DORK_DESCRIPTIONS: dict[str, str] = {
    "general": "any value → open web search, no site restrictions",
    "person": "person name → LinkedIn, Xing, GitHub, social profiles",
    "email_exposure": "email address → surface web mentions outside breach sites",
    "username": "username → Reddit, Twitter, GitHub, Instagram, TikTok",
    "phone": "phone number → any mention on the web",
    "domain_mentions": "domain → external mentions (excluding the site itself)",
    "company": "company name → Northdata, Handelsregister, OpenCorporates, LinkedIn",
    "paste_exposure": "any value → paste sites (Pastebin, paste.ee, etc.)",
    "document_search": "any value → leaked PDF/Excel/Word/CSV documents",
    "crypto_mentions": "wallet address → blockchain explorer mentions",
    "news": "any value → recent news articles (post 2022)",
    "forum_mentions": "any value → Reddit, HackerNews, StackOverflow",
    "image_sources": "any value → image hosting sites",
}

DORK_TYPE_DESCRIPTION = "Dork template to apply. Options:\n" + "\n".join(
    f"  {k}: {v}" for k, v in _DORK_DESCRIPTIONS.items()
)

SESSION_BLOCKED_MSG = (
    "Google blocked this request (CAPTCHA / bot detection).\n"
    "The session will be kept alive and reused for all subsequent headless calls."
)

ALL_ENGINES_BLOCKED_MSG = (
    "All search engines are currently blocked (CAPTCHA / bot detection).\n"
    "Run with interactive=True so a CAPTCHA can be solved manually, then retry."
)

INTERACTIVE_CAPTCHA_ATTEMPT_MSG = (
    "\n⚠ All headless engines blocked — opening Google visibly so you can solve the CAPTCHA.\n"
    "You have 90 seconds. The session will be reused for subsequent calls once solved.\n"
)

ALL_ENGINES_BLOCKED_INTERACTIVE_MSG = (
    "All search engines failed even after the interactive CAPTCHA attempt.\n"
    "The CAPTCHA may not have been solved in time, or the session was rejected.\n"
    "Try again or check your network / proxy setup."
)


# ── Helpers ────────────────────────────────────────────────────────────────────


class BotDetectedError(RuntimeError):
    pass


def build_dork(dork_type: str, target: str, extra: str = "") -> str:
    query = DORK_TEMPLATES.get(dork_type, '"{target}"').replace("{target}", target)
    return f"{query} {extra.strip()}".strip() if extra else query


def format_results(header: str, query: str, results: list[dict]) -> str:
    lines = [header, f"Query: {query}\n"]
    for i, r in enumerate(results, 1):
        snippet = r.get("snippet", "")
        lines.append(
            f"[{i}] {r.get('title', 'N/A')}\n"
            f"    URL: {r.get('url', 'N/A')}"
            + (f"\n  {snippet}" if snippet else "")
            + "\n"
        )
    # Append structured pivot block — agent acts on these, not on raw URLs
    pivots = extract_pivots_from_results(results)
    pivot_block = format_pivots(pivots)
    if pivot_block:
        lines.append(pivot_block)
    return "\n".join(lines)


# ── Core search runners ────────────────────────────────────────────────────────


async def _run_search(
    query: str,
    max_results: int,
    interactive: bool,
    cfg: EngineConfig,
) -> list[dict]:
    """Run a search against a single engine. Raises BotDetectedError if blocked."""
    if interactive:
        await browser.restart_interactive()
    elif not browser.session_ok():
        await browser.start(headless=True)

    await rate_limit(f"search_{cfg.name.lower()}")

    async with browser.open_page() as page:
        await page.goto(
            cfg.build_url(query), wait_until="domcontentloaded", timeout=20_000
        )

        if cfg.captcha_selector:
            ready = await browser.smart_wait(
                page,
                result_selector=cfg.result_selector,
                captcha_selector=cfg.captcha_selector,
                interactive=interactive,
            )
            if not ready:
                browser.invalidate_session()
                raise BotDetectedError(f"{cfg.name} bot detection triggered.")
        else:
            # Engines without a CAPTCHA page (e.g. DDG lite) — just wait for results
            await page.wait_for_selector(cfg.result_selector, timeout=10_000)

        results: list[dict] = await page.evaluate(cfg.js_extractor)
        return results[:max_results]


async def engine_search(
    query: str,
    max_results: int,
    interactive: bool,
    engine_key: str = "google",
    fallback: bool = True,
) -> tuple[list[dict], str]:
    """
    Run a search starting from *engine_key*, with a two-phase fallback strategy:

    Phase 1 — Headless chain:
        Always tries the full fallback chain silently (google → bing → ddg),
        starting from engine_key. No user interaction, no visible browser.

    Phase 2 — Interactive CAPTCHA rescue (only if interactive=True):
        If every headless engine is blocked, opens Google visibly and gives
        the user 90 seconds to solve the CAPTCHA. On success the session is
        reused for all subsequent calls. This fires at most once per invocation
        and only as a genuine last resort.

    Args:
        query:       The full search query string.
        max_results: Maximum number of results to return.
        interactive: True when a human is present who can solve CAPTCHAs.
        engine_key:  Preferred engine to try first (must be a key in ENGINES).
        fallback:    If True, walks the full FALLBACK_CHAIN on block.
                     If False, raises BotDetectedError immediately on block.

    Returns:
        A (results, engine_name) tuple — engine_name is the engine that succeeded.

    Raises:
        BotDetectedError: If all engines are blocked and interactive rescue also fails.
        KeyError:         If engine_key is not in ENGINES.
    """
    if engine_key not in ENGINES:
        raise KeyError(f"Unknown engine '{engine_key}'. Valid: {', '.join(ENGINES)}")

    chain = FALLBACK_CHAIN if fallback else [engine_key]

    # Build the ordered list starting from the preferred engine
    if engine_key in chain:
        ordered = chain[chain.index(engine_key) :]
    else:
        ordered = [engine_key]

    # ── Phase 1: silent headless attempts ─────────────────────────────────────
    last_exc: Exception | None = None
    for key in ordered:
        cfg = ENGINES[key]
        try:
            results = await _run_search(query, max_results, interactive=False, cfg=cfg)
            return results, cfg.name
        except BotDetectedError as exc:
            last_exc = exc
            continue

    # ── Phase 2: interactive CAPTCHA rescue on Google ─────────────────────────
    # Only reached when every headless engine failed.
    if not interactive:
        raise BotDetectedError(f"All headless engines blocked. Last error: {last_exc}")

    import sys

    print(INTERACTIVE_CAPTCHA_ATTEMPT_MSG, file=sys.stderr, flush=True)

    google_cfg = ENGINES["google"]
    try:
        results = await _run_search(
            query, max_results, interactive=True, cfg=google_cfg
        )
        return results, f"{google_cfg.name} (interactive)"
    except BotDetectedError as exc:
        raise BotDetectedError(
            f"Interactive Google CAPTCHA rescue also failed: {exc}"
        ) from exc


async def google_search(query: str, max_results: int, interactive: bool) -> list[dict]:
    """
    Backward-compatible shim — wraps engine_search() starting from Google.
    Existing callers that don't need the engine name can keep using this.
    """
    results, _ = await engine_search(
        query, max_results, interactive, engine_key="google"
    )
    return results
