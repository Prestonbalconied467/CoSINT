"""
tools/search.py  –  Web Search & Google Dorks
Tools: osint_web_search, osint_web_dork
"""

from __future__ import annotations

from typing import Annotated, Optional

from mcp.server.fastmcp import FastMCP
from pydantic import Field

from agent_runtime import browser
from tools.helper.search_utils import (
    ALL_ENGINES_BLOCKED_MSG,
    ALL_ENGINES_BLOCKED_INTERACTIVE_MSG,
    BotDetectedError,
    DORK_TEMPLATES,
    DORK_TYPE_DESCRIPTION,
    ENGINES,
    SESSION_BLOCKED_MSG,
    build_dork,
    engine_search,
    format_results,
)

_ENGINE_KEYS = sorted(ENGINES.keys())
_ENGINE_KEY_DESCRIPTION = (
    "Preferred search engine. Tried first; falls back through the chain "
    f"(google → bing → ddg) on bot detection. Options: {', '.join(_ENGINE_KEYS)}."
)


def register(mcp: FastMCP) -> None:
    @mcp.tool(annotations={"readOnlyHint": True})
    async def osint_web_search(
        query: Annotated[
            str,
            Field(
                description="Search query. Supports operators: site:, filetype:, OR, -."
            ),
        ],
        interactive: Annotated[
            bool,
            Field(
                description=(
                    "True when a human is present who can solve CAPTCHAs. "
                    "False for unattended/automated runs."
                )
            ),
        ] = False,
        max_results: Annotated[
            int, Field(description="Maximum results to return (1–20).", ge=1, le=20)
        ] = 10,
        engine: Annotated[
            str,
            Field(description=_ENGINE_KEY_DESCRIPTION),
        ] = "google",
    ) -> str:
        """Search the web using a real browser with a persistent CAPTCHA-solved session.

        Tries the preferred engine first, then falls back through bing → ddg automatically
        on bot detection. DuckDuckGo (ddg) is the most reliable fallback for unattended
        runs — it uses DDG lite (near-plaintext HTML) and almost never triggers CAPTCHA.

        Returns: list of results with title, URL, and snippet.
        Use for: free-form queries, negative-space searches (confirming absence of expected
          content), and follow-up queries after dork results.
        Keep queries specific (3–6 words). A manually-solved CAPTCHA is reused across
          calls until the session is blocked again.
        """
        query = (query or "").strip()
        if not query:
            return "Error: query cannot be empty."

        engine_key = (engine or "google").strip().lower()
        if engine_key not in ENGINES:
            valid = ", ".join(_ENGINE_KEYS)
            return f"Error: unknown engine '{engine_key}'. Valid: {valid}"

        try:
            results, engine_used = await engine_search(
                query, max_results, interactive, engine_key=engine_key
            )
        except BotDetectedError:
            return (
                ALL_ENGINES_BLOCKED_INTERACTIVE_MSG
                if interactive
                else ALL_ENGINES_BLOCKED_MSG
            )
        except Exception as e:
            return f"Search error: {e}"

        if not results:
            return f"No results found for '{query}'."

        return format_results(
            f"Web search results for '{query}' via {engine_used} ({len(results)} results):",
            query,
            results,
        )

    @mcp.tool(annotations={"readOnlyHint": True})
    async def osint_web_dork(
        target: Annotated[
            str,
            Field(
                description=(
                    "Value to search for: name, email, username, phone, domain, etc. "
                    "Do not add quotation marks — the dork builder quotes terms automatically."
                )
            ),
        ],
        dork_type: Annotated[
            str,
            Field(description=DORK_TYPE_DESCRIPTION),
        ],
        interactive: Annotated[
            bool,
            Field(
                description=(
                    "True when a human is present who can solve CAPTCHAs. "
                    "False for unattended/automated runs."
                )
            ),
        ] = False,
        extra_terms: Annotated[
            Optional[str],
            Field(
                description="Extra operators appended to the dork, e.g. 'site:de after:2023-01-01'."
            ),
        ] = None,
        max_results: Annotated[
            int, Field(description="Maximum results to return (1–20).", ge=1, le=20)
        ] = 10,
        engine: Annotated[
            str,
            Field(description=_ENGINE_KEY_DESCRIPTION),
        ] = "google",
    ) -> str:
        """Build and execute a targeted Google dork for OSINT discovery via a persistent browser session.

        Tries the preferred engine first, then falls back through bing → ddg automatically
        on bot detection. Note: operator-heavy dorks (site:, filetype:) work best on
        Google and Bing — the fallback note in results will flag if a weaker engine was used.

        Returns: list of results with title, URL, and snippet.
        dork_type values:
          person          → full name across profile-heavy and directory sites
          email_exposure  → email appearances on the surface web
          username        → handle presence across platforms and forums
          phone           → number mentions in listings and complaints
          domain_mentions → external references to a domain outside the domain itself
          company         → business profile sources, complaints, and corporate mentions
          crypto_mentions → wallet address appearances in forums and GitHub
          document_search → CVs, presentations, academic papers, and leaked documents
          forum_mentions  → community and forum discussion mentions
          paste_exposure  → value in public paste URLs (no IntelX quota cost)
          news            → recent press coverage
          general         → broad catch-all; always run alongside any specific dork_type
        """
        target = (target or "").strip()
        if not target:
            return "Error: target cannot be empty."

        dork_type = (dork_type or "").strip().lower()
        if dork_type not in DORK_TEMPLATES:
            valid = ", ".join(sorted(DORK_TEMPLATES.keys()))
            return f"Error: unknown dork_type '{dork_type}'. Valid: {valid}"

        engine_key = (engine or "google").strip().lower()
        if engine_key not in ENGINES:
            valid = ", ".join(_ENGINE_KEYS)
            return f"Error: unknown engine '{engine_key}'. Valid: {valid}"

        if not browser.session_ok() and not interactive:
            return SESSION_BLOCKED_MSG

        query = build_dork(dork_type, target, extra_terms or "")

        try:
            results, engine_used = await engine_search(
                query, max_results, interactive, engine_key=engine_key
            )
        except BotDetectedError:
            return (
                ALL_ENGINES_BLOCKED_INTERACTIVE_MSG
                if interactive
                else ALL_ENGINES_BLOCKED_MSG
            )
        except Exception as e:
            return f"Dork search error: {e}"

        if not results:
            return (
                f"No results for dork_type='{dork_type}' target='{target}'.\n"
                f"Query used: {query}"
            )

        # Warn when the winning engine may not fully honour operator syntax
        engine_cfg = ENGINES.get(engine_used.lower(), ENGINES[engine_key])
        operator_note = (
            f"\nNote: fell back to {engine_used}, which may not fully honour "
            "site:/filetype: operators — results may be less precise."
            if engine_used.lower() != engine_key and not engine_cfg.supports_operators
            else (
                f"\nNote: fell back to {engine_used} (bot detection on preferred engine)."
                if engine_used.lower() != engine_key
                else ""
            )
        )

        return format_results(
            f"Dork search [{dork_type}] → '{target}' via {engine_used} ({len(results)} results):{operator_note}",
            query,
            results,
        )
