"""
agent_runtime/reporting/prompting.py

Prompt and decision helpers for report generation.
"""

from __future__ import annotations

from typing import Final, Literal

from ..prompting import looks_like_final_report
from ..scanner.decision_types import NoToolDecision

_REPORT_SECTIONS_NOTE: Final = (
    "## Executive Summary, ## Key Findings (grouped by category, every finding "
    "has a confidence tag + evidence reference), ## Anomalies (every ANOMALY raised; "
    "'none detected' if clean), ## Scope Decisions (summarize allowed/blocked scope "
    "checks with reason codes), ## Evidence Chains (canonical multi-line format: "
    "artifact --[relationship]--> artifact, Source: tool (EV-xxxx), tier T1-T5, "
    "recency, confidence), ## Pivots Taken (with outcome: confirmed/empty/error), "
    "## Subagents Used (which agents ran and what they returned), "
    "## Recommendations (specific tool-level actions -- name the tool, artifact, "
    "and platform), ## Tools Used / Skipped."
)

REPORT_QA_FIELDS: Final = (
    "Investigation mode, Hypothesis verdict (CONFIRMED/REFUTED/INCONCLUSIVE/n/a), "
    "Correlation verdict (verdict + HIGH/MED/LOW / n/a), "
    "Unsupported claims, Confidence overstatements, Contradictions found, "
    "Anomalies flagged, False-positive risks, Missing evidence chains, QA verdict"
)


def _build_multi_target_report_note(
    extra_targets: list[str],
    *,
    correlate_targets: bool,
) -> str:
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


def decide_max_round_action(
    *,
    msg_content: str | None,
    extra_targets: list[str],
    correlate_targets: bool,
) -> NoToolDecision:
    if looks_like_final_report(msg_content or ""):
        return NoToolDecision(action="return_report", report=msg_content or "")
    prompt = build_report_prompt(extra_targets, correlate_targets, mode="force")
    return NoToolDecision(
        action="request_report",
        prompt=f"{prompt} Round limit reached; this is a finalization-only turn. Do not call tools.",
    )


__all__ = [
    "REPORT_QA_FIELDS",
    "_build_multi_target_report_note",
    "build_report_prompt",
    "decide_max_round_action",
]

