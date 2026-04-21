"""
agent_runtime/subagents/dispatch_execution.py

Subagent execution, summaries, and auto-dispatch helpers.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

from ..display import cyan, dim, green, yellow
from ..investigation.events import record_event
from .dispatch_records import append_subagent_call_records
from .runner import SubAgentResult, run_subagent

if TYPE_CHECKING:
    from mcp import ClientSession


def should_auto_dispatch_evidence_linker(
    agent_name: str,
    result: SubAgentResult,
    promoted: list[tuple[str, str, str]],
) -> bool:
    if (agent_name or "").strip() == "evidence_linker":
        return False
    if result.error:
        return False
    if not (result.findings or "").strip():
        return False
    return bool(result.tools_called or promoted)


def build_evidence_linker_payload(
    case_file: "CaseFile",
    primary_target: str,
    primary_target_type: str,
    triggering_agent: str,
    max_evidence: int = 8,
) -> tuple[str, str]:
    task = (
        "Connect newly discovered artifacts into explicit evidence chains. "
        "Each chain entry must answer: how did artifact A reveal artifact B - "
        "not just that both exist. "
        "Use the canonical multi-line format from your skill file. "
        "If the same artifact appears in multiple EV records, connect them into one chain, "
        "do not list them as separate entries. "
        "Include anomaly chains for any unresolved contradictions. "
        "A single artifact with one attribute (e.g. email -> registration found) is NOT a chain. "
        "Only document a chain when artifact B was discovered through artifact A. "
        "Lone confirmed facts (a single artifact with one attribute) - skip them entirely. "
        "Do not record them in any format. "
    )
    recent = case_file.recent_evidence(max_evidence)
    evidence_lines = [
        (
            f"- {ev} | {ev.tool_name} | status={ev.status} | "
            f"preview={ev.result_preview[:200].replace(chr(10), ' ')}"
        )
        for ev in recent
    ]
    context = (
        f"Primary target: {primary_target} ({primary_target_type})\n"
        f"Triggering subagent: {triggering_agent}\n"
        f"Recent evidence to link ({len(recent)} items):\n"
        + ("\n".join(evidence_lines) if evidence_lines else "- none")
    )
    return task, context


async def dispatch_subagent(
    tc: Any,
    agent_name: str,
    task: str,
    context: str,
    session: ClientSession,
    model: str,
    all_mcp_tools: list,
    verbose: bool,
    primary_target: str,
    primary_target_type: str,
    extra_targets: list[str],
    scope_mode: str,
    scope_blocked_domains: set[str],
) -> tuple[SubAgentResult, dict]:
    augmented_context = context
    if scope_blocked_domains:
        blocked_list = ", ".join(sorted(scope_blocked_domains)[:20])
        augmented_context = (
            context.rstrip()
            + "\n\n[SCOPE NOTE] The following domains were blocked as out-of-scope "
            "earlier in this investigation - do NOT investigate them: "
            + blocked_list
        )

    result = await run_subagent(
        agent_name=agent_name,
        task=task,
        context=augmented_context,
        mcp_session=session,
        model=model,
        all_mcp_tools=all_mcp_tools,
        verbose=verbose,
        max_rounds=12,
        primary_target=primary_target,
        primary_target_type=primary_target_type,
        extra_targets=extra_targets,
        scope_mode=scope_mode,
    )

    findings_text = result.findings or "(no findings returned)"
    result_content = f"[SUBAGENT: {agent_name}]\nTask: {task}\n\nFindings:\n{findings_text}\n"
    if result.tools_called:
        result_content += f"\nTools called: {', '.join(result.tools_called)}"
    if result.error:
        result_content += f"\nNote: {result.error}"

    tool_message = {
        "role": "tool",
        "tool_call_id": tc.id,
        "name": "call_subagent",
        "content": result_content,
    }
    return result, tool_message


def print_subagent_summary(
    agent_name: str,
    result: SubAgentResult,
    verbose: bool,
) -> None:
    if result.error:
        print(f"  {yellow('[SUBAGENT]')} {agent_name} warning: {dim(result.error)}")

    if verbose:
        print(
            f"  {green('[SUBAGENT]')} {cyan(agent_name)} complete "
            "- summary in findings above"
        )
        return

    findings_preview = ""
    if result.findings:
        for fl in result.findings.splitlines():
            fl = fl.strip()
            if fl and not fl.startswith("SUBAGENT COMPLETE"):
                findings_preview = fl if len(fl) <= 90 else fl[:89] + "..."
                break

    unique_tools = list(dict.fromkeys(result.tools_called))
    tool_summary = (
        ", ".join(t.replace("osint_", "").replace("_", " ") for t in unique_tools[:5])
        or "no tools"
    )
    if len(unique_tools) > 5:
        tool_summary += f" +{len(unique_tools) - 5} more"

    print(f"  {green('[SUBAGENT]')} {cyan(agent_name)} complete")
    print(f"    {dim('Tools:')}    {dim(tool_summary)}")
    if result.scope_blocks:
        print(f"    {yellow('Blocked:')}  {result.scope_blocks} scope block(s)")
    if findings_preview:
        print(f"    {dim('Finding:')}  {dim(findings_preview)}")
    if result.error:
        print(f"    {yellow('Warning:')}  {dim(result.error)}")


async def dispatch_evidence_linkers(
    ctx: "ScanContext",
    pending_linker_dispatches: list[tuple[str, Any, list]],
    round_num: int,
) -> None:
    from ..display import print_subagent_dispatch

    for triggering_agent, _subagent_result, _promoted in pending_linker_dispatches:
        auto_task, auto_context = build_evidence_linker_payload(
            case_file=ctx.case_file,
            primary_target=ctx.target,
            primary_target_type=ctx.target_type,
            triggering_agent=triggering_agent,
        )
        auto_call_id = (
            f"auto-call-subagent-{round_num + 1}"
            f"-{len(ctx.case_file.subagent_tool_calls) + 1}"
        )
        auto_args = {
            "agent": "evidence_linker",
            "task": auto_task,
            "context": auto_context,
        }

        ctx.convo.append(
            {
                "role": "assistant",
                "tool_calls": [
                    {
                        "id": auto_call_id,
                        "type": "function",
                        "function": {
                            "name": "call_subagent",
                            "arguments": json.dumps(auto_args, ensure_ascii=False),
                        },
                    }
                ],
            }
        )
        print_subagent_dispatch(
            "evidence_linker", "link recent evidence chains", auto=True
        )
        record_event(
            ctx.events,
            ctx.event_log_size,
            round_num + 1,
            "subagent-auto-dispatch",
            f"evidence_linker: trigger={triggering_agent}",
        )

        auto_tc = SimpleNamespace(id=auto_call_id)
        linker_result, linker_tool_message = await dispatch_subagent(
            tc=auto_tc,
            agent_name="evidence_linker",
            task=auto_task,
            context=auto_context,
            session=ctx.session,
            model=ctx.model,
            all_mcp_tools=ctx.all_mcp_tools,
            verbose=ctx.verbose,
            primary_target=ctx.target,
            primary_target_type=ctx.target_type,
            extra_targets=ctx.extra_targets,
            scope_mode=ctx.scope_mode,
            scope_blocked_domains=ctx.scope_blocked_domains,
        )
        ctx.root.record_result(linker_result)
        ctx.stats.subagents_activated.append("evidence_linker")
        append_subagent_call_records(
            ctx,
            round_num=round_num,
            agent_name="evidence_linker",
            tool_call_records=linker_result.tool_call_records,
            raw_output=None,
        )
        record_event(
            ctx.events,
            ctx.event_log_size,
            round_num + 1,
            "subagent-complete",
            f"evidence_linker: {len(linker_result.tools_called)} tools, error={linker_result.error}",
        )
        ctx.convo.append(linker_tool_message)
        print_subagent_summary("evidence_linker", linker_result, verbose=ctx.verbose)


__all__ = [
    "build_evidence_linker_payload",
    "dispatch_evidence_linkers",
    "dispatch_subagent",
    "print_subagent_summary",
    "should_auto_dispatch_evidence_linker",
]

