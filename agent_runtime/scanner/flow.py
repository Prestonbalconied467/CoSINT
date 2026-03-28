"""
agent_runtime/scanner/flow.py

Flow control helpers for the root scan loop:
  - record_event / capture_worklog_snapshots
  - No-tool action decisions (decide_no_tool_action, decide_max_round_action)
  - Interactive pause handling (handle_pre_execution_pause, handle_interactive_pause)
  - No-tool path (handle_no_tools)
  - Post-loop finalization (finalize_scan, append_case_relation)
  - Prompt builders and utility predicates
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, Final

from shared.config import DEFAULT_MAX_TOOL_CALLS, DEFAULT_MAX_REPORT_GRACE_ROUNDS

from .pivot_tracker import (
    build_continue_pivot_prompt,
    find_unfollowed_pivots,
)
from ..display import interactive_pause, print_pre_report_pause, print_narrative, dim
from ..models import AgentEvent
from ..prompting import looks_like_final_report
from ..targeting import build_relation_summary

if TYPE_CHECKING:
    from mcp import ClientSession
    from .context import ScanContext
    from .tool_calls import RoutingResult
    from ..models import CaseFile

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

MAX_PIVOT_FOLLOWUPS: Final = 3
MAX_AGENT_CHAIN_DEPTH: Final = 3

# Tools whose names start with these prefixes are internal worklog ops —
# they don't count as "real" work for post-execution pause purposes.
_WORKLOG_PREFIXES: Final[tuple[str, ...]] = ("osint_notes_", "osint_todo_")

_REPORT_SECTIONS_NOTE: Final = (
    "## Executive Summary, ## Key Findings (grouped by category, every finding "
    "has a confidence tag + evidence reference), ## Anomalies (every ANOMALY raised; "
    "'none detected' if clean), ## Scope Decisions (summarize allowed/blocked scope "
    "checks with reason codes), ## Evidence Chains (canonical multi-line format: "
    "artifact --[relationship]--> artifact, Source: tool (EV-xxxx), tier T1-T5, "
    "recency, confidence), ## Pivots Taken (with outcome: confirmed/empty/error), "
    "## Subagents Used (which agents ran and what they returned), "
    "## Recommendations (specific tool-level actions — name the tool, artifact, "
    "and platform), ## Tools Used / Skipped."
)

# Compiled once; matched against the tail of agent responses each round.
_QUESTION_RE: Final = re.compile(
    r"\?$"
    r"|\bplease specify\b"
    r"|\bplease (let me know|advise|confirm|indicate)\b"
    r"|\bdo you (want|wish|prefer|need)\b"
    r"|\b(shall i|should i|would you like)\b"
    r"|\b(enter enrichment|proceed to phase|ready to proceed)\b"
    r"|\bawait(ing)? (your|further|operator)\b"
    r"|\b(specify|indicate) if you\b",
    re.IGNORECASE,
)

REPORT_QA_FIELDS: Final = (
    "Investigation mode, Hypothesis verdict (CONFIRMED/REFUTED/INCONCLUSIVE/n/a), "
    "Correlation verdict (verdict + HIGH/MED/LOW / n/a), "
    "Unsupported claims, Confidence overstatements, Contradictions found, "
    "Anomalies flagged, False-positive risks, Missing evidence chains, QA verdict"
)

# ---------------------------------------------------------------------------
# Event logging
# ---------------------------------------------------------------------------


def record_event(
    events: list[AgentEvent],
    max_events: int,
    round_num: int,
    phase: str,
    detail: str,
) -> None:
    """Append an event and trim the log to *max_events* entries.

    Args:
        events: Mutable event list shared across the scan.
        max_events: Maximum number of entries to retain (oldest are dropped).
        round_num: Current scan round number, attached to the new event.
        phase: Short phase label (e.g. ``"directive"``, ``"report-request"``).
        detail: Human-readable description of what happened.
    """
    events.append(AgentEvent(round_num=round_num, phase=phase, detail=detail))
    if len(events) > max_events:
        del events[:-max_events]


# ---------------------------------------------------------------------------
# Worklog snapshots
# ---------------------------------------------------------------------------


async def capture_worklog_snapshots(
    session: ClientSession,
    case_file: CaseFile,
) -> None:
    """Best-effort capture of the todo/notes workspace state from the MCP session.

    Both fields are written only once; subsequent calls are no-ops.  Errors are
    stored as diagnostic strings so callers never have to handle exceptions here.

    Args:
        session: Active MCP client session used to call worklog tools.
        case_file: Case file whose ``todo_snapshot`` / ``notes_snapshot`` fields
            will be populated in-place.
    """
    from ..mcp_runtime import call_mcp_tool  # local import — avoid circular deps

    if case_file.todo_snapshot is None:
        try:
            case_file.todo_snapshot = await call_mcp_tool(
                session, "osint_todo_list", {"status": "all"}
            )
        except Exception as exc:
            case_file.todo_snapshot = f"Todo snapshot unavailable: {exc}"

    if case_file.notes_snapshot is None:
        try:
            case_file.notes_snapshot = await call_mcp_tool(
                session, "osint_notes_list", {"tag": "", "limit": 200}
            )
        except Exception as exc:
            case_file.notes_snapshot = f"Notes snapshot unavailable: {exc}"


# ---------------------------------------------------------------------------
# Case relation helper
# ---------------------------------------------------------------------------


def append_case_relation(ctx: ScanContext) -> None:
    """Flush the current event list and rebuild the relation summary on *ctx*.

    Args:
        ctx: Mutable scan context updated in-place.
    """
    ctx.case_file.events = list(ctx.events)
    ctx.case_file.relation = build_relation_summary(
        primary_target=ctx.target,
        related_targets=list(ctx.extra_targets),
        correlate_targets=ctx.correlate_targets,
        evidence=ctx.case_file.evidence_list(),
    )


# ---------------------------------------------------------------------------
# No-tool action decision
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class NoToolDecision:
    """Immutable result of a no-tool decision step.

    Attributes:
        action: What the scan loop should do next.
        report: Populated when *action* is ``"return_report"``.
        prompt: Populated when *action* is ``"request_report"``; injected as the
            next user-turn message.
    """

    action: Literal["return_report", "request_report", "pause_interactive"]
    report: str | None = None
    prompt: str | None = None


def _build_multi_target_report_note(
    extra_targets: list[str],
    *,
    correlate_targets: bool,
) -> str:
    """Return the extra-target paragraph inserted into report prompts.

    Args:
        extra_targets: Additional targets beyond the primary one.
        correlate_targets: When ``True``, the agent is in correlation-verification
            mode and the note requests a ``## Correlation Assessment`` section.

    Returns:
        An empty string when there are no extra targets; otherwise a sentence
        describing which additional sections are required.
    """
    if not extra_targets:
        return ""
    if correlate_targets:
        return (
            " Include ## Target Profiles (one sub-section per target) and "
            "## Correlation Assessment (shared anchors, conflicts, verdict, reasoning) "
            "BEFORE the Evidence Chains section."
        )
    return (
        " Include ## Subject Identifiers and ## Cross-Identifier Synthesis BEFORE the "
        "Evidence Chains section. Use those sections to show how the provided "
        "identifiers and discovered pivots fit together into one subject profile."
    )


def build_report_prompt(
    extra_targets: list[str],
    correlate_targets: bool,
    *,
    mode: Literal["force", "interactive"] = "force",
) -> str:
    """Build the user-turn message that requests the final report.

    Args:
        extra_targets: Additional targets; passed through to
            :func:`_build_multi_target_report_note`.
        correlate_targets: Correlation-verification flag; forwarded to
            :func:`_build_multi_target_report_note`.
        mode: ``"force"`` produces a hard "no more tools" instruction;
            ``"interactive"`` is softer and leaves a critical-gap escape hatch.

    Returns:
        A fully-formed prompt string ready to inject as a ``user`` message.
    """

    opener = (
        "You have finished collecting evidence. Now write the final report."
        if mode == "force"
        else "The investigator has reviewed the findings."
    )
    tool_note = (
        " Do NOT call any more tools. Write the report now."
        if mode == "force"
        else " Do not call any more tools unless there is a critical gap."
    )
    multi_note = _build_multi_target_report_note(
        extra_targets, correlate_targets=correlate_targets
    )
    return (
        f"{opener} First output the mandatory PRE-REPORT QA block "
        f"({REPORT_QA_FIELDS}). If verdict is FAIL, stop and state what must be "
        f"resolved. Otherwise write the full report with ALL required sections: "
        f"{_REPORT_SECTIONS_NOTE}{multi_note} "
        f"If QA verdict was PASS WITH NOTES add a ## QA Notes section at the end."
        f"{tool_note}"
    )


def decide_no_tool_action(
    *,
    msg_content: str | None,
    interactive_root: bool,
    report_requested: bool,
    extra_targets: list[str],
    correlate_targets: bool,
    report_request_count: int = 0,
) -> NoToolDecision:
    """Decide how to proceed when the agent produced no tool calls.

    Args:
        msg_content: Raw text from the assistant message (may be ``None``).
        interactive_root: Whether the scan is running in interactive (operator)
            mode.  Non-interactive scans always force a report.
        report_requested: Whether a report has already been requested this pass.
            When ``True`` in interactive mode, whatever the agent returned is
            accepted as the final report.
        extra_targets: Forwarded to :func:`build_report_prompt`.
        correlate_targets: Forwarded to :func:`build_report_prompt`.
        report_request_count: How many times a report has already been explicitly
            requested this session.  When this exceeds 2 the agent output is
            accepted verbatim to break potential infinite loops.

    Returns:
        A :class:`NoToolDecision` describing the next action.
    """
    content = msg_content or ""

    if not interactive_root:
        if looks_like_final_report(content):
            return NoToolDecision(action="return_report", report=content)
        if not report_requested:
            # No QA yet — nudge back to investigation, don't force a report
            return NoToolDecision(
                action="request_report", prompt="Continue the investigation..."
            )
        return NoToolDecision(
            action="request_report",
            prompt=build_report_prompt(extra_targets, correlate_targets, mode="force"),
        )

    if report_requested:
        if looks_like_final_report(content):
            return NoToolDecision(action="return_report", report=content)
        # Accept whatever we have after 2 forced attempts — breaks infinite loops.
        # Guard against accepting a blank response (e.g. None → "") as a report.
        if report_request_count >= 2:
            return NoToolDecision(
                action="return_report",
                report=content
                if content.strip()
                else "(report not generated after repeated requests)",
            )
        # QA block came back without the report body — force the full report now.
        # Use mode="force": the operator already confirmed, no soft escape hatch.
        return NoToolDecision(
            action="request_report",
            prompt=build_report_prompt(extra_targets, correlate_targets, mode="force"),
        )

    return NoToolDecision(action="pause_interactive")


def decide_max_round_action(
    *,
    msg_content: str | None,
    extra_targets: list[str],
    correlate_targets: bool,
) -> NoToolDecision:
    """Decide what to do when the root loop hits ``MAX_TOOL_ROUNDS``.

    Always forces report mode — never triggers an interactive pause.

    Args:
        msg_content: Raw text from the last assistant message (may be ``None``).
        extra_targets: Forwarded to :func:`build_report_prompt`.
        correlate_targets: Forwarded to :func:`build_report_prompt`.

    Returns:
        A :class:`NoToolDecision`; if the base decision is ``"request_report"``
        the prompt is extended with a round-limit notice.
    """
    if looks_like_final_report(msg_content or ""):
        return NoToolDecision(action="return_report", report=msg_content or "")
    prompt = build_report_prompt(extra_targets, correlate_targets, mode="force")
    return NoToolDecision(
        action="request_report",
        prompt=f"{prompt} Round limit reached; this is a finalization-only turn. Do not call tools.",
    )


# ---------------------------------------------------------------------------
# Next-hint extraction (interactive pause display)
# ---------------------------------------------------------------------------


def extract_next_hints(content: str | None, max_hints: int = 3) -> list[str]:
    """Extract forward-looking sentences from agent narrative for the pause UI.

    Lines that describe *past* actions (e.g. "Checking…", "Found…") or that
    merely summarise the current phase are filtered out; only lines expressing
    planned next steps are returned.

    Args:
        content: Raw agent narrative text (may be ``None``).
        max_hints: Maximum number of hints to return.

    Returns:
        A deduplicated list of up to *max_hints* forward-looking sentences.
    """
    if not content:
        return []

    hints: list[str] = []
    for raw_line in content.strip().splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if re.match(
            r"^(checking|found|phase complete|phase summary)\b", line, re.IGNORECASE
        ):
            continue
        if not re.search(
            r"\b(next|will|plan|going to|proceed|then|after that|follow-up|"
            r"investigate|examine|look into|pivot)\b",
            line,
            re.IGNORECASE,
        ):
            continue
        if line not in hints:
            hints.append(line)
        if len(hints) >= max_hints:
            break

    return hints


def looks_like_agent_question(content: str | None) -> bool:
    """Return ``True`` if the agent narrative appears to be asking the operator.

    Only the last 800 characters and four non-empty lines of *content* are
    inspected to keep the check cheap.

    Args:
        content: Raw assistant message text (may be ``None``).

    Returns:
        ``True`` when the tail of *content* matches the question regex.
    """
    if not content:
        return False

    tail = content.strip()[-800:]
    lines = [line.strip() for line in tail.splitlines() if line.strip()]
    return any(_QUESTION_RE.search(line) for line in lines[-4:])


# ---------------------------------------------------------------------------
# Interactive pause handler (post-execution)
# ---------------------------------------------------------------------------


async def handle_interactive_pause(
    ctx: ScanContext,
    msg: Any,
    routing: RoutingResult,
    round_tool_names: list[str],
    round_num: int,
) -> bool:
    """Show the interactive pause prompt *after* tool calls have executed.

    Only triggers when real (non-worklog) tools actually ran this round.
    Agent-question interception is handled earlier by
    :func:`handle_pre_execution_pause`.

    Args:
        ctx: Mutable scan context.
        msg: Assistant message returned after tool execution.
        routing: Routing result for the current round.
        round_tool_names: Names of every tool that ran this round.
        round_num: Current round index (0-based).

    Returns:
        ``True`` if the caller should ``continue`` to the next round.
    """
    from .tool_calls import _should_handle_no_tools  # local — avoid circular

    all_worklog = bool(round_tool_names) and all(
        any(t.startswith(p) for p in _WORKLOG_PREFIXES) for t in round_tool_names
    )
    had_real_calls = (
        routing.executable_mcp_calls or routing.approved_subagent_calls
    ) and not all_worklog

    if not had_real_calls:
        return False

    next_hints = extract_next_hints(msg.content)
    directive = interactive_pause(
        last_content=msg.content,
        next_tools=round_tool_names,
        next_hints=next_hints,
    )

    if directive:
        ctx.convo.append(
            {"role": "user", "content": f"[INVESTIGATOR DIRECTIVE] {directive}"}
        )
        ctx.stats.directives_issued += 1
        ctx.directive_pending = True
        record_event(
            ctx.events,
            ctx.event_log_size,
            round_num + 1,
            "directive",
            directive,
        )
        if _should_handle_no_tools(
            routing.executable_mcp_calls,
            routing.approved_subagent_calls,
            routing.blocked_subagent_tool_messages,
        ):
            return True
    else:
        ctx.convo.append(
            {
                "role": "user",
                "content": "Confirmed. Proceed with the planned investigation now.",
            }
        )
        return None
    return False


_QA_VERDICT_RE: Final = re.compile(
    r"qa verdict\s*:\s*(PASS(?:\s+WITH\s+NOTES)?|FAIL)",
    re.IGNORECASE,
)


def extract_qa_verdict(content: str | None) -> str | None:
    """Return 'PASS', 'PASS WITH NOTES', 'FAIL', or None if no QA block found."""
    if not content:
        return None
    m = _QA_VERDICT_RE.search(content)
    if not m:
        return None
    verdict = m.group(1).upper()
    # Normalise
    if verdict.startswith("PASS WITH"):
        return "PASS WITH NOTES"
    return verdict  # 'PASS' or 'FAIL'


def handle_qa_verdict(
    ctx: ScanContext,
    msg: Any,
    qa_verdict: str,
    round_num: int,
) -> bool | None:
    """Process the extracted QA verdict, triggering transitions or remediation.

    This runs immediately after the LLM response is received. It queues up the
    appropriate user-role prompt (report transition, remediation, or interactive
    pause) for the next round.

    Returns:
        ``True`` when ``ctx.report_requested`` was set here — signals the caller
        to set ``ctx.qa_verdict_pending`` so that the *next* LLM call runs with
        ``tools=None`` (forcing report generation rather than more tool calls).
        Returns ``None`` when a directive was given (agent needs tools to follow
        it) or when no state change was made.
    """
    # ── 1. Handle PASS / PASS WITH NOTES ──────────────────────────────────
    if qa_verdict in ("PASS", "PASS WITH NOTES") and not ctx.report_requested:
        if ctx.interactive_root:
            # Print the full QA block before prompting so the operator can
            # actually read the verdict before deciding.  Set the flag so
            # tool_calls.py skips its own narrative render for this round
            # (add: `if not getattr(ctx, "qa_narrative_printed", False)`
            # guard wherever print_narrative(msg.content) is called there).
            print_narrative(msg.content)
            ctx.qa_narrative_printed = True  # type: ignore[attr-defined]
            directive = print_pre_report_pause(None)  # None → no redundant preview
            if directive:
                inject = f"[INVESTIGATOR DIRECTIVE] {directive}"
                ctx.stats.directives_issued += 1
                ctx.directive_pending = True
                record_event(
                    ctx.events,
                    ctx.event_log_size,
                    round_num + 1,
                    "directive",
                    directive,
                )
                ctx.convo.append({"role": "user", "content": inject})
                # Directive given — keep tools available so agent can follow it.
                return None
            else:
                print(f"\n  {dim('Generating report...')}\n")
                # Operator pressed Enter with no directive — wrap up.
                inject = build_report_prompt(
                    ctx.extra_targets, ctx.correlate_targets, mode="force"
                )
                ctx.report_requested = True
                ctx.report_request_count = getattr(ctx, "report_request_count", 0) + 1
                record_event(
                    ctx.events,
                    ctx.event_log_size,
                    round_num + 1,
                    "report-request",
                    "operator triggered",
                )
                ctx.convo.append({"role": "user", "content": inject})
                return True  # next LLM call should use tools=None

        else:
            # Non-interactive PASS: Auto-transition to report.
            ctx.report_requested = True
            ctx.report_request_count = getattr(ctx, "report_request_count", 0) + 1
            record_event(
                ctx.events,
                ctx.event_log_size,
                round_num + 1,
                "report-request",
                f"QA verdict {qa_verdict} — auto-transitioning to report",
            )
            ctx.convo.append(
                {
                    "role": "user",
                    "content": build_report_prompt(
                        ctx.extra_targets, ctx.correlate_targets, mode="force"
                    ),
                }
            )
            return True  # next LLM call should use tools=None

    # ── 2. Handle FAIL ────────────────────────────────────────────────────
    elif qa_verdict == "FAIL":
        record_event(
            ctx.events,
            ctx.event_log_size,
            round_num + 1,
            "qa-fail",
            "QA verdict FAIL — pausing for operator"
            if ctx.interactive_root
            else "QA verdict FAIL — re-requesting report with remediation note",
        )

        if not ctx.interactive_root:
            # Non-interactive FAIL: Auto-inject remediation note.
            ctx.convo.append(
                {
                    "role": "user",
                    "content": (
                        "The PRE-REPORT QA check returned FAIL. "
                        "Address every issue flagged in the QA block before writing the report. "
                        "If missing evidence requires additional tool calls, run them now."
                    ),
                }
            )
            return None  # keep tools available for remediation

        else:
            # Interactive FAIL: show full QA block before prompting operator.
            print_narrative(msg.content)
            ctx.qa_narrative_printed = True  # type: ignore[attr-defined]
            directive = print_pre_report_pause(None)  # None → no redundant preview
            if directive:
                inject = f"[INVESTIGATOR DIRECTIVE] {directive}"
                ctx.stats.directives_issued += 1
                ctx.directive_pending = True
                record_event(
                    ctx.events,
                    ctx.event_log_size,
                    round_num + 1,
                    "directive",
                    directive,
                )
                ctx.convo.append({"role": "user", "content": inject})
                # Directive given — agent may need tools to gather missing evidence.
                return None
            else:
                # Operator pressed Enter on a FAIL — force fix + report.
                inject = (
                    "The PRE-REPORT QA check returned FAIL. "
                    "Address every issue flagged in the QA block, then "
                    + build_report_prompt(
                        ctx.extra_targets, ctx.correlate_targets, mode="force"
                    )
                )
                ctx.report_requested = True
                ctx.report_request_count = getattr(ctx, "report_request_count", 0) + 1
                record_event(
                    ctx.events,
                    ctx.event_log_size,
                    round_num + 1,
                    "report-request",
                    "operator triggered on FAIL",
                )
                ctx.convo.append({"role": "user", "content": inject})
                return True  # next LLM call should use tools=None

    # No state change (e.g. report already requested when PASS arrived).
    return None


# ---------------------------------------------------------------------------
# No-tool path
# ---------------------------------------------------------------------------


def handle_no_tools(
    ctx: ScanContext,
    msg: Any,
    routing: RoutingResult,
    round_num: int,
    qa_newly_handled: bool = False,
) -> str | None:
    """Handle a round in which the agent produced no executable tool calls.

    Drives pivot follow-up, report requests, interactive pauses, and directive
    injection as appropriate.

    Args:
        ctx: Mutable scan context.
        msg: Assistant message from this round.
        routing: Routing result (may contain only blocked/rejected calls).
        round_num: Current round index (0-based).

    Returns:
        A report string when the scan should terminate, ``None`` to continue
        the loop.
    """
    from .tool_calls import _should_handle_no_tools  # local — avoid circular

    if not _should_handle_no_tools(
        routing.executable_mcp_calls,
        routing.approved_subagent_calls,
        routing.blocked_subagent_tool_messages,
    ):
        return None

    append_case_relation(ctx)

    # ── Upstream Guard ────────────────────────────────────────────────────
    # If a QA block was output in THIS message, handle_qa_verdict already
    # processed the state changes and appended the next steps. We exit
    # early to avoid double-prompting the LLM.
    if qa_newly_handled and not looks_like_final_report(msg.content):
        return None

    # ── Pivot Follow-up ───────────────────────────────────────────────────
    pending_pivots: list[tuple[str, str]] = []
    if not ctx.report_requested:
        pending_pivots = find_unfollowed_pivots(evidence=ctx.case_file.evidence_list())

    if pending_pivots and ctx.pivot_followup_requests < MAX_PIVOT_FOLLOWUPS:
        ctx.pivot_followup_requests += 1
        record_event(
            ctx.events,
            ctx.event_log_size,
            round_num + 1,
            "pivot-followup",
            ", ".join(f"{kind}:{value}" for kind, value in pending_pivots),
        )
        ctx.convo.append(
            {"role": "user", "content": build_continue_pivot_prompt(pending_pivots)}
        )
        return None

    # ── General No-Tool Routing ───────────────────────────────────────────
    no_tool = decide_no_tool_action(
        msg_content=msg.content,
        interactive_root=ctx.interactive_root,
        report_requested=ctx.report_requested,
        extra_targets=ctx.extra_targets,
        correlate_targets=ctx.correlate_targets,
        report_request_count=getattr(ctx, "report_request_count", 0),
    )

    if no_tool.action == "return_report":
        if looks_like_final_report(no_tool.report or ""):
            print(f"\n  {dim('Report complete. Saving...')}\n")
            return no_tool.report or "(no report generated)"

        ctx.report_requested = True
        print(f"\n  {dim('Report structure incomplete — retrying...')}\n")

        ctx.report_request_count = getattr(ctx, "report_request_count", 0) + 1
        record_event(
            ctx.events,
            ctx.event_log_size,
            round_num + 1,
            "report-request",
            "return_report action but report failed structure check — re-requesting",
        )
        ctx.convo.append(
            {
                "role": "user",
                "content": build_report_prompt(
                    ctx.extra_targets, ctx.correlate_targets, mode="force"
                ),
            }
        )
        return None

    if no_tool.action == "request_report":
        ctx.report_requested = True
        print(f"\n  {dim('Requesting final report...')}\n")
        ctx.report_request_count = getattr(ctx, "report_request_count", 0) + 1
        record_event(
            ctx.events, ctx.event_log_size, round_num + 1, "report-request", "forced"
        )
        ctx.convo.append(
            {"role": "user", "content": no_tool.prompt or "Write the final report now."}
        )
        return None

    # ── Interactive Pause / Report Trigger ────────────────────────────────
    if ctx.already_paused:
        ctx.already_paused = False
        return None

    directive = interactive_pause(
        last_content=msg.content,
        tools_ran=False,
    )
    if directive:
        inject: str = f"[INVESTIGATOR DIRECTIVE] {directive}"
        ctx.stats.directives_issued += 1
        ctx.directive_pending = True
        record_event(
            ctx.events, ctx.event_log_size, round_num + 1, "directive", directive
        )
    else:
        ctx.convo.append(
            {
                "role": "user",
                "content": "Confirmed. Proceed with the planned investigation now.",
            }
        )
        return None
    ctx.convo.append({"role": "user", "content": inject})
    return None


# ---------------------------------------------------------------------------
# Post-loop finalization
# ---------------------------------------------------------------------------


def _teardown_and_return(
    ctx: ScanContext,
    report: str,
) -> tuple[str, Any, Any, Any]:
    """Flush LLM usage into ``ctx.usage`` and return the standard scan-result tuple.

    Args:
        ctx: Scan context whose ``llm_usage`` will be merged into ``usage``.
        report: The final report string.

    Returns:
        A ``(report, case_file, usage, stats)`` tuple.
    """
    ctx.llm_usage.merge_into(ctx.usage)
    return report, ctx.case_file, ctx.usage, ctx.stats


async def finalize_scan(
    ctx: ScanContext,
    agent_chain_depth: int,
    use_confidence_log: bool,
) -> tuple[str | None, Any, Any, Any]:
    """Attempt grace-round report extraction, agent chaining, then stub report.

    Called after the main loop exhausts ``DEFAULT_MAX_TOOL_CALLS`` rounds.
    Tries up to ``DEFAULT_MAX_REPORT_GRACE_ROUNDS`` additional completions
    before falling back to agent chaining or a minimal stub report.

    Args:
        ctx: Mutable scan context carrying all shared state.
        agent_chain_depth: How many times the agent has already been chained;
            compared against ``MAX_AGENT_CHAIN_DEPTH`` to prevent infinite loops.
        use_confidence_log: Forwarded verbatim when spawning a chained agent.

    Returns:
        A ``(report, case_file, usage, stats)`` tuple.  *report* is ``None``
        only in the (unreachable in practice) case where agent chaining itself
        returns ``None``.
    """
    append_case_relation(ctx)

    for attempt in range(DEFAULT_MAX_REPORT_GRACE_ROUNDS):
        max_round = decide_max_round_action(
            msg_content=ctx.last_assistant_content,
            extra_targets=ctx.extra_targets,
            correlate_targets=ctx.correlate_targets,
        )

        if max_round.action == "return_report" and looks_like_final_report(
            max_round.report or ""
        ):
            print(f"\n  {dim('Report complete. Saving...')}\n")
            await capture_worklog_snapshots(ctx.session, ctx.case_file)
            return _teardown_and_return(
                ctx, max_round.report or "(no report generated)"
            )

        if max_round.action != "request_report":
            break

        round_label = DEFAULT_MAX_TOOL_CALLS + attempt + 1
        print(
            f"\n  {dim(f'Round limit reached — finalizing report (attempt {attempt + 1})...')}\n"
        )
        record_event(
            ctx.events,
            ctx.event_log_size,
            round_label,
            "report-request",
            f"max-round attempt {attempt + 1}",
        )
        ctx.convo.append(
            {
                "role": "user",
                "content": max_round.prompt or "Write the final report now.",
            }
        )
        ctx.stats.rounds += 1
        msg = await ctx.convo.complete(tools=None)
        ctx.last_assistant_content = msg.content
        ctx.convo.append({"role": "assistant", "content": msg.content})
        if msg.content and msg.content.strip():
            print_narrative(msg.content)
        if looks_like_final_report(msg.content or ""):
            print(f"\n  {dim('Report complete. Saving...')}\n")
            await capture_worklog_snapshots(ctx.session, ctx.case_file)
            return _teardown_and_return(ctx, msg.content or "(no report generated)")

    # Agent chaining — carry all state into a fresh loop pass.
    if agent_chain_depth < MAX_AGENT_CHAIN_DEPTH:
        from .scanner import run_scan  # local — avoid circular import

        print(
            f"\n  {dim(f'Continuing investigation (pass {agent_chain_depth + 1})...')}\n"
        )

        record_event(
            ctx.events,
            ctx.event_log_size,
            ctx.stats.rounds + 1,
            "agent-chain",
            f"Spawning agent chain depth {agent_chain_depth + 1}",
        )
        return await run_scan(
            session=ctx.session,
            target=ctx.target,
            target_type=ctx.target_type,
            depth=ctx.depth,
            model=ctx.model,
            verbose=ctx.verbose,
            instruction=ctx.instruction,
            hypothesis=ctx.hypothesis,
            extra_targets=ctx.extra_targets,
            correlate_targets=ctx.correlate_targets,
            policy_flags=ctx.policy_flags,
            interactive_root=ctx.interactive_root,
            max_context_tokens=ctx.max_context_tokens,
            compression_threshold=ctx.compression_threshold,
            event_log_size=ctx.event_log_size,
            scope_mode=ctx.scope_mode,
            max_tool_calls=ctx.max_tool_calls,
            use_confidence_log=use_confidence_log,
            agent_chain_depth=agent_chain_depth + 1,
            case_file=ctx.case_file,
            usage=ctx.usage,
            stats=ctx.stats,
            llm_usage=ctx.llm_usage,
            confidence_log=ctx.confidence_log,
            evidence_by_id=ctx.evidence_by_id,
            events=ctx.events,
            cached_call_results=ctx.cached_call_results,
            cached_evidence_ids=ctx.cached_evidence_ids,
            seen_call_signatures=ctx.seen_call_signatures,
            confidence_approved_domains=ctx.confidence_approved_domains,
            _scope_blocked_domains=ctx.scope_blocked_domains,
        )
    print(f"\n  {dim('Max rounds reached — saving partial results...')}\n")

    await capture_worklog_snapshots(ctx.session, ctx.case_file)
    stub_report = (
        "## Executive Summary\n(max rounds reached - partial results above)\n"
        "## Key Findings\nnone\n"
        "## Evidence Chains\nnone\n"
        "## Pivots Taken\nnone\n"
        "## Scope Decisions\nnone\n"
        "## Recommendations\nnone\n"
        "## Tools Used\nnone\n"
    )
    return _teardown_and_return(ctx, stub_report)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

__all__ = [
    # Constants
    "MAX_PIVOT_FOLLOWUPS",
    "MAX_AGENT_CHAIN_DEPTH",
    "REPORT_QA_FIELDS",
    # Core helpers
    "record_event",
    "capture_worklog_snapshots",
    "append_case_relation",
    # Decision types and functions
    "NoToolDecision",
    "decide_no_tool_action",
    "decide_max_round_action",
    # Prompt builders
    "build_report_prompt",
    # Loop step functions
    "handle_interactive_pause",
    "handle_no_tools",
    "extract_qa_verdict",
    "finalize_scan",
    # Predicates and utilities
    "extract_next_hints",
    "looks_like_agent_question",
    "handle_qa_verdict",
]
