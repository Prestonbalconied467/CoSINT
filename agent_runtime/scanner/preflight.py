"""
agent_runtime/scanner/preflight.py

Pre-execution gate logic for MCP tool calls:
  - Routing hint generation (_ROUTING_RULES, _suggest_routing)
  - Artifact collection from args and raw output (_collect_artifacts)
  - Blocked-call recording (_record_blocked_scope_tool_call)
  - Batch scope classification (apply_scope_preflight)
  - Duplicate detection and batch capping (dedup_and_cap_tool_calls, apply_dedupe_preflight)
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from .case_log import log_scope_decision
from .flow import record_event
from ..display import (
    dim,
    get_phase_label,
    print_phase,
    print_tool_result,
    print_tool_start,
)
from ..mcp_runtime import make_tool_call_signature
from ..models import (
    AgentEvent,
    ArtifactObservation,
    CaseFile,
    ScanStats,
    ToolEvidenceRecord,
)
from ..scope import (
    ScopeDecision,
    ScopePreflightResult,
    classify_scope_preflight,
    parse_tool_call_args,
    split_scope_meta_args,
    summarize_tool_call,
)
from ..targeting import (
    MAX_ARTIFACTS_PER_EVIDENCE,
    extract_artifact_observations,
    infer_target_scope,
)

MAX_EVIDENCE_PREVIEW = 400

_ROUTING_RULES = [
    (("osint_username_",), ("username",), "identity", "username"),
    (("osint_person_",), ("name", "fullname", "full_name"), "identity", "person"),
    (("osint_email_",), ("email",), "identity", "email"),
    (("osint_phone_",), ("phone", "phone_number"), "identity", "phone"),
    (("osint_domain_",), ("domain", "host", "hostname"), "infrastructure", "domain"),
    (("osint_network_",), ("ip", "ip_address"), "infrastructure", "ip"),
    (("osint_geo_",), ("address", "location"), "geo", "address"),
    (
        ("osint_crypto_", "osint_blockchain_", "osint_wallet_"),
        ("wallet", "address", "hash", "tx_hash"),
        "finance",
        "address",
    ),
    (("osint_social_",), ("username", "handle"), "identity", "username"),
    (("osint_leak_",), ("email",), "identity", "email"),
]


def _suggest_routing(tool_name: str, args: dict[str, Any]) -> str:
    for tool_prefixes, arg_keys, subagent, label in _ROUTING_RULES:
        tool_match = any(tool_name.startswith(p) for p in tool_prefixes)
        arg_value = next(
            (
                args.get(k) or args.get(k.replace("_", ""))
                for k in arg_keys
                if isinstance(args.get(k) or args.get(k.replace("_", "")), str)
                and (args.get(k) or args.get(k.replace("_", ""))).strip()
            ),
            "",
        )
        if tool_match or arg_value:
            hint = f"→ dispatch {subagent} subagent"
            if arg_value:
                hint += f" for {label}: {arg_value}"
            return hint
    return ""


def _collect_artifacts(
    *, args: dict[str, Any], raw_output: str, tool_name: str
) -> list[ArtifactObservation]:
    observations = []
    for key, value in sorted(args.items()):
        if isinstance(value, str):
            observations.extend(
                extract_artifact_observations(text=value, source=f"arg:{key}")
            )
    observations.extend(
        extract_artifact_observations(text=raw_output, source=tool_name)
    )
    deduped_obs, seen_obs = [], set()
    for obs in observations:
        obs_key = (obs.kind, obs.value.lower(), obs.source)
        if obs_key in seen_obs:
            continue
        seen_obs.add(obs_key)
        deduped_obs.append(obs)
        if len(deduped_obs) >= MAX_ARTIFACTS_PER_EVIDENCE:
            break
    return deduped_obs


@dataclass
class DedupeCapResult:
    tool_calls: list[Any] | None
    pre_existing_dupes: int
    intra_batch_dupes: int
    capped_count: int


@dataclass
class RuntimeScopePreflightResult:
    scope_preflight: ScopePreflightResult
    blocked_tool_results: list[tuple[str, str, str, str]]
    blocked_feedback_lines: list[str]
    current_phase_label: str
    blocked_domains: set[str]


def _record_blocked_scope_tool_call(
    *,
    tc: Any,
    args: dict[str, Any],
    round_num: int,
    target: str,
    extra_targets: list[str],
    scope_mode: str,
    scope_decision: ScopeDecision,
    case_file: CaseFile,
    stats: ScanStats,
    events: list[AgentEvent],
    event_log_size: int,
    evidence_by_id: dict[str, ToolEvidenceRecord],
    current_phase_label: str,
) -> tuple[str, tuple[str, str, str, str], str]:
    name = tc.function.name
    phase_label = get_phase_label(name)
    if phase_label != current_phase_label:
        current_phase_label = phase_label
        print_phase(phase_label, round_num + 1)
    print_tool_start(name, args)
    started_at = datetime.now(timezone.utc).isoformat()
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
    # allocate an evidence record via CaseFile.add_evidence
    scope_ai_audit = log_scope_decision(
        round_num=round_num,
        source="root",
        tested=summarize_tool_call(name, args),
        scope_decision=scope_decision,
        requested_reason=args.get("reason", ""),
    )
    deduped_obs = _collect_artifacts(args=args, raw_output=result, tool_name=name)
    target_scope = infer_target_scope(
        primary_target=target,
        related_targets=list(extra_targets),
        tool_args=args,
        raw_output=result,
    )
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
        target_scope=target_scope,
        observed_artifacts=deduped_obs,
        scope_mode=scope_mode,
        scope_decision_allow=scope_decision.allow,
        scope_decision_code=scope_decision.code,
        scope_decision_reason=scope_decision.reason,
        scope_ai_audit=scope_ai_audit,
        is_duplicate=False,
        duplicate_of=None,
    )
    eid = case_file.add_evidence(record, subagent=False)
    evidence_by_id[eid] = record
    routing_hint = _suggest_routing(name, args)
    if routing_hint:
        result += f"\n\n[ROUTING NOTE] This tool is typically run inside a subagent. {routing_hint}"
    feedback_line = f"{summarize_tool_call(name, args)}: {scope_decision.reason}"
    return current_phase_label, (tc.id, name, result, eid), feedback_line


async def apply_scope_preflight(
    *,
    tool_calls: list[Any],
    round_num: int,
    target: str,
    target_type: str,
    scope_mode: str,
    extra_targets: list[str],
    case_file: CaseFile,
    stats: ScanStats,
    events: list[AgentEvent],
    event_log_size: int,
    evidence_by_id: dict[str, ToolEvidenceRecord],
    current_phase_label: str,
    approved_domains: set[str] | None = None,
    model: str = "",
    confidence_log=None,
    llm_usage=None,
) -> RuntimeScopePreflightResult:
    from shared.url_utils import extract_domain as _ed

    classified = await classify_scope_preflight(
        tool_calls=tool_calls,
        primary_target=target,
        primary_type=target_type,
        related_targets=list(extra_targets),
        evidence=case_file.evidence_list(),
        scope_mode=scope_mode,
        model=model,
        approved_domains=approved_domains,
        confidence_log=confidence_log,
        usage=llm_usage,
    )
    blocked_tool_results, blocked_feedback_lines = [], []
    for blocked in classified.blocked_calls:
        current_phase_label, blocked_result, feedback_line = (
            _record_blocked_scope_tool_call(
                tc=blocked.tool_call,
                args=blocked.tool_args,
                round_num=round_num,
                target=target,
                extra_targets=extra_targets,
                scope_mode=scope_mode,
                scope_decision=blocked.decision,
                case_file=case_file,
                stats=stats,
                events=events,
                event_log_size=event_log_size,
                evidence_by_id=evidence_by_id,
                current_phase_label=current_phase_label,
            )
        )
        blocked_tool_results.append(blocked_result)
        blocked_feedback_lines.append(feedback_line)

    blocked_domains = set()
    for blocked in classified.blocked_calls:
        for _v in (blocked.tool_args or {}).values():
            if isinstance(_v, str):
                _d = _ed(_v)
                if _d:
                    blocked_domains.add(_d)

    return RuntimeScopePreflightResult(
        scope_preflight=ScopePreflightResult(
            executable_tool_calls=classified.executable_tool_calls,
            allowed_scope_decisions=classified.allowed_scope_decisions,
            blocked_calls=[],
        ),
        blocked_tool_results=blocked_tool_results,
        blocked_feedback_lines=blocked_feedback_lines,
        current_phase_label=current_phase_label,
        blocked_domains=blocked_domains,
    )


def dedup_and_cap_tool_calls(
    *, tool_calls: list[Any], seen_call_signatures: set[str], cap: int
) -> DedupeCapResult:
    deduped, batch_seen = [], set()
    pre_existing_dupes = intra_batch_dupes = 0
    for tc in tool_calls:
        raw_args = parse_tool_call_args(tc)
        args, _ = split_scope_meta_args(raw_args)
        sig = make_tool_call_signature(tc.function.name, args)
        if sig in seen_call_signatures:
            pre_existing_dupes += 1
            continue
        if sig in batch_seen:
            intra_batch_dupes += 1
            continue
        batch_seen.add(sig)
        deduped.append(tc)
    capped_count = 0
    if len(deduped) > cap:
        capped_count = len(deduped) - cap
        deduped = deduped[:cap]
    return DedupeCapResult(
        tool_calls=deduped if deduped else None,
        pre_existing_dupes=pre_existing_dupes,
        intra_batch_dupes=intra_batch_dupes,
        capped_count=capped_count,
    )


def apply_dedupe_preflight(
    *,
    tool_calls: list[Any],
    seen_call_signatures: set[str],
    cap: int,
    stats: ScanStats,
    events: list[AgentEvent],
    event_log_size: int,
    round_num: int,
) -> DedupeCapResult:
    preflight = dedup_and_cap_tool_calls(
        tool_calls=tool_calls,
        seen_call_signatures=seen_call_signatures,
        cap=cap,
    )
    total_dupes = preflight.pre_existing_dupes + preflight.intra_batch_dupes
    if total_dupes:
        stats.tools_deduped += total_dupes
        dedup_msg = (
            f"[tool-call dedup: removed {total_dupes} duplicate(s) before execution"
            f" ({preflight.pre_existing_dupes} already-seen, {preflight.intra_batch_dupes} intra-batch)]"
        )
        print(f"  {dim(dedup_msg)}")
        record_event(
            events,
            event_log_size,
            round_num + 1,
            "tool-dedup-preflight",
            f"removed {total_dupes} dupes (pre-existing={preflight.pre_existing_dupes}, intra={preflight.intra_batch_dupes})",
        )
    if preflight.capped_count:
        remaining = len(preflight.tool_calls) if preflight.tool_calls else 0
        print(
            f"  {dim(f'[tool-call batch capped: keeping {remaining}, dropped {preflight.capped_count}]')}"
        )
        record_event(
            events,
            event_log_size,
            round_num + 1,
            "tool-cap",
            f"capped {preflight.capped_count} calls (cap={cap})",
        )
    return preflight


__all__ = [
    "MAX_EVIDENCE_PREVIEW",
    "DedupeCapResult",
    "RuntimeScopePreflightResult",
    "_collect_artifacts",
    "_record_blocked_scope_tool_call",
    "_suggest_routing",
    "apply_dedupe_preflight",
    "apply_scope_preflight",
    "dedup_and_cap_tool_calls",
]
