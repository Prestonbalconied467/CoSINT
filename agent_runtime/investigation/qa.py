"""
agent_runtime/investigation/qa.py

QA verdict extraction and transition handlers.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any, Final

from ..display import dim, print_narrative, print_pre_report_pause
from ..reporting.prompting import build_report_prompt
from .events import record_event

if TYPE_CHECKING:
    from ..scanner.context import ScanContext

REPORT_QA_FIELDS: Final = (
    "Investigation mode, Hypothesis verdict (CONFIRMED/REFUTED/INCONCLUSIVE/n/a), "
    "Correlation verdict (verdict + HIGH/MED/LOW / n/a), "
    "Unsupported claims, Confidence overstatements, Contradictions found, "
    "Anomalies flagged, False-positive risks, Missing evidence chains, QA verdict"
)

_QA_VERDICT_RE: Final = re.compile(
    r"qa verdict\s*:\s*(PASS(?:\s+WITH\s+NOTES)?|FAIL)",
    re.IGNORECASE,
)


def extract_qa_verdict(content: str | None) -> str | None:
    if not content:
        return None
    match = _QA_VERDICT_RE.search(content)
    if not match:
        return None
    verdict = match.group(1).upper()
    if verdict.startswith("PASS WITH"):
        return "PASS WITH NOTES"
    return verdict


def handle_qa_verdict(
    ctx: "ScanContext",
    msg: Any,
    qa_verdict: str,
    round_num: int,
) -> bool | None:
    if qa_verdict in ("PASS", "PASS WITH NOTES") and not ctx.report_requested:
        if ctx.interactive_root:
            print_narrative(msg.content)
            ctx.qa_narrative_printed = True  # type: ignore[attr-defined]
            directive = print_pre_report_pause(None)
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
                return None

            print(f"\n  {dim('Generating report...')}\n")
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
            return True

        ctx.report_requested = True
        ctx.report_request_count = getattr(ctx, "report_request_count", 0) + 1
        record_event(
            ctx.events,
            ctx.event_log_size,
            round_num + 1,
            "report-request",
            f"QA verdict {qa_verdict} -- auto-transitioning to report",
        )
        ctx.convo.append(
            {
                "role": "user",
                "content": build_report_prompt(
                    ctx.extra_targets, ctx.correlate_targets, mode="force"
                ),
            }
        )
        return True

    if qa_verdict == "FAIL":
        record_event(
            ctx.events,
            ctx.event_log_size,
            round_num + 1,
            "qa-fail",
            "QA verdict FAIL -- pausing for operator"
            if ctx.interactive_root
            else "QA verdict FAIL -- re-requesting report with remediation note",
        )

        if not ctx.interactive_root:
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
            return None

        print_narrative(msg.content)
        ctx.qa_narrative_printed = True  # type: ignore[attr-defined]
        directive = print_pre_report_pause(None)
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
            return None

        inject = (
            "The PRE-REPORT QA check returned FAIL. "
            "Address every issue flagged in the QA block, then "
            + build_report_prompt(ctx.extra_targets, ctx.correlate_targets, mode="force")
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
        return True

    return None


__all__ = ["REPORT_QA_FIELDS", "extract_qa_verdict", "handle_qa_verdict"]

