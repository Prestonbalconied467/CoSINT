from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

from mcp import ClientSession


def _inject_scope_reason_parameter(
    schema: dict | None, *, required: bool = False
) -> dict:
    """Add scope rationale metadata to a tool schema."""
    base = schema if isinstance(schema, dict) else {"type": "object", "properties": {}}
    if base.get("type") != "object":
        return base

    properties = base.get("properties")
    if not isinstance(properties, dict):
        properties = {}
        base["properties"] = properties

    if "reason" not in properties:
        properties["reason"] = {
            "type": "string",
            "description": (
                "Required scope rationale explaining why this tool call is relevant "
                "to the investigation target and how it connects to known evidence. "
                "Used only by scope confidence checks; not sent to the MCP tool."
            ),
        }

    if required:
        existing_required = base.get("required")
        if not isinstance(existing_required, list):
            existing_required = []
            base["required"] = existing_required
        if "reason" not in existing_required:
            existing_required.append("reason")

    return base


async def get_mcp_tools(
    session: ClientSession, *, scope_mode: str = "strict"
) -> list[dict]:
    reason_required = scope_mode in {"guided", "ai", "explore"}
    tools_result = await session.list_tools()
    return [
        {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description or "",
                "parameters": _inject_scope_reason_parameter(
                    tool.inputSchema or {"type": "object", "properties": {}},
                    required=reason_required,
                ),
            },
        }
        for tool in tools_result.tools
    ]


async def call_mcp_tool(session: ClientSession, name: str, args: dict) -> str:
    try:
        result = await session.call_tool(name, args)
        parts = result.content
        if not parts:
            return "(no output)"
        return "\n".join(p.text if hasattr(p, "text") else str(p) for p in parts)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        return f"Tool error ({name}): {exc}"


def make_tool_call_signature(name: str, args: dict) -> str:
    try:
        canonical = json.dumps(
            args, sort_keys=True, separators=(",", ":"), ensure_ascii=True
        )
    except TypeError:
        canonical = repr(args)
    return f"{name}:{canonical}"


def build_call_ledger(seen_signatures: set[str]) -> str | None:
    """Build a compact summary of already-executed tool calls for dedupe guidance."""
    if not seen_signatures:
        return None

    lines = []
    for sig in seen_signatures:
        colon = sig.index(":")
        tool = sig[:colon]
        raw_args = sig[colon + 1 :]
        try:
            args = json.loads(raw_args)
            val = (
                args.get("email")
                or args.get("domain")
                or args.get("ip")
                or args.get("username")
                or args.get("query")
                or args.get("url")
                or args.get("name")
                or args.get("phone")
                or args.get("address")
                or raw_args
            )
        except (ValueError, AttributeError):
            val = raw_args
        lines.append(f"  - {tool}({val})")

    joined = "\n".join(lines)
    return (
        "[TOOL CALL LEDGER] The following tool+argument combinations have already been called "
        "this session. Do NOT call any of them again -- their results are already in the "
        f"conversation history above.\n{joined}"
    )


__all__ = [
    "build_call_ledger",
    "call_mcp_tool",
    "get_mcp_tools",
    "make_tool_call_signature",
]