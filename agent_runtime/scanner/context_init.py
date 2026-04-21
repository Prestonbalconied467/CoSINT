"""
agent_runtime/scanner/context_init.py

ScanContext initialization phase extracted from scanner context module.
"""

from __future__ import annotations

from ..llm import Conversation
from ..mcp_runtime import get_mcp_tools
from ..models import ScopeInclusion
from ..prompting import (
    build_hypothesis_block,
    build_initial_messages,
    build_instruction_block,
    build_multi_target_block,
    build_opening_parts,
    build_policy_block,
    build_reference_injection,
    build_system_prompt,
)
from ..skills import load_skill
from ..subagents import RootCoordinator, build_subagent_tool_definitions
from ..targeting import detect_type
from ..context_utils import get_model_max_tokens
from ..display import print_scan_startup, print_token_note
from ..investigation.events import record_event


async def init_scan_state(ctx: "ScanContext") -> None:
    ctx.all_mcp_tools = await get_mcp_tools(ctx.session, scope_mode=ctx.scope_mode)

    if ctx.max_context_tokens == 0:
        resolved = get_model_max_tokens(ctx.model)
        ctx.max_context_tokens = resolved
        record_event(
            ctx.events,
            ctx.event_log_size,
            0,
            "context",
            f"max_context_tokens auto-resolved: model={resolved}",
        )
        print_token_note(f"{resolved:,}")
    else:
        record_event(
            ctx.events,
            ctx.event_log_size,
            0,
            "context",
            f"max_context_tokens manual: {ctx.max_context_tokens}",
        )
        print_token_note(f"{ctx.max_context_tokens:,}")

    general_skill = load_skill("general") or ""
    reasoning_skill = load_skill("reasoning") or ""
    correlation_skill = load_skill("correlation") or ""
    depth_skill = load_skill(ctx.depth) or ""
    report_skill = load_skill("subagent_report_synthesizer") or ""

    ctx.reference_injection = build_reference_injection(
        general_skill=general_skill,
        reasoning_skill=reasoning_skill,
        depth_skill=depth_skill,
        correlation_skill=correlation_skill,
        report_skill=report_skill,
        correlate_targets=ctx.correlate_targets,
    )

    ctx.root = RootCoordinator(
        target_type=ctx.target_type,
        has_multi_targets=bool(ctx.extra_targets),
        correlate_targets=ctx.correlate_targets,
    )

    ctx.system_prompt = build_system_prompt(
        target=ctx.target,
        target_type=ctx.target_type,
        depth=ctx.depth,
        dispatch_hint=ctx.root.build_dispatch_hint(),
        instruction_block=build_instruction_block(ctx.instruction),
        hypothesis_block=build_hypothesis_block(ctx.hypothesis),
        policy_block=build_policy_block(ctx.policy_flags),
        multi_target_block=build_multi_target_block(
            ctx.extra_targets, ctx.correlate_targets
        ),
        interactive=ctx.interactive_root,
        instruction_text=ctx.instruction or "",
        hypothesis_text=ctx.hypothesis or "",
        correlate_targets=ctx.correlate_targets,
        open_ended=ctx.open_ended,
    )
    ctx.root_tools = ctx.all_mcp_tools + build_subagent_tool_definitions()

    ctx.opening_parts = build_opening_parts(
        target=ctx.target,
        target_type=ctx.target_type,
        depth=ctx.depth,
        extra_targets=ctx.extra_targets,
        correlate_targets=ctx.correlate_targets,
        policy_flags=ctx.policy_flags,
        instruction=ctx.instruction,
        hypothesis=ctx.hypothesis,
    )

    messages, ctx.role_label = build_initial_messages(
        system_prompt=ctx.system_prompt,
        reference_injection=ctx.reference_injection,
        opening_parts=ctx.opening_parts,
        prefer_system=True,
        model=ctx.model,
    )
    ctx.convo = Conversation(model=ctx.model, messages=messages, usage=ctx.usage)

    ctx.case_file.scope_inclusions.append(
        ScopeInclusion(value=ctx.target, kind=ctx.target_type, reason="primary_target")
    )
    for rel in ctx.extra_targets:
        rel_type = detect_type(rel)
        ctx.case_file.scope_inclusions.append(
            ScopeInclusion(value=rel, kind=rel_type, reason="related_target")
        )

    print_scan_startup(
        target=ctx.target,
        target_type=ctx.target_type,
        depth=ctx.depth,
        role_label=ctx.role_label,
        num_tools=len(ctx.all_mcp_tools),
        initial_agent_names=ctx.root.initial_agent_names(),
    )
    record_event(
        ctx.events,
        ctx.event_log_size,
        0,
        "root",
        f"initial subagents: {', '.join(ctx.root.initial_agent_names())}",
    )


__all__ = ["init_scan_state"]

