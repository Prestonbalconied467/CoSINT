"""
agent_runtime/scanner/llm_round.py

LLM completion helpers for scanner rounds.
"""

from __future__ import annotations

from typing import Any

from ..display import print_skills_confirmed, print_warn
from ..investigation.events import record_event
from ..llm import Conversation, is_system_role_error
from ..mcp_runtime import build_call_ledger
from ..prompting import build_initial_messages


async def build_ledger_extras(ctx: "ScanContext") -> list[dict[str, str]] | None:
    ledger_content = build_call_ledger(ctx.seen_call_signatures)
    if not ledger_content:
        return None
    return [{"role": "system", "content": ledger_content}]


def rebuild_conversation_as_user_role(ctx: "ScanContext", exc: Exception) -> None:
    messages, ctx.role_label = build_initial_messages(
        system_prompt=ctx.system_prompt,
        reference_injection=ctx.reference_injection,
        opening_parts=ctx.opening_parts,
        prefer_system=False,
        model=ctx.model,
    )
    ctx.convo = Conversation(model=ctx.model, messages=messages, usage=ctx.usage)

    print_warn(f"System role rejected -- retrying as {ctx.role_label}")
    record_event(
        ctx.events,
        ctx.event_log_size,
        1,
        "system-role-fallback",
        str(exc)[:120],
    )


async def get_llm_response(ctx: "ScanContext", round_num: int) -> Any:
    extras = await build_ledger_extras(ctx)

    qa_pending = getattr(ctx, "qa_verdict_pending", False)
    if qa_pending:
        ctx.qa_verdict_pending = False  # type: ignore[attr-defined]

    tools = None if (qa_pending or ctx.report_requested) else ctx.root_tools

    try:
        msg = await ctx.convo.complete(tools=tools, extra_messages=extras)
        if round_num == 0:
            print_skills_confirmed(ctx.role_label)
        return msg
    except Exception as exc:
        if round_num == 0 and is_system_role_error(exc):
            rebuild_conversation_as_user_role(ctx, exc)
            return await ctx.convo.complete(tools=tools, extra_messages=extras)
        raise


__all__ = ["build_ledger_extras", "get_llm_response", "rebuild_conversation_as_user_role"]

