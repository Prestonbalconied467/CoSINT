"""
agent_runtime/subagents/dispatch_records.py

Scope-promotion parsing and case-record persistence for subagent runs.
"""

from __future__ import annotations

import re as _re

from ..models import ArtifactObservation, CaseFile, ToolEvidenceRecord
from ..scanner.case_log import log_artifact_promotion
from ..execution.preflight import MAX_EVIDENCE_PREVIEW
from ..scope import is_internal_worklog_tool

MAX_SUBAGENT_RAW_OUTPUT_CHARS = 2_000


def parse_scope_promote_block(
    findings: str,
    agent_name: str,
    round_num: int,
    case_file: CaseFile,
    evidence_by_id: dict,
    confidence_approved_domains: set,
    confidence_log: object = None,
) -> list[tuple[str, str, str]]:
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

    line_re = _re.compile(r"^\s*(\w+):\s*(.+?)\s+\[(HIGH|MED)]\s*[--]\s*(.+)$", _re.I)
    promoted: list[tuple[str, str, str]] = []
    seen: set[tuple[str, str]] = set()

    for line in block.splitlines():
        line = line.replace("—", "-").replace("–", "-")
        match = line_re.match(line)
        if not match:
            continue
        kind = match.group(1).strip().lower()
        value = match.group(2).strip().lower().rstrip(".,;:")
        conf = match.group(3).upper()
        reason = match.group(4).strip()
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
            raw_output=f"{kind}: {value}  [{conf}]  - {reason}",
            target_scope=[value],
            observed_artifacts=[
                ArtifactObservation(value=value, kind=kind, source=f"subagent:{agent_name}")
            ],
            scope_decision_allow=True,
            scope_decision_code="ALLOW_SUBAGENT_PROMOTE",
            scope_decision_reason=f"{kind} [{conf}] declared by {agent_name}: {reason}",
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


def append_subagent_call_records(
    ctx: "ScanContext",
    *,
    round_num: int,
    agent_name: str,
    tool_call_records: list[dict],
    raw_output: str | None = None,
) -> None:
    from ..scanner.case_log import sanitize_audit

    raw_output_preview = (raw_output or "")[:MAX_SUBAGENT_RAW_OUTPUT_CHARS] or None
    include_raw_output = True

    for call in tool_call_records:
        sanitized = dict(call)
        sanitized["scope_ai_evaluation"] = sanitize_audit(
            sanitized.get("scope_ai_evaluation")
        )
        ctx.case_file.subagent_tool_calls.append(
            {
                "root_round": round_num + 1,
                "agent_name": agent_name,
                "raw_output": raw_output_preview if include_raw_output else None,
                **sanitized,
            }
        )
        include_raw_output = False

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


__all__ = [
    "MAX_SUBAGENT_RAW_OUTPUT_CHARS",
    "append_subagent_call_records",
    "parse_scope_promote_block",
]

