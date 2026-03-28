"""
server.py – OSINT AI MCP Server entry point.

Starts a FastMCP server and registers all OSINT tool modules.

Start: python server.py
MCP transport: stdio (local)
"""

import logging
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from agent_runtime import browser
from shared import http_client
from shared.session_tracker import SessionRunTracker
from tools import (
    company,
    crypto,
    domain,
    email,
    geo,
    leaks,
    media,
    network,
    notes,
    person,
    phone,
    public,
    scraper,
    search,
    session,
    social,
    todo,
    username,
)

# Add venv Scripts to PATH so CLI tools like holehe can be found
venv_scripts = Path(sys.executable).parent
if str(venv_scripts) not in os.environ["PATH"]:
    os.environ["PATH"] = f"{venv_scripts}{os.pathsep}{os.environ['PATH']}"

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stderr,  # MCP communicates over stdout — logs go to stderr only
)

# ── Browser lifespan ──────────────────────────────────────────────────────────


@asynccontextmanager
async def lifespan(server: FastMCP):
    await browser.start(headless=True)
    try:
        yield
    finally:
        await browser.stop()
        await http_client.close()


# ── Server setup ──────────────────────────────────────────────────────────────
mcp = FastMCP(
    "cosint",
    instructions=(
        "OSINT investigation server. Exposes tools for domain, email, IP, username, "
        "phone, person, company, crypto, geo, media, leaks, and public records research."
    ),
    lifespan=lifespan,
)

# ── Session tracking ──────────────────────────────────────────────────────────
TRACKER = SessionRunTracker(max_events=1000)


def _install_tracking_hook(server: FastMCP, tracker: SessionRunTracker) -> None:
    """Wrap FastMCP tool decorators so all registered tools are tracked."""
    original_tool = server.tool

    def tracked_tool(*args: Any, **kwargs: Any):
        decorator = original_tool(*args, **kwargs)

        def apply(fn):
            return decorator(tracker.wrap_tool(fn))

        return apply

    server.tool = tracked_tool


_install_tracking_hook(mcp, TRACKER)

# ── Register all tool modules ─────────────────────────────────────────────────
domain.register(mcp)
network.register(mcp)
email.register(mcp)
person.register(mcp)
company.register(mcp)
username.register(mcp)
leaks.register(mcp)
phone.register(mcp)
media.register(mcp)
geo.register(mcp)
crypto.register(mcp)
public.register(mcp)
scraper.register(mcp)
social.register(mcp)
search.register(mcp)
todo.register(mcp)
notes.register(mcp)
session.register(mcp, TRACKER)


# ── Start ─────────────────────────────────────────────────────────────────────
def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
