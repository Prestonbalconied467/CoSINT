"""
agent_runtime/scanner/subagents/__init__.py

Subagent package exports registry, runner, and split dispatch helpers.
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
from .runner import SubAgentResult, run_subagent
from .dispatch_execution import (
    build_evidence_linker_payload,
    dispatch_evidence_linkers,
    dispatch_subagent,
    print_subagent_summary,
    should_auto_dispatch_evidence_linker,
)
from .dispatch_preflight import (
    SubagentPreflightResult,
    parse_subagent_call,
    preflight_subagent_calls,
)
from .dispatch_records import (
    append_subagent_call_records,
    parse_scope_promote_block,
)

__all__ = [
    "SCOPE_EXEMPT_SUBAGENTS",
    "SUBAGENT_REGISTRY",
    "RootCoordinator",
    "SubAgentSpec",
    "build_subagent_tool_definitions",
    "initial_subagents",
    "is_scope_exempt_subagent",
    "SubAgentResult",
    "run_subagent",
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