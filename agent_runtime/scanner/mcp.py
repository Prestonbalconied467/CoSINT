"""
agent_runtime/scanner/mcp.py

Execution of a single MCP tool call and evidence recording:
  - Browser-tool interactive flag injection (BROWSER_BASED_TOOLS, _is_browser_tool)
  - Scope inclusion bookkeeping (_maybe_add_scope_inclusion)
  - Full tool call batch execution loop (execute_tool_call_batch)
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from .case_log import log_artifact_ratings, log_scope_decision
from .flow import record_event
from .preflight import MAX_EVIDENCE_PREVIEW, _collect_artifacts
from ..display import (
    dim,
    get_phase_label,
    print_phase,
    print_tool_result,
    print_tool_start,
)
from ..mcp_runtime import call_mcp_tool, make_tool_call_signature
from ..models import AgentEvent, CaseFile, ScanStats, ScopeInclusion, ToolEvidenceRecord
from ..scope import (
    ScopeDecision,
    ScopePreflightResult,
    build_scope_policy,
    evaluate_tool_scope,
    find_source_evidence,
    is_internal_worklog_tool,
    parse_tool_call_args,
    rate_artifacts_for_scope,
    split_scope_meta_args,
    summarize_tool_call,
)
from ..targeting import infer_target_scope

# List of browser-based tool names that accept the interactive flag
BROWSER_BASED_TOOLS = {
    "osint_web_search",
    "osint_web_dork",
    "osint_media_reverse_image_search",
    # Add more browser-based tool names here as needed
}


def _is_browser_tool(tool_name: str) -> bool:
    return tool_name in BROWSER_BASED_TOOLS


def _maybe_add_scope_inclusion(
    case_file: CaseFile,
    args: dict[str, Any],
    scope_decision: ScopeDecision,
) -> None:
    """Infer value/kind from tool args and append a ScopeInclusion if new and allowed."""
    value = ""
    kind = ""
    for key, val in args.items():
        if not isinstance(key, str) or not val:
            continue
        key_lower = key.strip().lower()
        if not value and "domain" in key_lower:
            value, kind = str(val), "domain"
        elif not value and "email" in key_lower:
            value, kind = str(val), "email"
        elif not value and "username" in key_lower:
            value, kind = str(val), "username"
        elif not value and "phone" in key_lower:
            value, kind = str(val), "phone"
    if kind == "domain" and scope_decision.code == "ALLOW_IDENTIFIER_MATCH":
        kind = ""  # suppress
    inclusion = ScopeInclusion(
        value=value, kind=kind, reason=scope_decision.reason or "allowed_by_policy"
    )
    if (
        inclusion.value
        and inclusion.kind
        and not any(
            (i.value == inclusion.value and i.kind == inclusion.kind)
            for i in case_file.scope_inclusions
        )
    ):
        case_file.scope_inclusions.append(inclusion)


@dataclass
class ToolCallBatchResult:
    tool_results: list[tuple[str, str, str, str]]
    current_phase_label: str


async def execute_tool_call_batch(
    *,
    session: Any,
    tool_calls: list[Any],
    round_num: int,
    verbose: bool,
    target: str,
    target_type: str,
    scope_mode: str,
    extra_targets: list[str],
    case_file: CaseFile,
    stats: ScanStats,
    events: list[AgentEvent],
    event_log_size: int,
    seen_call_signatures: set[str],
    cached_call_results: dict[str, str],
    cached_evidence_ids: dict[str, str],
    evidence_by_id: dict[str, ToolEvidenceRecord],
    current_phase_label: str,
    approved_domains: set[str] | None = None,
    model: str = "",
    confidence_log=None,
    llm_usage=None,
    allowed_scope_decisions: dict[int, ScopeDecision] | None = None,
    interactive_root: bool = False,
) -> ToolCallBatchResult:
    scoped = ScopePreflightResult(
        executable_tool_calls=tool_calls or [],
        allowed_scope_decisions=allowed_scope_decisions or {},
        blocked_calls=[],
    )
    tool_results = []
    scope_policy = build_scope_policy(
        primary_target=target,
        primary_type=target_type,
        related_targets=list(extra_targets),
        evidence=case_file.evidence_list(),
        approved_domains=approved_domains,
    )
    for tc in scoped.executable_tool_calls:
        fn = tc.function
        name = fn.name
        raw_args = parse_tool_call_args(tc)
        args, scope_reason = split_scope_meta_args(raw_args)

        # Inject interactive=True for browser-based tools if not already set
        if (
            _is_browser_tool(name)
            and interactive_root
            and not args.get("interactive", False)
        ):
            args["interactive"] = True
            if hasattr(fn, "arguments") and isinstance(fn.arguments, str):
                try:
                    _args_dict = json.loads(fn.arguments)
                    _args_dict["interactive"] = True
                    fn.arguments = json.dumps(_args_dict)
                except Exception:
                    pass

        phase_label = get_phase_label(name)
        if phase_label != current_phase_label:
            current_phase_label = phase_label
            print_phase(phase_label, round_num + 1)

        signature = make_tool_call_signature(name=name, args=args)
        is_duplicate = signature in seen_call_signatures
        print_tool_start(name, args)
        started_at = datetime.now(timezone.utc).isoformat()
        duration_ms = 0
        duplicate_of = cached_evidence_ids.get(signature)

        scope_decision = scoped.allowed_scope_decisions.get(id(tc))
        if scope_decision is None:
            source_evidence_context = find_source_evidence(
                args, case_file.evidence_list()
            )
            scope_decision = await evaluate_tool_scope(
                tool_name=name,
                tool_args=args,
                scope_reason=scope_reason,
                scope_policy=scope_policy,
                scope_mode=scope_mode,
                model=model,
                source_evidence_context=source_evidence_context,
                confidence_log=confidence_log,
                usage=llm_usage,
            )

        if not scope_decision.allow:
            result = (
                f"[SCOPE BLOCKED] {scope_decision.reason}. "
                "Stay focused on identifiers attributable to the investigation target."
            )
            print_tool_result(result)
            stats.tools_blocked += 1
            record_event(
                events,
                event_log_size,
                round_num + 1,
                "tool-blocked",
                f"{name}: {scope_decision.reason}",
            )
            log_scope_decision(
                round_num=round_num,
                source="root",
                tested=summarize_tool_call(name, args),
                scope_decision=scope_decision,
                requested_reason=args.get("reason", ""),
            )
            # construct record without an id; CaseFile.add_evidence will allocate one
            record = ToolEvidenceRecord(
                round_num=round_num + 1,
                phase=phase_label,
                tool_name=name,
                tool_args=args,
                status="blocked_scope",
                started_at=started_at,
                duration_ms=0,
                result_preview=result[:MAX_EVIDENCE_PREVIEW],
                raw_output=result,
                target_scope=[],
                observed_artifacts=[],
                scope_mode=scope_mode,
                scope_decision_allow=False,
                scope_decision_code=scope_decision.code,
                scope_decision_reason=scope_decision.reason,
                is_duplicate=False,
                duplicate_of=None,
            )
            eid = case_file.add_evidence(record, subagent=False)
            evidence_by_id[eid] = record
            tool_results.append((tc.id, name, result, eid))
            continue  # skip call_mcp_tool entirely

        if is_duplicate:
            cached = cached_call_results[signature]
            evidence_ref = duplicate_of or "(unknown)"
            result = (
                f"[ALREADY CALLED] This exact call ({name}) with these arguments was already executed "
                f"earlier in this investigation (CASE EVIDENCE {evidence_ref}). "
                f"Do not call this tool again with the same arguments."
            )
            print_tool_result(cached, is_duplicate=True)
            record_event(
                events, event_log_size, round_num + 1, "tool-dedupe", f"{name} cached"
            )
            raw_output = ""
            preview_text = result
            evidence_status = "duplicate"
        else:
            started_perf = time.perf_counter()
            result = await call_mcp_tool(session, name, args)
            duration_ms = int((time.perf_counter() - started_perf) * 1000)
            print_tool_result(result)
            stats.tools_run += 1
            record_event(events, event_log_size, round_num + 1, "tool-run", name)
            seen_call_signatures.add(signature)
            cached_call_results[signature] = result
            raw_output = result
            preview_text = result
            evidence_status = (
                "error" if result.startswith(f"Tool error ({name})") else "success"
            )
            if verbose:
                raw_preview = result[:MAX_EVIDENCE_PREVIEW].replace("\n", "  ")
                print(f"       {dim('raw output: ' + raw_preview)}")

        # allocate id later via CaseFile.add_evidence; construct record with empty id
        scope_ai_audit = log_scope_decision(
            round_num=round_num,
            source="root",
            tested=summarize_tool_call(name, args),
            scope_decision=scope_decision,
            requested_reason=args.get("reason", ""),
        )
        source_record = (
            evidence_by_id.get(duplicate_of)
            if (is_duplicate and duplicate_of)
            else None
        )

        if evidence_status == "duplicate":
            deduped_obs = []
            target_scope = []
        elif source_record is not None:
            deduped_obs = list(source_record.observed_artifacts)
            target_scope = list(source_record.target_scope)
        else:
            deduped_obs = _collect_artifacts(
                args=args, raw_output=raw_output, tool_name=name
            )
            target_scope = infer_target_scope(
                primary_target=target,
                related_targets=list(extra_targets),
                tool_args=args,
                raw_output=raw_output,
            )
            # In ai mode, rate every output artifact for attribution confidence.
            # Artifacts that don't clear the threshold keep scope_approved=False so
            # build_scope_policy never promotes them — but they stay in the evidence
            # record for audit.
            # Worklog tools (notes, todo) are internal agent state — their output
            # artifacts are never investigation targets and must not be rated.
            if (
                scope_mode == "ai"
                and deduped_obs
                and model
                and not is_internal_worklog_tool(name)
            ):
                rateable = [
                    (obs.kind, obs.value)
                    for obs in deduped_obs
                    if not obs.source.startswith("arg:")
                ]
                if rateable:
                    ratings = await rate_artifacts_for_scope(
                        artifacts=rateable,
                        scope_policy=scope_policy,
                        findings_excerpt=raw_output,
                        model=model,
                        round_num=round_num,
                        subagent_name=f"root:{name}",
                        confidence_log=confidence_log,
                        usage=llm_usage,
                        mode=scope_mode,
                    )
                    # Build a fast lookup: (kind, value.lower()) -> approved
                    approved_map = {
                        (r["kind"], r["value"].lower()): r["approved"] for r in ratings
                    }
                    for obs in deduped_obs:
                        if obs.source.startswith("arg:"):
                            continue
                        key = (obs.kind, obs.value.lower())
                        if key in approved_map:
                            obs.scope_approved = approved_map[key]
                    log_artifact_ratings(
                        confidence_log, ratings=ratings, round_num=round_num
                    )

        stored_raw_output = (
            raw_output
            if not is_duplicate
            else f"[duplicate output omitted; see {duplicate_of or 'prior evidence'}]"
        )
        if is_internal_worklog_tool(name):
            # Don't store worklog ops as investigation evidence
            tool_results.append((tc.id, name, result, ""))
            continue
        record = ToolEvidenceRecord(
            round_num=round_num + 1,
            phase=phase_label,
            tool_name=name,
            tool_args=args,
            status=evidence_status,
            started_at=started_at,
            duration_ms=duration_ms,
            result_preview=preview_text[:MAX_EVIDENCE_PREVIEW],
            raw_output=stored_raw_output,
            target_scope=target_scope,
            observed_artifacts=deduped_obs,
            scope_mode=scope_mode,
            scope_decision_allow=scope_decision.allow,
            scope_decision_code=scope_decision.code,
            scope_decision_reason=scope_decision.reason,
            scope_ai_audit=scope_ai_audit,
            is_duplicate=is_duplicate,
            duplicate_of=duplicate_of,
        )
        eid = case_file.add_evidence(record, subagent=False)
        evidence_by_id[eid] = record
        if not is_duplicate:
            cached_evidence_ids[signature] = eid
        tool_results.append((tc.id, name, result, eid))
        _maybe_add_scope_inclusion(case_file, args, scope_decision)

    return ToolCallBatchResult(
        tool_results=tool_results, current_phase_label=current_phase_label
    )


__all__ = [
    "BROWSER_BASED_TOOLS",
    "ToolCallBatchResult",
    "_is_browser_tool",
    "_maybe_add_scope_inclusion",
    "execute_tool_call_batch",
]
