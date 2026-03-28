"""
agent_runtime/scanner/subagent_dispatch.py

Scanner-side dispatch helpers for subagents:
  - SubagentPreflightResult  — preflight output container
  - should_auto_dispatch_evidence_linker
  - build_evidence_linker_payload
  - parse_subagent_call
  - preflight_subagent_calls
  - parse_scope_promote_block
  - dispatch_subagent
  - print_subagent_summary
  - append_subagent_call_records
  - dispatch_evidence_linkers
"""

from __future__ import annotations

import json
import re as _re
from dataclasses import dataclass
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

from ..display import cyan, dim, green, yellow
from ..models import (
    AgentEvent,
    ArtifactObservation,
    CaseFile,
    ScanStats,
    ToolEvidenceRecord,
)
from ..scanner.preflight import MAX_EVIDENCE_PREVIEW
from ..scope import (
    classify_scope_preflight,
    parse_tool_call_args,
    split_scope_meta_args,
    summarize_tool_call,
    is_internal_worklog_tool,
)
from ..scanner.case_log import log_artifact_promotion, log_scope_decision
from ..scanner.flow import record_event
from .registry import (
    is_scope_exempt_subagent,
)
from .runner import SubAgentResult, run_subagent

if TYPE_CHECKING:
    from mcp import ClientSession
    from ..scanner.context import ScanContext


@dataclass
class SubagentPreflightResult:
    """Result of the scope preflight pass over a batch of subagent calls.

    Attributes:
        approved_calls: ``(tool_call, parsed_triple)`` pairs that passed scope.
        blocked_calls: Raw tool-call objects that were scope-blocked.
        blocked_tool_messages: Ready-to-append ``role=tool`` messages for blocked
            calls.
        blocked_feedback_lines: Short human-readable lines for the scope note
            appended to the conversation.
    """

    approved_calls: list[tuple[Any, tuple[str, str, str]]]
    blocked_calls: list[Any]
    blocked_tool_messages: list[dict[str, str]]
    blocked_feedback_lines: list[str]


def should_auto_dispatch_evidence_linker(
    agent_name: str,
    result: SubAgentResult,
    promoted: list[tuple[str, str, str]],
) -> bool:
    """Return ``True`` when an evidence_linker should be auto-dispatched.

    Args:
        agent_name: Name of the agent that just completed.
        result: Its :class:`~subagent_runner.SubAgentResult`.
        promoted: Scope promotions parsed from its findings.

    Returns:
        ``True`` when the agent produced findings that warrant linking, and is
        not itself the evidence_linker (prevents infinite recursion).
    """
    if (agent_name or "").strip() == "evidence_linker":
        return False
    if result.error:
        return False
    if not (result.findings or "").strip():
        return False
    return bool(result.tools_called or promoted)


def build_evidence_linker_payload(
    case_file: CaseFile,
    primary_target: str,
    primary_target_type: str,
    triggering_agent: str,
    max_evidence: int = 8,
) -> tuple[str, str]:
    """Build the task and context strings for an auto-dispatched evidence_linker.

    Args:
        case_file: Current case file; the most recent *max_evidence* records
            are summarised.
        primary_target: Primary scan target.
        primary_target_type: Semantic type of *primary_target*.
        triggering_agent: Name of the agent whose findings triggered this call.
        max_evidence: Number of recent evidence records to include in the
            context summary.

    Returns:
        A ``(task, context)`` string pair.
    """
    task = (
        "Connect newly discovered artifacts into explicit evidence chains. "
        "Each chain entry must answer: how did artifact A reveal artifact B — "
        "not just that both exist. "
        "Use the canonical multi-line format from your skill file. "
        "If the same artifact appears in multiple EV records, connect them into one chain, "
        "do not list them as separate entries. "
        "Include anomaly chains for any unresolved contradictions. "  # ← space added
        "A single artifact with one attribute (e.g. email → registration found) is NOT a chain. "
        "Only document a chain when artifact B was discovered through artifact A. "
        "Lone confirmed facts (a single artifact with one attribute) — skip them entirely. "
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


def parse_subagent_call(tool_call: Any) -> tuple[str, str, str] | None:
    """Parse a ``call_subagent`` tool call into ``(agent, task, context)``.

    Args:
        tool_call: Raw tool-call object from the LLM response.

    Returns:
        A ``(agent_name, task, context)`` triple when the call is valid, or
        ``None`` for any other tool name or malformed JSON.
    """
    try:
        if tool_call.function.name != "call_subagent":
            return None
        args = json.loads(tool_call.function.arguments or "{}")
        return args.get("agent", ""), args.get("task", ""), args.get("context", "")
    except Exception:
        return None


async def preflight_subagent_calls(
    subagent_calls: list[tuple[Any, tuple[str, str, str]]],
    round_num: int,
    target: str,
    target_type: str,
    scope_mode: str,
    extra_targets: list[str],
    case_file: CaseFile,
    stats: ScanStats,
    events: list[AgentEvent],
    event_log_size: int,
    model: str = "",
    confidence_log: Any = None,
    llm_usage: Any = None,
) -> SubagentPreflightResult:
    """Run scope preflight over a batch of subagent calls.

    Scope-exempt agents pass through immediately.  All others go through
    :func:`classify_scope_preflight`.

    Args:
        subagent_calls: ``(tool_call, parsed_triple)`` pairs to evaluate.
        round_num: Current scan round (for event logging).
        target: Primary scan target.
        target_type: Semantic type of *target*.
        scope_mode: Scope enforcement mode.
        extra_targets: Additional scope-allowed targets.
        case_file: Accumulated case file (evidence used by scope classifier).
        stats: Scan statistics (``tools_blocked`` incremented on blocks).
        events: Event log (mutated in place).
        event_log_size: Maximum event log size.
        model: LLM model identifier (used by AI scope modes).
        confidence_log: Optional confidence log.
        llm_usage: Optional LLM usage tracker.

    Returns:
        A :class:`SubagentPreflightResult`.
    """
    if not subagent_calls:
        return SubagentPreflightResult([], [], [], [])

    approved: list[tuple[Any, tuple[str, str, str]]] = []
    gated: list[tuple[Any, tuple[str, str, str]]] = []

    for tc, parsed in subagent_calls:
        agent_name = (parsed[0] or "").strip()
        if is_scope_exempt_subagent(agent_name):
            approved.append((tc, parsed))
        else:
            gated.append((tc, parsed))

    if not gated:
        return SubagentPreflightResult(approved, [], [], [])

    classified = await classify_scope_preflight(
        tool_calls=[tc for tc, _ in gated],
        primary_target=target,
        primary_type=target_type,
        related_targets=list(extra_targets),
        evidence=case_file.evidence_list(),
        scope_mode=scope_mode,
        model=model,
        confidence_log=confidence_log,
        usage=llm_usage,
    )

    blocked_by_id = {id(b.tool_call): b for b in classified.blocked_calls}
    blocked_calls: list[Any] = []
    blocked_tool_messages: list[dict[str, str]] = []
    blocked_feedback_lines: list[str] = []

    for tc, parsed in gated:
        raw_args = parse_tool_call_args(tc)
        exec_args, _ = split_scope_meta_args(raw_args)
        tested = summarize_tool_call("call_subagent", exec_args)
        decision = classified.allowed_scope_decisions.get(id(tc))

        if decision is not None:
            log_scope_decision(
                round_num=round_num,
                source="root-subagent-gate",
                tested=tested,
                scope_decision=decision,
                requested_reason=(raw_args or {}).get("reason", ""),
            )
            approved.append((tc, parsed))
            continue

        blocked = blocked_by_id.get(id(tc))
        if blocked is None:
            continue

        decision = blocked.decision
        log_scope_decision(
            round_num=round_num,
            source="root-subagent-gate",
            tested=tested,
            scope_decision=decision,
            requested_reason=(raw_args or {}).get("reason", ""),
        )
        agent_name, task, _ = parsed
        stats.tools_blocked += 1
        blocked_calls.append(tc)
        blocked_feedback_lines.append(f"{tested}: {decision.reason}")
        record_event(
            events,
            event_log_size,
            round_num + 1,
            "subagent-blocked",
            f"{agent_name}: {decision.reason}",
        )
        blocked_tool_messages.append(
            {
                "role": "tool",
                "tool_call_id": tc.id,
                "name": "call_subagent",
                "content": (
                    f"[SUBAGENT BLOCKED] {decision.reason}\n"
                    f"Agent: {agent_name}\n"
                    f"Task: {task}"
                ),
            }
        )

    return SubagentPreflightResult(
        approved_calls=approved,
        blocked_calls=blocked_calls,
        blocked_tool_messages=blocked_tool_messages,
        blocked_feedback_lines=blocked_feedback_lines,
    )


def parse_scope_promote_block(
    findings: str,
    agent_name: str,
    round_num: int,
    case_file: CaseFile,
    evidence_by_id: dict,
    confidence_approved_domains: set,
    confidence_log: Any = None,
) -> list[tuple[str, str, str]]:
    """Parse ``SCOPE PROMOTE:`` blocks from subagent findings.

    Extracts structured ``kind: value [CONF] — reason`` lines and records each
    as a scope-promotion evidence entry.

    Args:
        findings: Raw findings string from the subagent.
        agent_name: Name of the producing agent.
        round_num: Current scan round.
        case_file: Case file that receives new ``ToolEvidenceRecord`` entries.
        evidence_by_id: Evidence ID → record mapping (mutated in place).
        confidence_approved_domains: Domain allow-set (mutated for ``domain``
            kind promotions).
        confidence_log: Optional confidence log to record promotions in.

    Returns:
        List of ``(kind, value, reason)`` triples for each promoted artifact.
    """
    valid_kinds = {"email", "username", "domain", "ip", "phone", "crypto"}
    block_match = _re.search(
        r"SCOPE PROMOTE:\s*\n(.*?)(?=\nSUBAGENT COMPLETE:|$)",
        findings,
        _re.S | _re.I,
    )
    if not block_match:
        return []

    block = block_match.group(1).strip()
    if not block or block.lower() == "none":
        return []

    line_re = _re.compile(r"^\s*(\w+):\s*(.+?)\s+\[(HIGH|MED)]\s*[—\-–]\s*(.+)$", _re.I)
    promoted: list[tuple[str, str, str]] = []
    seen: set[tuple[str, str]] = set()

    for line in block.splitlines():
        m = line_re.match(line)
        if not m:
            continue
        kind = m.group(1).strip().lower()
        value = m.group(2).strip().lower().rstrip(".,;:")
        conf = m.group(3).upper()
        reason = m.group(4).strip()
        if kind not in valid_kinds or not value:
            continue
        key = (kind, value)
        if key in seen:
            continue
        seen.add(key)

        record = ToolEvidenceRecord(
            round_num=round_num,
            phase="scope-promotion",
            tool_name=f"subagent_promote({agent_name})",
            tool_args={kind: value},
            status="success",
            started_at="",
            duration_ms=0,
            result_preview=f"{kind} [{conf}] from {agent_name}: {value}",
            raw_output=f"{kind}: {value}  [{conf}]  — {reason}",
            target_scope=[value],
            observed_artifacts=[
                ArtifactObservation(
                    value=value, kind=kind, source=f"subagent:{agent_name}"
                )
            ],
            scope_decision_allow=True,
            scope_decision_code="ALLOW_SUBAGENT_PROMOTE",
            scope_decision_reason=(
                f"{kind} [{conf}] declared by {agent_name}: {reason}"
            ),
        )
        ev_id = case_file.add_evidence(record, subagent=True)
        evidence_by_id[ev_id] = record
        if kind == "domain":
            confidence_approved_domains.add(value)
        log_artifact_promotion(
            confidence_log,
            kind=kind,
            value=value,
            conf_level=conf,
            reason=reason,
            round_num=round_num,
        )
        promoted.append((kind, value, reason))

    return promoted


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
    """Dispatch a single subagent and return its result alongside a tool message.

    Blocked domains from the root scan are appended to *context* as a scope
    note so the subagent doesn't re-investigate them.

    Args:
        tc: The original tool-call object (provides ``tc.id`` for the response).
        agent_name: Registry key of the agent to run.
        task: Task string passed to the agent.
        context: Summarised investigation context.
        session: Active MCP session.
        model: LLM model identifier.
        all_mcp_tools: Full list of MCP tool definitions.
        verbose: When ``True``, emit per-tool progress output.
        primary_target: Primary scan target.
        primary_target_type: Semantic type of *primary_target*.
        extra_targets: Additional scope-allowed targets.
        scope_mode: Scope enforcement mode.
        scope_blocked_domains: Domains blocked earlier in the root scan.

    Returns:
        ``(SubAgentResult, tool_message_dict)`` where *tool_message_dict* is
        ready to append to the conversation as a ``role=tool`` message.
    """
    augmented_context = context
    if scope_blocked_domains:
        blocked_list = ", ".join(sorted(scope_blocked_domains)[:20])
        augmented_context = (
            context.rstrip()
            + "\n\n[SCOPE NOTE] The following domains were blocked as out-of-scope "
            "earlier in this investigation — do NOT investigate them: " + blocked_list
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
    result_content = (
        f"[SUBAGENT: {agent_name}]\nTask: {task}\n\nFindings:\n{findings_text}\n"
    )
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
    """Print a brief completion summary for *agent_name* to stdout.

    Args:
        agent_name: Display name.
        result: Completed :class:`~subagent_runner.SubAgentResult`.
        verbose: When ``True``, emits a single-line summary; when ``False``,
            emits a structured multi-line block with tool and finding previews.
    """
    if result.error:
        print(f"  {yellow('[SUBAGENT]')} {agent_name} warning: {dim(result.error)}")

    if verbose:
        print(
            f"  {green('[SUBAGENT]')} {cyan(agent_name)} complete "
            "— summary in findings above"
        )
        return

    findings_preview = ""
    if result.findings:
        for fl in result.findings.splitlines():
            fl = fl.strip()
            if fl and not fl.startswith("SUBAGENT COMPLETE"):
                findings_preview = fl if len(fl) <= 90 else fl[:89] + "…"
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


def append_subagent_call_records(
    ctx: ScanContext,
    *,
    round_num: int,
    agent_name: str,
    tool_call_records: list[dict],
    raw_output: str | None = None,
) -> None:
    """Append sanitised tool-call records from a subagent to the case file.

    Args:
        ctx: Mutable scan context (``case_file.subagent_tool_calls`` is mutated).
        round_num: Root scan round during which the subagent ran.
        agent_name: Name of the agent that produced the records.
        tool_call_records: Raw records from :class:`~subagent_runner.SubAgentResult`.
    """
    from ..scanner.case_log import sanitize_audit

    for call in tool_call_records:
        sanitized = dict(call)
        sanitized["scope_ai_evaluation"] = sanitize_audit(
            sanitized.get("scope_ai_evaluation")
        )
        # raw output is flawed I think
        ctx.case_file.subagent_tool_calls.append(
            {
                "root_round": round_num + 1,
                "agent_name": agent_name,
                "raw_output": raw_output,
                **sanitized,
            }
        )

        if call.get("status") == "success" and not is_internal_worklog_tool(
            call.get("tool_name", "")
        ):
            record = ToolEvidenceRecord(
                round_num=round_num + 1,
                phase=call.get("phase", "subagent"),
                tool_name=call["tool_name"],
                tool_args=call.get("tool_args", {}),
                status="success",
                started_at=call.get("started_at", ""),
                duration_ms=call.get("duration_ms", 0),
                result_preview=call.get("result_preview", "")[:MAX_EVIDENCE_PREVIEW],
                raw_output=call.get("result", ""),
                target_scope=call.get("target_scope", []),
                observed_artifacts=call.get("deduped_obs", []),
                scope_mode=call.get("scope_mode", ""),
                scope_decision_allow=call.get("scope_decision_allow", True),
                scope_decision_code=call.get("scope_decision_code", ""),
                scope_decision_reason=call.get("scope_decision_reason", ""),
                scope_ai_audit=call.get("scope_ai_evaluation"),
                is_duplicate=call.get("is_duplicate", False),
                duplicate_of=call.get("duplicate_of", None),
            )
            eid = ctx.case_file.add_evidence(record, subagent=True)
            ctx.evidence_by_id[eid] = record


async def dispatch_evidence_linkers(
    ctx: ScanContext,
    pending_linker_dispatches: list[tuple[str, Any, list]],
    round_num: int,
) -> None:
    """Dispatch evidence_linker subagents for any agents that produced findings.

    Runs after the main round flush so each linker starts a clean
    ``assistant + tool`` turn.

    Args:
        ctx: Mutable scan context.
        pending_linker_dispatches: List of ``(triggering_agent, result, promoted)``
            tuples collected during the round.
        round_num: Current scan round index.
    """
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
            f"evidence_linker: {len(linker_result.tools_called)} tools, "
            f"error={linker_result.error}",
        )
        ctx.convo.append(linker_tool_message)
        print_subagent_summary("evidence_linker", linker_result, verbose=ctx.verbose)


__all__ = [
    "SubagentPreflightResult",
    "append_subagent_call_records",
    "build_evidence_linker_payload",
    "dispatch_evidence_linkers",
    "dispatch_subagent",
    "parse_scope_promote_block",
    "parse_subagent_call",
    "preflight_subagent_calls",
    "print_subagent_summary",
    "should_auto_dispatch_evidence_linker",
]
