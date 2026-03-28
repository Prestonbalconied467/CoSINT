"""
tools/media_utils.py

Shared helpers for media tools (media.py).
Owns: JS extractors for reverse image search engines, browser-based
      reverse image search runner.
"""

from __future__ import annotations

from urllib.parse import quote_plus

from agent_runtime import browser

# ── JS extractors ─────────────────────────────────────────────────────────────

YANDEX_JS = """() => {
    const clean = t => (t || "").trim().replace(/\\s+/g, " ");
    const results = [];

    document.querySelectorAll(".CbirSites-Item").forEach(el => {
        const title = clean(el.querySelector(".CbirSites-ItemTitle")?.innerText);
        const url   = el.querySelector("a.CbirSites-ItemTitle")?.href || "";
        const desc  = clean(el.querySelector(".CbirSites-ItemDescription")?.innerText);
        if (url) results.push({ title, url, snippet: desc });
    });

    const entity = clean(
        document.querySelector(".CbirObjectResponse-Title, .Tags-Item")?.innerText
    );

    return { entity, results: results.slice(0, 10) };
}"""

GOOGLE_IMAGES_JS = """() => {
    const clean = t => (t || "").trim().replace(/\\s+/g, " ");
    const results = [];

    document.querySelectorAll("div.g, .IsZvec").forEach(el => {
        const a       = el.querySelector("a[href]");
        const title   = clean(el.querySelector("h3")?.innerText);
        const snippet = clean(el.querySelector(".VwiC3b, .st")?.innerText);
        const url     = a?.href || "";
        if (title && url && !url.startsWith("https://www.google.com")) {
            results.push({ title, url, snippet });
        }
    });

    const bestGuess = clean(
        document.querySelector(".r12Koc, [data-q]")?.innerText
    );

    return { bestGuess, results: results.slice(0, 10) };
}"""

TINEYE_FREE_JS = """() => {
    const clean = t => (t || "").trim().replace(/\\s+/g, " ");
    const count = clean(document.querySelector(".num-matches, #result-count")?.innerText);
    const results = [];
    document.querySelectorAll(".match").forEach(el => {
        const domain   = clean(el.querySelector(".match-domain a")?.innerText);
        const imageUrl = el.querySelector(".match-image a")?.href || "";
        const crawled  = clean(el.querySelector(".crawl-date")?.innerText);
        if (domain) results.push({ domain, imageUrl, crawled });
    });
    return { count, results: results.slice(0, 8) };
}"""

# ── Browser-based reverse image search ───────────────────────────────────────


async def browser_reverse_image_search(
    image_url: str, interactive: bool = False
) -> str:
    """
    Reverse image search via Yandex Images, Google Images, and TinEye free
    web UI using the shared Playwright session.

    interactive=False (default, autonomous scan):
        Short timeouts, gives up immediately if a CAPTCHA appears — no user
        present to solve it.

    interactive=True (user is present):
        Detects CAPTCHAs and relaunches the browser in visible mode so the
        user can solve them. Waits up to 90 seconds per engine.

    Stops after Yandex if it returns actual result URLs. Falls through to
    Google Images and TinEye only if Yandex returns no links.

    Returns a formatted string ready to be appended to the tool output.
    """
    if not browser.session_ok():
        return (
            "\n── Browser fallback ──\n"
            "Browser session not available. Call browser.start() at scan start,\n"
            "or set at least one API key (GOOGLE_VISION_KEY / TINEYE_KEY / SAUCENAO_KEY)."
        )

    enc = quote_plus(image_url)
    lines = ["\n── Browser fallback (no API keys configured) ──"]

    # ── Yandex Images ─────────────────────────────────────────────────────────
    yandex_has_results = False
    try:
        async with browser.open_page() as page:
            await page.goto(
                f"https://yandex.com/images/search?rpt=imageview&url={enc}",
                wait_until="domcontentloaded",
                timeout=20_000,
            )
            await browser.smart_wait(
                page,
                result_selector=".CbirSites-Item, .CbirObjectResponse-Title, .Tags-Item",
                captcha_selector="form[action*='captcha'], .CheckboxCaptcha, #js-captcha",
                interactive=interactive,
            )
            data = await page.evaluate(YANDEX_JS)

        lines.append("\n  Yandex Images:")
        if data.get("entity"):
            lines.append(f"  Detected subject: {data['entity']}")
        results = data.get("results", [])
        if results:
            yandex_has_results = True
            for r in results:
                lines.append(f"  • {r.get('title', 'N/A')}")
                lines.append(f"    {r.get('url', '')}")
                if r.get("snippet"):
                    lines.append(f"  ↳ {r['snippet'][:120]}")
        else:
            lines.append("  No result links found.")
    except Exception as e:
        if "closed" in str(e).lower():
            lines.append("  Yandex: browser was closed.")
            return "\n".join(lines)
        lines.append(f"  Yandex Images error: {e}")

    # Stop here if Yandex already found real matches
    if yandex_has_results:
        lines.append("\n  Yandex returned results — skipping Google Images and TinEye.")
        return "\n".join(lines)

    # ── Google Images ─────────────────────────────────────────────────────────
    try:
        async with browser.open_page() as page:
            await page.goto(
                f"https://www.google.com/searchbyimage?image_url={enc}&safe=off",
                wait_until="domcontentloaded",
                timeout=20_000,
            )
            await browser.smart_wait(
                page,
                result_selector="#search, #rso, .r12Koc",
                captcha_selector="iframe[src*='recaptcha'], #captcha-form",
                interactive=interactive,
            )
            data = await page.evaluate(GOOGLE_IMAGES_JS)

        lines.append("\n  Google Images:")
        if data.get("bestGuess"):
            lines.append(f"  Best guess: {data['bestGuess']}")
        results = data.get("results", [])
        if results:
            for r in results:
                lines.append(f"  • {r.get('title', 'N/A')}")
                lines.append(f"    {r.get('url', '')}")
                if r.get("snippet"):
                    lines.append(f"  ↳ {r['snippet'][:120]}")
        else:
            lines.append("  No results found.")
    except Exception as e:
        if "closed" in str(e).lower():
            lines.append("  Google Images: browser was closed.")
            return "\n".join(lines)
        lines.append(f"  Google Images error: {e}")

    # ── TinEye free ───────────────────────────────────────────────────────────
    try:
        async with browser.open_page() as page:
            await page.goto(
                f"https://tineye.com/search?url={enc}",
                wait_until="domcontentloaded",
                timeout=20_000,
            )
            await browser.smart_wait(
                page,
                result_selector=".match, .num-matches, #result-count",
                captcha_selector=".g-recaptcha, #captcha",
                interactive=interactive,
            )
            data = await page.evaluate(TINEYE_FREE_JS)

        lines.append("\n  TinEye (free):")
        if data.get("count"):
            lines.append(f"  {data['count']}")
        results = data.get("results", [])
        if results:
            for r in results:
                lines.append(
                    f"  • {r.get('domain', 'N/A')}  (crawled: {r.get('crawled', 'N/A')})"
                )
                if r.get("imageUrl"):
                    lines.append(f"    {r['imageUrl']}")
        else:
            lines.append("  No results found.")
    except Exception as e:
        if "closed" in str(e).lower():
            lines.append("  TinEye: browser was closed.")
            return "\n".join(lines)
        lines.append(f"  TinEye free error: {e}")

    return "\n".join(lines)