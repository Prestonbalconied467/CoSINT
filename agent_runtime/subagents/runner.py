"""
agent_runtime/scanner/subagent_runner.py

Isolated LLM execution for a single subagent:
  - SubAgentResult  — return value from one subagent run
  - run_subagent    — own tool loop with scope enforcement
  - _build_subagent_system_prompt
  - _preview_args
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

try:
    import litellm
except ImportError:
    litellm = None  # type: ignore

from ..llm import ConfidenceLog, Conversation, LLMError, LLMUsage
from ..mcp_runtime import call_mcp_tool, make_tool_call_signature
from ..scope import (
    build_scope_policy,
    evaluate_tool_scope,
    parse_tool_call_args,
    split_scope_meta_args,
)
from ..skills import load_skill
from ..targeting import extract_artifact_observations, normalize_target_value
from .registry import SUBAGENT_REGISTRY


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass
class SubAgentResult:
    """Result returned by :func:`run_subagent`.

    Attributes:
        agent_name: Registry name of the agent that ran.
        task: The task string passed to the agent.
        findings: Raw text output produced by the agent (may be empty on error).
        tools_called: Ordered list of tool names the agent invoked.
        tool_call_records: Structured audit records for every tool call, including
            scope decisions.
        scope_blocks: Number of tool calls that were blocked by the scope gate.
        error: Non-``None`` when the agent did not complete cleanly.
    """

    agent_name: str
    task: str
    findings: str
    tools_called: list[str] = field(default_factory=list)
    tool_call_records: list[dict[str, Any]] = field(default_factory=list)
    scope_blocks: int = 0
    error: str | None = None


# ---------------------------------------------------------------------------
# System prompt builder
# ---------------------------------------------------------------------------


def _build_subagent_system_prompt(
    *,
    agent_name: str,
    skill_content: str,
    has_tools: bool,
) -> str:
    """Assemble the system prompt for a subagent.

    Args:
        agent_name: Display name of the agent.
        skill_content: Full content of the agent's skill file.
        has_tools: When ``True``, the tool-usage section encourages active tool
            calls; when ``False``, the agent is directed to reason over context only.

    Returns:
        A complete system prompt string.
    """
    tool_section = (
        """
## Tool Usage
You have access to MCP tools. Use them to collect evidence for your assigned task.
- Call tools in order of signal quality — cheapest and highest-signal first.
- After each result write one line: "Found: [key fact]" or "No results — [what this means]".
- Never repeat a tool call with the same arguments.
- When you have enough evidence to answer your task, STOP calling tools and write findings.
"""
        if has_tools
        else """
## Tool Usage
You have no tools. Reason carefully over the context and evidence provided.
"""
    )
    return (
        f"You are the {agent_name} specialist subagent in an OSINT investigation system.\n\n"
        f"{skill_content}\n"
        f"{tool_section}\n"
        "## Output Requirements\n"
        "Structure your findings clearly:\n"
        "- Tag every finding: [HIGH] / [MED] / [LOW] confidence.\n"
        "- Reference the source for each finding (tool name, breach DB, etc.).\n"
        "- If you find something outside your scope worth escalating, write: ESCALATE: [finding]\n"
        "- End your output with: SUBAGENT COMPLETE: [one sentence summary of what was established]\n\n"
        "Stay tightly focused on your assigned task. Do not pursue unrelated leads.\n"
    )


# ---------------------------------------------------------------------------
# Argument preview helper
# ---------------------------------------------------------------------------


def _preview_args(args: dict) -> str:
    """Format up to two key=value pairs from *args* for display.

    Args:
        args: Tool argument dict.

    Returns:
        A short ``key=value, ...`` string, or an empty string if *args* is empty.
    """
    if not args:
        return ""
    return ", ".join(f"{k}={str(v)[:40]}" for k, v in list(args.items())[:2])


# ---------------------------------------------------------------------------
# run_subagent
# ---------------------------------------------------------------------------


async def run_subagent(
    *,
    agent_name: str,
    task: str,
    context: str,
    mcp_session: Any,
    model: str,
    all_mcp_tools: list[dict],
    verbose: bool = False,
    max_rounds: int = 12,
    primary_target: str = "",
    primary_target_type: str = "",
    extra_targets: list[str] | None = None,
    scope_mode: str = "open",
) -> SubAgentResult:
    """Run a subagent as an isolated LLM call with its own tool loop.

    The subagent gets its own ``Conversation``, scope policy, confidence log,
    and LLM usage tracker — nothing leaks back to the root.

    Args:
        agent_name: Registry key identifying which specialist to run.
        task: The focused task description.
        context: Structured investigation context (summarised, not raw history).
        mcp_session: Active MCP session for calling tools.
        model: LLM model identifier.
        all_mcp_tools: Full list of available MCP tool definitions from the root.
        verbose: When ``True``, emit per-tool progress lines.
        max_rounds: Maximum tool-loop iterations before the agent is forced to stop.
        primary_target: Primary scan target forwarded for scope policy construction.
        primary_target_type: Semantic type of *primary_target*.
        extra_targets: Additional scope-allowed targets.
        scope_mode: Scope enforcement mode (``"open"``, ``"balanced"``, ``"ai"``, …).

    Returns:
        A :class:`SubAgentResult` with findings, tool records, and error state.
    """
    from ..display import cyan as _cyan, dim as _dim, green as _green, yellow as _yellow

    spec = SUBAGENT_REGISTRY.get(agent_name)
    if spec is None:
        return SubAgentResult(
            agent_name=agent_name,
            task=task,
            findings="",
            error=f"Unknown subagent: {agent_name}",
        )

    if litellm is None:
        return SubAgentResult(
            agent_name=agent_name,
            task=task,
            findings="",
            error="litellm not available",
        )

    skill_content = (
        load_skill(spec.skill_name) or f"You are the {agent_name} specialist."
    )

    # Filter tools to the agent's declared scope; empty scope = no tools.
    if spec.tool_scope:
        available_tools = [
            t
            for t in all_mcp_tools
            if any(t["function"]["name"].startswith(p) for p in spec.tool_scope)
        ]
    else:
        available_tools = []

    system_prompt = _build_subagent_system_prompt(
        agent_name=agent_name,
        skill_content=skill_content,
        has_tools=bool(available_tools),
    )

    messages: list[dict] = [
        {"role": "system", "content": system_prompt},
        {
            "role": "user",
            "content": (
                f"## Investigation Context\n{context}\n\n"
                f"## Your Task\n{task}\n\n"
                "Run your investigation now. When done, write your findings with confidence tags."
            ),
        },
    ]

    # Build scope policy from explicitly named artifacts in the task/context so
    # the subagent can call tools on what root sent it without being blocked, while
    # still being gated on arbitrary pivots outside its assigned scope.
    _allowed_kinds = {"email", "domain", "ip", "username", "phone", "crypto"}
    task_extras: list[str] = [
        normalize_target_value(obs.value).strip()
        for obs in extract_artifact_observations(
            text=f"{task} {context}", source="subagent_task"
        )
        if obs.kind in _allowed_kinds and normalize_target_value(obs.value).strip()
    ]
    subagent_scope_policy = build_scope_policy(
        primary_target=primary_target,
        primary_type=primary_target_type,
        related_targets=list(extra_targets or []) + task_extras,
        evidence=[],
    )

    sub_confidence_log = ConfidenceLog(enabled=True)
    sub_llm_usage = LLMUsage()
    convo = Conversation(model=model, messages=messages, usage=sub_llm_usage)
    seen_sigs: set[str] = set()

    tools_called: list[str] = []
    tool_call_records: list[dict[str, Any]] = []
    scope_blocks_count: int = 0

    for _round in range(max_rounds):
        try:
            msg = await convo.complete(
                tools=available_tools if available_tools else None,
            )
        except LLMError as exc:
            last = next(
                (
                    m["content"]
                    for m in reversed(convo.history)
                    if m.get("role") == "assistant" and m.get("content")
                ),
                "",
            )
            return SubAgentResult(
                agent_name=agent_name,
                task=task,
                findings=last,
                tools_called=tools_called,
                tool_call_records=tool_call_records,
                scope_blocks=scope_blocks_count,
                error=f"LLM error round {_round}: {exc}",
            )

        assistant_entry: dict[str, Any] = {"role": "assistant"}
        if msg.content:
            assistant_entry["content"] = msg.content

        tool_calls = list(msg.tool_calls or [])

        if not tool_calls:
            convo.append(assistant_entry)
            findings = (msg.content or "").strip()
            if verbose:
                print(
                    f"    {_green('✔')} {_cyan(agent_name)} done — "
                    f"{_round + 1} round(s), {len(tools_called)} tool call(s)"
                )
            return SubAgentResult(
                agent_name=agent_name,
                task=task,
                findings=findings,
                tools_called=tools_called,
                tool_call_records=tool_call_records,
                scope_blocks=scope_blocks_count,
            )

        # Deduplicate within this subagent's session.
        executable = []
        for tc in tool_calls:
            raw_args = parse_tool_call_args(tc) or {}
            args, _ = split_scope_meta_args(raw_args)
            sig = make_tool_call_signature(tc.function.name, args)
            if sig not in seen_sigs:
                seen_sigs.add(sig)
                executable.append(tc)

        if executable:
            assistant_entry["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    },
                }
                for tc in executable
            ]
        convo.append(assistant_entry)

        for tc in executable:
            name = tc.function.name
            raw_args = parse_tool_call_args(tc) or {}
            args, scope_reason = split_scope_meta_args(raw_args)
            tools_called.append(name)
            scope_decision = None

            # Worklog tools are internal agent state — skip scope check.
            if not (name.startswith("osint_todo_") or name.startswith("osint_notes_")):
                scope_decision = await evaluate_tool_scope(
                    tool_name=name,
                    tool_args=args,
                    scope_reason=scope_reason,
                    scope_policy=subagent_scope_policy,
                    scope_mode=scope_mode,
                    model=model,
                    confidence_log=sub_confidence_log,
                    usage=sub_llm_usage,
                )
                if not scope_decision.allow:
                    scope_blocks_count += 1
                    blocked_result = (
                        f"[SCOPE BLOCKED] {scope_decision.reason}. "
                        "Stay focused on identifiers attributable to the subagent task."
                    )
                    tool_call_records.append(
                        {
                            "subagent_round": _round + 1,
                            "tool_name": name,
                            "tool_args": args,
                            "status": "blocked_scope",
                            "scope_decision_allow": False,
                            "scope_decision_code": scope_decision.code,
                            "scope_decision_reason": scope_decision.reason,
                            "scope_ai_score": scope_decision.ai_score,
                            "scope_ai_reason": scope_decision.ai_reason,
                            "scope_ai_evaluation": scope_decision.ai_input,
                            "result_preview": blocked_result[:200],
                        }
                    )
                    if verbose:
                        print(
                            f"    {_yellow('✗')} {_dim(name)} "
                            f"{_yellow('[SCOPE BLOCKED]')} "
                            f"{_dim(scope_decision.reason[:80])}"
                        )
                    convo.append(
                        {
                            "role": "tool",
                            "tool_call_id": tc.id,
                            "name": name,
                            "content": blocked_result,
                        }
                    )
                    continue

            result = await call_mcp_tool(mcp_session, name, args)
            tool_call_records.append(
                {
                    "subagent_round": _round + 1,
                    "tool_name": name,
                    "tool_args": args,
                    "status": "success",
                    "scope_decision_allow": True,
                    "scope_decision_code": "ALLOW_SUBAGENT_TOOL_CALL",
                    "scope_decision_reason": "subagent tool call executed",
                    "scope_ai_score": scope_decision.ai_score
                    if scope_decision
                    else None,
                    "scope_ai_reason": scope_decision.ai_reason
                    if scope_decision
                    else None,
                    "scope_ai_evaluation": scope_decision.ai_input
                    if scope_decision
                    else None,
                    "result_preview": result[:200],
                    "result": result,
                }
            )
            if verbose:
                preview = next(
                    (line.strip() for line in result.splitlines() if line.strip()),
                    "(no output)",
                )
                if len(preview) > 100:
                    preview = preview[:99] + "…"
                print(f"    {_cyan('◆')} {_dim(name)}({_dim(_preview_args(args))})")
                print(f"      {_dim('└─')} {_dim(preview)}")
            convo.append(
                {
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "name": name,
                    "content": result,
                }
            )

    # Max rounds reached — return whatever the last assistant message contained.
    last = next(
        (
            m["content"]
            for m in reversed(convo.history)
            if m.get("role") == "assistant" and m.get("content")
        ),
        "",
    )
    return SubAgentResult(
        agent_name=agent_name,
        task=task,
        findings=last,
        tools_called=tools_called,
        tool_call_records=tool_call_records,
        scope_blocks=scope_blocks_count,
        error=f"max_rounds={max_rounds} reached",
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

__all__ = [
    "SubAgentResult",
    "_build_subagent_system_prompt",
    "_preview_args",
    "run_subagent",
]
