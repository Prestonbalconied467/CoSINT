"""
agent_runtime/investigation/plan_checks.py

Plan-check cadence and prompt-injection helpers.
"""

from __future__ import annotations

from typing import Final

from ..scanner.pivot_tracker import build_plan_check_prompt
from .events import record_event

_PLAN_CHECK_INTERVAL: Final[dict[str, int]] = {"quick": 4, "deep": 3}
_DEFAULT_PLAN_CHECK_INTERVAL: Final[int] = 3


def plan_check_interval(depth: str) -> int:
    return _PLAN_CHECK_INTERVAL.get(depth, _DEFAULT_PLAN_CHECK_INTERVAL)


def should_inject_plan_check(round_num: int, depth: str, ctx: "ScanContext") -> bool:
    if ctx.directive_pending:
        return False
    return round_num > 0 and round_num % plan_check_interval(depth) == 0


def inject_plan_check(ctx: "ScanContext", round_num: int) -> None:
    ctx.convo.append(
        {
            "role": "user",
            "content": build_plan_check_prompt(
                evidence=ctx.case_file.evidence_list(),
                seen_signatures=ctx.seen_call_signatures,
                round_num=round_num,
                depth=ctx.depth,
            ),
        }
    )
    record_event(
        ctx.events,
        ctx.event_log_size,
        round_num + 1,
        "plan-check",
        f"round {round_num}",
    )


__all__ = ["inject_plan_check", "plan_check_interval", "should_inject_plan_check"]

