"""
agent_runtime/browser.py  –  Shared Playwright browser session

Owns a single Playwright + Chromium instance that lives for the duration of
a scan. Any tool that needs a real browser imports `get_context()` from here
rather than managing its own Playwright lifecycle.
"""

from __future__ import annotations

import asyncio
import atexit
import sys
from contextlib import asynccontextmanager
from typing import AsyncIterator
from shared.config import (
    BROWSER_LOCALE,
    BROWSER_TIMEZONE,
    BROWSER_USER_AGENT,
    BROWSER_CAPTCHA_POLL,
    BROWSER_RESULT_WAIT,
    BROWSER_CAPTCHA_SOLVE,
)

if sys.platform == "win32":
    import asyncio.proactor_events as _pre
    import asyncio.base_subprocess as _bsp

    def _make_safe_del(original):
        def _safe_del(self):
            try:
                original(self)
            except Exception:
                pass

        return _safe_del

    _pre._ProactorBasePipeTransport.__del__ = _make_safe_del(
        _pre._ProactorBasePipeTransport.__del__
    )
    _bsp.BaseSubprocessTransport.__del__ = _make_safe_del(
        _bsp.BaseSubprocessTransport.__del__
    )

# ── State ─────────────────────────────────────────────────────────────────────

_playwright = None
_browser = None
_context = None
_headless = True  # mirrors how the current browser was launched
_valid = False  # True only after start() succeeds
_lock: asyncio.Lock | None = None


def _get_lock() -> asyncio.Lock:
    global _lock
    if _lock is None:
        _lock = asyncio.Lock()
    return _lock


# ── Public state queries ──────────────────────────────────────────────────────


def session_ok() -> bool:
    """True if the browser is running and hasn't been flagged by bot detection."""
    return _valid and _browser is not None and _browser.is_connected()


def invalidate_session() -> None:
    """
    Mark the session as blocked (call when bot detection fires).
    Tools should check session_ok() and surface a message asking the user to
    call restart_interactive().
    """
    global _valid
    _valid = False


# ── Lifecycle ─────────────────────────────────────────────────────────────────


async def start(headless: bool = True) -> None:
    """
    Launch the Playwright browser and create a persistent context.
    Safe to call multiple times — no-ops if already running in the same mode.
    """
    global _playwright, _browser, _context, _headless, _valid

    async with _get_lock():
        already_running = (
            _browser is not None and _browser.is_connected() and _headless == headless
        )
        if already_running:
            return

        # Tear down whatever is left before relaunching
        await _teardown_unlocked()

        from playwright.async_api import async_playwright

        _playwright = await async_playwright().start()
        _browser = await _playwright.chromium.launch(
            headless=headless,
            args=[
                "--no-sandbox",
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
            ],
        )
        _context = await _browser.new_context(
            user_agent=BROWSER_USER_AGENT,
            viewport={"width": 1280, "height": 800},
            locale=BROWSER_LOCALE,  # "en-US",
            timezone_id=BROWSER_TIMEZONE,  # "US/Pacific",
            java_script_enabled=True,
            extra_http_headers={
                "Accept-Language": "en-US,en;q=0.9",
                "Accept": (
                    "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
                ),
            },
        )
        await _context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )
        _headless = headless
        _valid = True


async def restart_interactive() -> None:
    """
    Relaunch the browser in visible (non-headless) mode so the user can solve
    a CAPTCHA. After calling this, use open_page() as normal — the solved
    session will be reused for subsequent headless calls until blocked again.
    """
    await start(headless=False)


async def stop() -> None:
    """Clean shutdown. Call from your server/scan lifespan before the loop closes."""
    async with _get_lock():
        await _teardown_unlocked()


async def _teardown_unlocked() -> None:
    """Internal teardown — must be called with the lock already held."""
    global _playwright, _browser, _context, _valid
    _valid = False
    for obj, method in [
        (_context, "close"),
        (_browser, "close"),
        (_playwright, "stop"),
    ]:
        if obj is not None:
            try:
                await getattr(obj, method)()
            except Exception:
                pass
    _context = None
    _browser = None
    _playwright = None


# ── Per-request page ──────────────────────────────────────────────────────────


@asynccontextmanager
async def open_page() -> AsyncIterator:
    """
    Async context manager that yields a new page in the shared context.
    The page is closed on exit; the context (and its cookies) stays alive.

        async with browser.open_page() as page:
            await page.goto(url)
            data = await page.evaluate(js)

    Raises RuntimeError if the browser isn't running — call start() first,
    or check session_ok().
    """
    if _context is None:
        raise RuntimeError(
            "Browser session is not running. "
            "Call agent_runtime.browser.start() before using open_page()."
        )
    page = await _context.new_page()
    try:
        yield page
    finally:
        try:
            await page.close()
        except Exception:
            pass


# ── Convenience fetch ────────────────────────────────────────────────────────


async def fetch_page(
    url: str,
    *,
    wait_until: str = "networkidle",
    timeout: int = 30_000,
    wait_for_selector: str | None = None,
    wait_for_selector_timeout: int = 8_000,
    return_bytes: bool = False,
) -> tuple[str | bytes, str]:
    """
    Navigate to *url* in a new page and return (html, final_url).

    The page is closed afterwards; the context and its cookies stay alive.
    Use open_page() directly when you need to evaluate JS or do anything
    more than a plain fetch.

    Args:
        url:                        URL to navigate to.
        wait_until:                 Playwright waitUntil strategy
                                    ('domcontentloaded', 'networkidle', …).
        timeout:                    Navigation timeout in ms.
        wait_for_selector:          If set, additionally wait for this CSS
                                    selector before returning.
        wait_for_selector_timeout:  Timeout for the selector wait in ms.

    Raises:
        RuntimeError: if the browser session isn't running.
    """
    async with open_page() as page:
        # Capture the navigation response so we can access raw bytes when
        # requested. Playwright's page.goto() returns a Response object.
        response = await page.goto(url, wait_until=wait_until, timeout=timeout)
        if wait_for_selector:
            try:
                await page.wait_for_selector(
                    wait_for_selector, timeout=wait_for_selector_timeout
                )
            except Exception:
                pass
        final_url = page.url
        if return_bytes:
            # Try to obtain the response body from the navigation response.
            if response is not None:
                try:
                    body = await response.body()
                    return body, final_url
                except Exception:
                    # Fall back to getting the page content if body() is
                    # not available for some responses.
                    pass
            # As a fallback, return the UTF-8-encoded page content bytes.
            html = await page.content()
            try:
                return html.encode("utf-8"), final_url
            except Exception:
                return html, final_url
        else:
            html = await page.content()
            return html, final_url


# ── Smart CAPTCHA-aware wait ──────────────────────────────────────────────────

# Defaults used by smart_wait — callers can override via keyword args.
_CAPTCHA_POLL = BROWSER_CAPTCHA_POLL
_RESULT_WAIT = BROWSER_RESULT_WAIT
_CAPTCHA_SOLVE = BROWSER_CAPTCHA_SOLVE


async def smart_wait(
    page,
    *,
    result_selector: str,
    captcha_selector: str,
    interactive: bool = False,
    captcha_poll_timeout: int = _CAPTCHA_POLL,
    result_wait_timeout: int = _RESULT_WAIT,
    captcha_solve_timeout: int = _CAPTCHA_SOLVE,
) -> bool:
    """
    Wait for page results in a CAPTCHA-aware way.

    Works for any page that has distinct CSS selectors for "results loaded"
    and "CAPTCHA shown" — search engines, image search, etc.

    Non-interactive (autonomous scan, no user present)
    --------------------------------------------------
    - Waits briefly for the result selector.
    - If a CAPTCHA selector appears instead, returns False immediately.
      Nobody is there to solve it; the caller should skip or report blocked.

    Interactive (user is present)
    -----------------------------
    - Polls briefly for a CAPTCHA first.
    - If found: calls restart_interactive() to make the browser visible,
      then waits up to captcha_solve_timeout for the user to solve it.
    - If not found: waits for results normally.

    Returns True if results are likely present, False if blocked by CAPTCHA.

    Example usage in a tool:

        async with browser.open_page() as page:
            await page.goto(url, wait_until="domcontentloaded", timeout=20_000)
            ready = await browser.smart_wait(
                page,
                result_selector="#search, #rso",
                captcha_selector="iframe[src*='recaptcha'], #captcha-form",
                interactive=interactive,
            )
            if not ready:
                return "Blocked by CAPTCHA. Re-run with interactive=True."
            data = await page.evaluate(JS_EXTRACTOR)
    """
    if not interactive:
        # Autonomous — short wait, bail immediately if CAPTCHA appears
        try:
            await page.wait_for_selector(result_selector, timeout=result_wait_timeout)
        except Exception:
            pass
        try:
            await page.wait_for_selector(captcha_selector, timeout=500)
            return False  # CAPTCHA present, no user to solve it
        except Exception:
            return True

    # Interactive — detect CAPTCHA and give user time to solve it
    captcha_present = False
    try:
        await page.wait_for_selector(captcha_selector, timeout=captcha_poll_timeout)
        captcha_present = True
    except Exception:
        pass

    if captcha_present:
        await restart_interactive()
        try:
            await page.wait_for_selector(result_selector, timeout=captcha_solve_timeout)
            return True
        except Exception:
            return False

    try:
        await page.wait_for_selector(result_selector, timeout=result_wait_timeout)
        return True
    except Exception:
        return False


# ── atexit fallback ───────────────────────────────────────────────────────────
# Catches the case where stop() was never called (e.g. crash / KeyboardInterrupt).
# Uses WindowsSelectorEventLoop to avoid the ProactorEventLoop __del__ issue.


def _atexit_stop() -> None:
    if _playwright is None:
        return
    old_policy = None
    try:
        if sys.platform == "win32":
            old_policy = asyncio.get_event_loop_policy()
            asyncio.set_event_loop_policy(
                asyncio.WindowsSelectorEventLoopPolicy()  # type: ignore[attr-defined]
            )
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(_teardown_unlocked())
        finally:
            loop.close()
            if sys.platform == "win32":
                if old_policy is not None:
                    asyncio.set_event_loop_policy(old_policy)
    except Exception:
        pass


atexit.register(_atexit_stop)
