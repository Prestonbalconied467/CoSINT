"""
agent_runtime/scanner/decision_types.py

Shared decision/result dataclasses for scanner flow modules.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class NoToolDecision:
    action: Literal["return_report", "request_report", "pause_interactive"]
    report: str | None = None
    prompt: str | None = None


__all__ = ["NoToolDecision"]

