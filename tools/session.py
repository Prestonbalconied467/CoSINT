"""
tools/session.py  –  MCP Session Tracking Tools

Exposes three tools that let the agent inspect which tools ran in the current
MCP server session, useful for debugging and reporting.

Registered by server.py via session.register(mcp, tracker).
"""

from typing import Annotated

from mcp.server.fastmcp import FastMCP
from pydantic import Field

from shared.session_tracker import SessionRunTracker


def register(mcp: FastMCP, tracker: SessionRunTracker) -> None:
    @mcp.tool(annotations={"readOnlyHint": True})
    async def osint_session_tool_summary() -> str:
        """Show a summary of tool runs in the current MCP server session."""
        return tracker.summary_text()

    @mcp.tool(annotations={"readOnlyHint": True})
    async def osint_session_tool_runs(
        limit: Annotated[
            int, Field(description="Max runs to return (1–500)", ge=1, le=500)
        ] = 50,
    ) -> str:
        """List recent tool runs in the current MCP session as JSON."""
        if limit < 1:
            limit = 1
        if limit > 500:
            limit = 500
        return tracker.runs_text(limit=limit)

    @mcp.tool(annotations={"destructiveHint": True})
    async def osint_session_tool_runs_clear() -> str:
        """Clear current MCP session run history."""
        cleared = tracker.clear()
        return f"Cleared {cleared} session run record(s)."
