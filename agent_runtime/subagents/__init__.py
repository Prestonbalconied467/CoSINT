"""
agent_runtime/scanner/subagents/__init__.py

Subagent package — re-exports everything so the rest of the scanner can
continue to import from `scanner.subagents` without any changes.

Internal layout:
  registry.py   — SubAgentSpec, SUBAGENT_REGISTRY, RootCoordinator
  runner.py     — SubAgentResult, run_subagent
  dispatch.py   — preflight, parse, scope promote, dispatch, linker helpers
"""

from .registry import (
    SCOPE_EXEMPT_SUBAGENTS,
    SUBAGENT_REGISTRY,
    RootCoordinator,
    SubAgentSpec,
    build_subagent_tool_definitions,
    initial_subagents,
    is_scope_exempt_subagent,
)
from .runner import (
    SubAgentResult,
    run_subagent,
)
from .dispatch import (
    SubagentPreflightResult,
    append_subagent_call_records,
    build_evidence_linker_payload,
    dispatch_evidence_linkers,
    dispatch_subagent,
    parse_scope_promote_block,
    parse_subagent_call,
    preflight_subagent_calls,
    print_subagent_summary,
    should_auto_dispatch_evidence_linker,
)

__all__ = [
    # registry
    "SCOPE_EXEMPT_SUBAGENTS",
    "SUBAGENT_REGISTRY",
    "RootCoordinator",
    "SubAgentSpec",
    "build_subagent_tool_definitions",
    "initial_subagents",
    "is_scope_exempt_subagent",
    # runner
    "SubAgentResult",
    "run_subagent",
    # dispatch
    "SubagentPreflightResult",
    "append_subagent_call_records",
    "build_evidence_linker_payload",
    "dispatch_evidence_linkers",
    "dispatch_subagent",
    "parse_scope_promote_block",
    "parse_subagent_call",
    "preflight_subagent_calls",
    "print_subagent_summary",
    "should_auto_dispatch_evidence_linker",
]