"""
agent_runtime/scope/models.py  –  Scope system data models

Dataclasses used throughout the scope evaluation pipeline.
No logic here — just the type definitions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ScopePolicy:
    primary_target: str
    primary_type: str
    related_targets: list[str] = field(default_factory=list)
    allowed_terms: set[str] = field(default_factory=set)
    allowed_domains: set[str] = field(default_factory=set)
    # Domains allowed ONLY for crypto/blockchain lookup tools — not for
    # domain investigation (whois, dns, fetch, scrape etc.)
    crypto_explorer_domains: set[str] = field(default_factory=set)
    username_attributed_domains: set[str] = field(default_factory=set)


@dataclass
class ScopeDecision:
    allow: bool
    code: str
    reason: str
    ai_score: float | None = None
    ai_reason: str | None = None
    ai_input: dict[str, Any] | None = None


@dataclass
class ScopeBlockedCall:
    tool_call: Any
    tool_args: dict[str, Any] | None  # None signals a parse failure
    decision: ScopeDecision


@dataclass
class ScopePreflightResult:
    executable_tool_calls: list[Any]
    allowed_scope_decisions: dict[int, ScopeDecision]
    blocked_calls: list[ScopeBlockedCall]


__all__ = [
    "ScopeBlockedCall",
    "ScopeDecision",
    "ScopePolicy",
    "ScopePreflightResult",
]
