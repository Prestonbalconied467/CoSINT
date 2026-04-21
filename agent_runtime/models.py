from __future__ import annotations

from collections import OrderedDict
from dataclasses import asdict, dataclass, field
from typing import Any

from shared.config import DEFAULT_SCOPE_MODE

try:
    import litellm
except ImportError:
    litellm = None  # type: ignore


@dataclass
class AgentEvent:
    round_num: int
    phase: str
    detail: str


@dataclass
class ArtifactObservation:
    value: str
    kind: str
    source: str
    # Set to False by ai-mode artifact rating when the artifact is not
    # confidently attributable to the investigation target.  Preserved in the
    # evidence record for audit purposes but excluded from scope expansion in
    # build_scope_policy.  Defaults to True so all non-ai-mode paths are
    # unaffected.
    scope_approved: bool = True


#
@dataclass
class ToolEvidenceRecord:
    round_num: int
    phase: str
    tool_name: str
    tool_args: dict
    status: str
    started_at: str
    duration_ms: int
    result_preview: str
    raw_output: str
    target_scope: list[str] = field(default_factory=list)
    observed_artifacts: list[ArtifactObservation] = field(default_factory=list)
    scope_mode: str = DEFAULT_SCOPE_MODE
    scope_decision_allow: bool = True
    scope_decision_code: str = "ALLOW_UNKNOWN"
    scope_decision_reason: str = ""
    evidence_id: str = ""
    scope_ai_audit: dict | None = None
    is_duplicate: bool = False
    duplicate_of: str | None = None


@dataclass
class RelationSummary:
    mode: str
    primary_target: str
    related_targets: list[str] = field(default_factory=list)
    shared_artifacts: list[str] = field(default_factory=list)
    conflicting_artifacts: list[str] = field(default_factory=list)


@dataclass
class ScopeInclusion:
    value: str
    kind: str
    reason: str


@dataclass
class CaseFile:
    created_at: str
    primary_target: str
    primary_target_type: str
    depth: str
    model: str
    instruction: str | None
    hypothesis: str | None
    correlate_targets: bool
    scope_mode: str = DEFAULT_SCOPE_MODE
    policies: list[str] = field(default_factory=list)
    related_targets: list[str] = field(default_factory=list)
    # Change evidence from list to ordered mapping: evidence_id -> record
    evidence: dict[str, ToolEvidenceRecord] = field(default_factory=OrderedDict)
    events: list[AgentEvent] = field(default_factory=list)
    relation: RelationSummary | None = None
    todo_snapshot: str | None = None
    notes_snapshot: str | None = None
    subagent_tool_calls: list[dict] = field(default_factory=list)
    scope_inclusions: list[ScopeInclusion] = field(default_factory=list)

    # Internal counter to allocate stable sequential EV numbers
    _next_evidence_number: int = field(default=1, init=False, repr=False)

    def allocate_evidence_id(self, *, subagent: bool = False) -> str:
        """Return the next EV id and increment the counter."""
        prefix = "EV-SUB-" if subagent else "EV-"
        eid = f"{prefix}{self._next_evidence_number:04d}"
        self._next_evidence_number += 1
        return eid

    def add_evidence(
        self,
        record: ToolEvidenceRecord,
        *,
        evidence_id: str | None = None,
        subagent: bool = False,
    ) -> str:
        """Insert a ToolEvidenceRecord into the ordered mapping and return its evidence_id.

        - If record.evidence_id is already set, it will be used (but ensure uniqueness).
        - If evidence_id param is provided it will be used.
        - Otherwise allocate an id automatically.
        """
        _ = evidence_id  # kept for API compatibility with existing callers
        eid = self.allocate_evidence_id(subagent=subagent)
        # make sure record has its id set
        record.evidence_id = eid
        self.evidence[eid] = record
        return eid

    def evidence_list(self) -> list[ToolEvidenceRecord]:
        """Return the evidence records in insertion order as a list (helper)."""
        return list(self.evidence.values())

    def recent_evidence(self, max_n: int) -> list[ToolEvidenceRecord]:
        if max_n <= 0:
            return []
        vals = list(self.evidence.values())
        return vals[-max(1, max_n) :]

    def to_dict(self) -> dict:
        # produce dict where evidence is a mapping of id -> record-as-dict
        d = asdict(self)
        d["primary_type"] = d["primary_target_type"]
        # convert evidence values to dicts (asdict will have converted dataclasses,
        # but if needed ensure the mapping shape is clear)
        d["evidence"] = {k: asdict(v) for k, v in self.evidence.items()}
        return d


def _extract_token_counts(response: Any) -> tuple[int, int, int, float]:
    """Extract (prompt_tokens, completion_tokens, total_tokens, cost_usd) from a litellm response.

    Handles both attribute-style (litellm objects) and dict-style usage objects.
    cost_usd is 0.0 when litellm is not available or cost calculation fails.
    """
    usage = getattr(response, "usage", None) or {}
    p = int(getattr(usage, "prompt_tokens", 0) or usage.get("prompt_tokens", 0) or 0)
    c = int(
        getattr(usage, "completion_tokens", 0) or usage.get("completion_tokens", 0) or 0
    )
    t = int(
        getattr(usage, "total_tokens", 0) or usage.get("total_tokens", 0) or (p + c)
    )
    cost = 0.0
    if litellm is not None:
        try:
            cost = float(litellm.completion_cost(completion_response=response))
        except Exception:
            pass
    return p, c, t, cost


@dataclass
class UsageStats:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cost_usd: float = 0.0
    compressed_events: int = 0

    def apply(self, response: Any) -> None:
        p, c, t, cost = _extract_token_counts(response)
        self.prompt_tokens += p
        self.completion_tokens += c
        self.total_tokens += t
        self.cost_usd += cost


@dataclass
class ScanStats:
    rounds: int = 0
    tools_run: int = 0
    tools_deduped: int = 0
    tools_blocked: int = 0
    subagents_activated: list[str] = field(default_factory=list)
    directives_issued: int = 0
    pivots_found: int = 0


__all__ = [
    "AgentEvent",
    "ArtifactObservation",
    "ToolEvidenceRecord",
    "RelationSummary",
    "CaseFile",
    "UsageStats",
    "ScanStats",
    "ScopeInclusion",
    "_extract_token_counts",
]
