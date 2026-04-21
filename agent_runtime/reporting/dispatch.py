"""
agent_runtime/reporting/dispatch.py

report_synthesizer dispatch path and fallback detection.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import TYPE_CHECKING

from ..prompting import looks_like_final_report
from ..investigation.events import capture_worklog_snapshots, record_event

if TYPE_CHECKING:
    from ..scanner.context import ScanContext

MAX_REPORT_CONTEXT_EVIDENCE_LINES = 20
MAX_REPORT_CONTEXT_PREVIEW_CHARS = 220
MAX_REPORT_CONTEXT_NOTES_CHARS = 3_500
MAX_REPORT_CHAIN_DRAFTS = 24
MAX_CHAIN_SEEDS_PER_EVIDENCE = 3
MAX_CHAIN_OBS_PER_EVIDENCE = 6


def _single_line(text: str) -> str:
    return " ".join((text or "").split())


def _normalize_value(value: str) -> str:
    return _single_line(value).strip().lower()


def _build_chain_drafts(ctx: "ScanContext") -> list[str]:
    drafts: list[str] = []
    seen: set[tuple[str, str, str, str]] = set()

    for ev in ctx.case_file.evidence_list():
        ev_id = ev.evidence_id or "(no-ev-id)"
        seeds = [s for s in (ev.target_scope or []) if _normalize_value(s)]
        if not seeds:
            seeds = [ctx.target]
        seeds = seeds[:MAX_CHAIN_SEEDS_PER_EVIDENCE]

        observations = list(ev.observed_artifacts or [])[:MAX_CHAIN_OBS_PER_EVIDENCE]
        if not observations:
            continue

        for seed in seeds:
            seed_norm = _normalize_value(seed)
            if not seed_norm:
                continue
            for obs in observations:
                dst = _single_line(obs.value)
                dst_norm = _normalize_value(dst)
                if not dst_norm or dst_norm == seed_norm:
                    continue

                relation = f"observed_{obs.kind}" if obs.kind else "related_to"
                dedupe_key = (seed_norm, relation, dst_norm, ev_id)
                if dedupe_key in seen:
                    continue
                seen.add(dedupe_key)

                confidence = "MED" if (ev.status or "").lower() == "success" else "LOW"
                drafts.append(
                    f"{seed} --[{relation}]--> {dst}\n"
                    f"  Source: {ev.tool_name} (Evidence: {ev_id})\n"
                    "  Source tier: T3\n"
                    "  Recency: STALE/UNCERTAIN\n"
                    f"  Confidence: {confidence}"
                )

                if len(drafts) >= MAX_REPORT_CHAIN_DRAFTS:
                    return drafts

    return drafts


def build_report_subagent_context(ctx: "ScanContext") -> str:
    evidence = ctx.case_file.recent_evidence(MAX_REPORT_CONTEXT_EVIDENCE_LINES)
    evidence_lines: list[str] = []
    for ev in evidence:
        ev_id = ev.evidence_id or "(no-ev-id)"
        preview = _single_line(ev.result_preview)[:MAX_REPORT_CONTEXT_PREVIEW_CHARS]
        evidence_lines.append(
            f"- {ev_id} | {ev.tool_name} | status={ev.status} | preview={preview}"
        )

    notes_snapshot = ctx.case_file.notes_snapshot or ""
    notes_preview = notes_snapshot[:MAX_REPORT_CONTEXT_NOTES_CHARS].strip()
    notes_block = notes_preview if notes_preview else "(notes unavailable or empty)"
    chain_drafts = _build_chain_drafts(ctx)
    chain_block = "\n\n".join(chain_drafts) if chain_drafts else "- none"

    activated = ", ".join(ctx.stats.subagents_activated) or "none"
    return (
        f"Primary target: {ctx.target} ({ctx.target_type})\n"
        f"Depth: {ctx.depth}\n"
        f"Related targets: {', '.join(ctx.extra_targets) if ctx.extra_targets else 'none'}\n"
        f"Correlate mode: {ctx.correlate_targets}\n"
        f"Rounds executed: {ctx.stats.rounds}\n"
        f"Tools run: {ctx.stats.tools_run}\n"
        f"Pivots found: {ctx.stats.pivots_found}\n"
        f"Subagents activated: {activated}\n"
        f"Evidence count: {len(ctx.case_file.evidence_list())}\n\n"
        "Case Evidence Snapshot (most recent first):\n"
        + ("\n".join(reversed(evidence_lines)) if evidence_lines else "- none")
        + "\n\n"
        "Investigator Notes Snapshot:\n"
        f"{notes_block}\n\n"
        "Draft Evidence Chains (auto-generated from case evidence, refine before final output):\n"
        f"{chain_block}\n"
    )


async def maybe_generate_report_via_subagent(
    ctx: "ScanContext",
    round_num: int,
) -> str | None:
    if not ctx.report_requested or ctx.report_subagent_attempted:
        return None

    ctx.report_subagent_attempted = True
    await capture_worklog_snapshots(ctx.session, ctx.case_file)

    from ..subagents import append_subagent_call_records, dispatch_subagent

    task = (
        "Write the final investigation report from the completed case context. "
        "Use all required sections and include QA Notes only when QA verdict is PASS WITH NOTES. "
        "You MUST ground findings in the Case Evidence Snapshot and Notes Snapshot provided in context. "
        "Use Draft Evidence Chains as starting material, then refine them into final chains. "
        "If evidence entries are present, do not claim that no evidence was found."
    )
    context = build_report_subagent_context(ctx)

    auto_call_id = f"auto-call-subagent-report-{round_num + 1}"
    auto_tc = SimpleNamespace(id=auto_call_id)
    auto_args = {
        "agent": "report_synthesizer",
        "task": task,
        "context": context,
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

    record_event(
        ctx.events,
        ctx.event_log_size,
        round_num + 1,
        "subagent-auto-dispatch",
        "report_synthesizer: final report generation",
    )

    subagent_result, tool_message = await dispatch_subagent(
        tc=auto_tc,
        agent_name="report_synthesizer",
        task=task,
        context=context,
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

    ctx.root.record_result(subagent_result)
    ctx.stats.subagents_activated.append("report_synthesizer")
    append_subagent_call_records(
        ctx,
        round_num=round_num,
        agent_name="report_synthesizer",
        tool_call_records=subagent_result.tool_call_records,
        raw_output=tool_message.get("content"),
    )
    ctx.convo.append(tool_message)

    report_candidate = (subagent_result.findings or "").strip()
    if subagent_result.error or not looks_like_final_report(report_candidate):
        ctx.report_subagent_failed = True
        if subagent_result.error:
            reason = subagent_result.error
        elif not report_candidate:
            reason = "empty report output"
        else:
            reason = "report structure validation failed"
        record_event(
            ctx.events,
            ctx.event_log_size,
            round_num + 1,
            "report-subagent-fallback",
            reason,
        )
        return None

    record_event(
        ctx.events,
        ctx.event_log_size,
        round_num + 1,
        "report-subagent-complete",
        "final report accepted from report_synthesizer",
    )
    return report_candidate


__all__ = ["build_report_subagent_context", "maybe_generate_report_via_subagent"]

