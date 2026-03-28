"""
agent_runtime/scanner/subagent_registry.py

Subagent registry and root coordinator:
  - SubAgentSpec         — frozen spec for one specialist subagent
  - SUBAGENT_REGISTRY    — dict of all registered subagents
  - SCOPE_EXEMPT_SUBAGENTS / is_scope_exempt_subagent
  - initial_subagents    — recommended starting agents per target type
  - build_subagent_tool_definitions — litellm-compatible tool schema for root
  - RootCoordinator      — dispatch tracking and hint generation for the root agent
"""

from __future__ import annotations

from dataclasses import dataclass


# ---------------------------------------------------------------------------
# SubAgentSpec
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SubAgentSpec:
    """Immutable descriptor for one specialist subagent.

    Attributes:
        name: Registry key and display name.
        kind: ``"domain"`` for artifact-focused agents, ``"cross_cutting"`` for
            reasoning / coordination agents.
        skill_name: Maps to ``skills/subagents/<skill_name>.md``.
        description: One-line description injected into the root's tool list.
        tool_scope: Tool-name prefixes this agent may call.  Empty tuple means
            the agent has no tools and operates in reasoning-only mode.
    """

    name: str
    kind: str
    skill_name: str
    description: str
    tool_scope: tuple[str, ...] = ()


# ---------------------------------------------------------------------------
# SUBAGENT_REGISTRY
# ---------------------------------------------------------------------------

SUBAGENT_REGISTRY: dict[str, SubAgentSpec] = {
    # ------------------------------------------------------------------
    # Domain agents — one per primary artifact / target type
    # ------------------------------------------------------------------
    "email": SubAgentSpec(
        name="email",
        kind="domain",
        skill_name="subagent_email",
        description=(
            "Investigate an email address: validation, breach history, social discovery, "
            "reputation, domain pivot. Returns confirmed identity anchors."
        ),
        tool_scope=(
            "osint_email_",
            "osint_google_account_scan",
            "osint_domain_whois",
            "osint_domain_dns_records",
            "osint_web_dork",
            "osint_web_search",
            "osint_notes_",
            "osint_todo_",
        ),
    ),
    "person": SubAgentSpec(
        name="person",
        kind="domain",
        skill_name="subagent_person",
        description=(
            "Investigate a real person by name: public records, derived usernames, web "
            "presence, court records, address history. Returns identity profile with "
            "attribution confidence."
        ),
        tool_scope=(
            "osint_person_",
            "osint_username_search",
            "osint_email_social_accounts",
            "osint_public_court_records",
            "osint_public_news_search",
            "osint_media_reverse_image_search",
            "osint_web_dork",
            "osint_web_search",
            "osint_notes_",
            "osint_todo_",
        ),
    ),
    "username": SubAgentSpec(
        name="username",
        kind="domain",
        skill_name="subagent_username",
        description=(
            "Investigate an online handle: cross-platform sweep, verification pass, "
            "GitHub/Reddit deep-dive, variant search. Returns cross-platform identity picture."
        ),
        tool_scope=(
            "osint_username_",
            "osint_social_extract",
            "osint_scraper_fetch",
            "osint_media_reverse_image_search",
            "osint_media_exif_extract",
            "osint_leak_paste_search",
            "osint_web_dork",
            "osint_web_search",
            "osint_public_news_search",
            "osint_notes_",
            "osint_todo_",
        ),
    ),
    "phone": SubAgentSpec(
        name="phone",
        kind="domain",
        skill_name="subagent_phone",
        description=(
            "Investigate a phone number: carrier lookup, line type, web mentions, paste "
            "exposure. Returns identity anchors and fraud risk assessment."
        ),
        tool_scope=(
            "osint_phone_lookup",
            "osint_leak_paste_search",
            "osint_email_social_accounts",
            "osint_media_reverse_image_search",
            "osint_web_dork",
            "osint_web_search",
            "osint_notes_",
            "osint_todo_",
        ),
    ),
    "infrastructure": SubAgentSpec(
        name="infrastructure",
        kind="domain",
        skill_name="subagent_infrastructure",
        description=(
            "Investigate domains, DNS, certificates, IPs, ASNs, and hosting infrastructure. "
            "Returns full network topology with operator fingerprints."
        ),
        tool_scope=(
            "osint_domain_",
            "osint_network_",
            "osint_scraper_extract",
            "osint_scraper_fetch",
            "osint_web_dork",
            "osint_web_search",
            "osint_notes_",
            "osint_todo_",
        ),
    ),
    "company": SubAgentSpec(
        name="company",
        kind="domain",
        skill_name="subagent_company",
        description=(
            "Investigate a registered company: registry data, directors, employees, web "
            "presence, legal exposure, related entities. Returns corporate structure and "
            "beneficial owner signals."
        ),
        tool_scope=(
            "osint_company_",
            "osint_scraper_website",
            "osint_scraper_fetch",
            "osint_public_court_records",
            "osint_public_news_search",
            "osint_domain_whois",
            "osint_domain_tech_fingerprint",
            "osint_web_dork",
            "osint_web_search",
            "osint_notes_",
            "osint_todo_",
        ),
    ),
    "leaks": SubAgentSpec(
        name="leaks",
        kind="domain",
        skill_name="subagent_leaks",
        description=(
            "Search breach databases, paste sites, and exposed secrets. Returns credential "
            "exposure findings with recency tags and pivot artifacts."
        ),
        tool_scope=(
            "osint_leak_",
            "osint_email_breach_check",
            "osint_web_dork",
            "osint_notes_",
            "osint_todo_",
        ),
    ),
    "geo": SubAgentSpec(
        name="geo",
        kind="domain",
        skill_name="subagent_geo",
        description=(
            "Resolve and validate physical locations from GPS coordinates, IP geolocation, "
            "or address data. Returns normalized location with confidence scale."
        ),
        tool_scope=(
            "osint_geo_",
            "osint_network_ip_geolocation",
            "osint_person_address_lookup",
            "osint_company_registry_lookup",
            "osint_notes_",
            "osint_todo_",
        ),
    ),
    "finance": SubAgentSpec(
        name="finance",
        kind="domain",
        skill_name="subagent_finance",
        description=(
            "Trace crypto wallet flows, transaction patterns, and blockchain counterparties. "
            "Returns financial intelligence with off-chain identity signals."
        ),
        tool_scope=(
            "osint_crypto_",
            "osint_blockchain_",
            "osint_wallet_",
            "osint_crypto_ens_lookup",
            "osint_web_dork",
            "osint_web_search",
            "osint_notes_",
            "osint_todo_",
        ),
    ),
    "media": SubAgentSpec(
        name="media",
        kind="domain",
        skill_name="subagent_media",
        description=(
            "Investigate images and media files: EXIF metadata, GPS coordinates, reverse "
            "image search, OCR text extraction, platform provenance. Returns identity and "
            "location signals."
        ),
        tool_scope=(
            "osint_media_",
            "osint_geo_reverse",
            "osint_web_dork",
            "osint_web_search",
            "osint_notes_",
            "osint_todo_",
        ),
    ),
    "social": SubAgentSpec(
        name="social",
        kind="domain",
        skill_name="subagent_social",
        description=(
            "Deep-dive on confirmed social profiles: GitHub commit email extraction, Reddit "
            "behavioral fingerprint, cross-platform consistency. Returns rich identity signals."
        ),
        tool_scope=(
            "osint_social_extract",
            "osint_scraper_fetch",
            "osint_media_reverse_image_search",
            "osint_media_exif_extract",
            "osint_web_dork",
            "osint_web_search",
            "osint_notes_",
            "osint_todo_",
        ),
    ),
    "scraper": SubAgentSpec(
        name="scraper",
        kind="domain",
        skill_name="subagent_scraper",
        description=(
            "Extract operator identity and contact data from websites: Impressum, contact "
            "pages, hidden paths, JS bundles. Returns structured findings with pivot artifacts."
        ),
        tool_scope=(
            "osint_scraper_extract",
            "osint_scraper_fetch",
            "osint_web_dork",
            "osint_web_search",
            "osint_notes_",
            "osint_todo_",
        ),
    ),
    # ------------------------------------------------------------------
    # Cross-cutting agents — reasoning / coordination / QA
    # ------------------------------------------------------------------
    "evidence_linker": SubAgentSpec(
        name="evidence_linker",
        kind="cross_cutting",
        skill_name="subagent_evidence_linker",
        description=(
            "Connect confirmed artifacts into explicit evidence chains with source tier, "
            "recency, and confidence tags. Call after a domain agent returns new findings "
            "to lock in the chain before pivoting."
        ),
        tool_scope=(),
    ),
    "entity_resolution": SubAgentSpec(
        name="entity_resolution",
        kind="cross_cutting",
        skill_name="subagent_entity_resolution",
        description=(
            "Determine whether two or more seeds belong to the same real-world entity. "
            "Returns SAME_ENTITY / LIKELY_LINKED / UNRELATED / INCONCLUSIVE verdict."
        ),
        tool_scope=(),
    ),
    "validator": SubAgentSpec(
        name="validator",
        kind="cross_cutting",
        skill_name="subagent_validator",
        description=(
            "Apply confidence scoring and contradiction checks to a set of findings. "
            "Returns confidence-corrected finding list with contradictions flagged."
        ),
        tool_scope=(),
    ),
    "budget_guard": SubAgentSpec(
        name="budget_guard",
        kind="cross_cutting",
        skill_name="subagent_budget_guard",
        description=(
            "Assess investigation efficiency and recommend whether to continue or wrap up. "
            "Returns triage: top findings, open pivots, stop/continue recommendation."
        ),
        tool_scope=(),
    ),
    "tool_strategist": SubAgentSpec(
        name="tool_strategist",
        kind="cross_cutting",
        skill_name="subagent_tool_strategist",
        description=(
            "Called when investigation is stalled. Diagnoses incomplete pivot chains and "
            "proposes the next 2-3 highest-signal actions."
        ),
        tool_scope=(),
    ),
    "report_synthesizer": SubAgentSpec(
        name="report_synthesizer",
        kind="cross_cutting",
        skill_name="subagent_report_synthesizer",
        description=(
            "Write the final structured investigation report from a completed case file. "
            "Returns the formatted final report."
        ),
        tool_scope=("osint_notes_",),
    ),
}

# ---------------------------------------------------------------------------
# Scope-exempt subagents
# ---------------------------------------------------------------------------

# Agents that consume narrative context only and must not be blocked by root
# scope gates on call_subagent(context=...).
SCOPE_EXEMPT_SUBAGENTS: frozenset[str] = frozenset(
    {
        "budget_guard",
        "entity_resolution",
        "evidence_linker",
        "report_synthesizer",
        "tool_strategist",
        "validator",
    }
)


def is_scope_exempt_subagent(agent_name: str) -> bool:
    """Return ``True`` when *agent_name* is exempt from root scope gating.

    Args:
        agent_name: Name to look up (leading/trailing whitespace is stripped).

    Returns:
        ``True`` if the agent is in ``SCOPE_EXEMPT_SUBAGENTS``.
    """
    return (agent_name or "").strip() in SCOPE_EXEMPT_SUBAGENTS


# ---------------------------------------------------------------------------
# Initial agent selection per target type
# ---------------------------------------------------------------------------

_INITIAL_BY_TYPE: dict[str, list[str]] = {
    "email": ["email", "leaks"],
    "person": ["person"],
    "username": ["username"],
    "domain": ["infrastructure"],
    "ip": ["infrastructure", "geo"],
    "phone": ["phone"],
    "company": ["company", "infrastructure"],
    "crypto": ["finance", "leaks"],
    "geo": ["geo"],
    "media": ["media"],
}


def initial_subagents(
    target_type: str,
    has_multi_targets: bool,
    correlate_targets: bool,
) -> list[str]:
    """Return the recommended starting subagents for a given target type.

    Args:
        target_type: Semantic type string (e.g. ``"email"``, ``"domain"``).
        has_multi_targets: When ``True``, an extra ``entity_resolution`` agent
            is appended for multi-target correlation scans.
        correlate_targets: Only relevant when *has_multi_targets* is ``True``.

    Returns:
        List of subagent names to dispatch at the start of the scan.
    """
    base = list(_INITIAL_BY_TYPE.get(target_type, ["person"]))
    if has_multi_targets and correlate_targets and "entity_resolution" not in base:
        base.append("entity_resolution")
    return base


# ---------------------------------------------------------------------------
# Root tool definition
# ---------------------------------------------------------------------------


def build_subagent_tool_definitions() -> list[dict]:
    """Build a litellm-compatible tool definition for ``call_subagent``.

    Returns a single-element list containing the full JSON Schema tool
    descriptor that the root agent uses to call specialist subagents.

    Returns:
        List with one tool-definition dict.
    """
    agent_names = sorted(SUBAGENT_REGISTRY.keys())
    agent_list = "\n".join(
        f"  - {name}: {SUBAGENT_REGISTRY[name].description}" for name in agent_names
    )
    return [
        {
            "type": "function",
            "function": {
                "name": "call_subagent",
                "description": (
                    "Delegate a focused investigation task to a specialist subagent. "
                    "The subagent runs its own tool loop and returns structured findings. "
                    "Use this when you need deep work on a specific artifact type or analysis.\n\n"
                    f"Available agents:\n{agent_list}"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "agent": {
                            "type": "string",
                            "enum": agent_names,
                            "description": "Which specialist subagent to call.",
                        },
                        "task": {
                            "type": "string",
                            "description": (
                                "Specific task for the agent. Be explicit: what to investigate, "
                                "what question to answer, what artifacts to start from."
                            ),
                        },
                        "context": {
                            "type": "string",
                            "description": (
                                "Structured summary for the agent: primary target, target type, "
                                "key artifacts found so far (with EV-IDs), open questions. "
                                "Do NOT pass raw conversation history — summarize what the agent needs."
                            ),
                        },
                    },
                    "required": ["agent", "task", "context"],
                },
            },
        }
    ]


# ---------------------------------------------------------------------------
# RootCoordinator
# ---------------------------------------------------------------------------


class RootCoordinator:
    """Track subagent dispatch and results for the root agent loop.

    Provides the root agent with its tool definition and the initial dispatch
    hint embedded in the system prompt.

    Args:
        target_type: Primary target type (e.g. ``"email"``).
        has_multi_targets: Whether the scan has multiple targets.
        correlate_targets: Whether the multi-target scan is in verification mode.
    """

    def __init__(
        self,
        target_type: str,
        has_multi_targets: bool = False,
        correlate_targets: bool = False,
    ) -> None:
        self.target_type = target_type
        self.has_multi_targets = has_multi_targets
        self.correlate_targets = correlate_targets
        self._results: list = []
        self._initial: list[str] = initial_subagents(
            target_type, has_multi_targets, correlate_targets
        )

    def initial_agent_names(self) -> list[str]:
        """Return the recommended starting subagent names."""
        return list(self._initial)

    def record_result(self, result: object) -> None:
        """Append a completed subagent result.

        Args:
            result: A ``SubAgentResult`` instance.
        """
        self._results.append(result)

    def completed_agents(self) -> list[str]:
        """Return the names of all agents that have completed."""
        return [r.agent_name for r in self._results]

    def get_results(self) -> list:
        """Return a copy of all recorded subagent results."""
        return list(self._results)

    def build_dispatch_hint(self) -> str:
        """Build the compact routing hint injected into the root system prompt.

        Returns:
            A formatted string listing recommended starting agents and all
            available subagents.
        """
        initial_names = ", ".join(self._initial)
        all_agents = "\n".join(
            f"  - {name}: {spec.description}"
            for name, spec in SUBAGENT_REGISTRY.items()
        )
        return (
            f"## Subagent Dispatch\n"
            f"Recommended starting subagents for target type '{self.target_type}': "
            f"{initial_names}\n\n"
            f"All available subagents (use call_subagent tool):\n{all_agents}\n\n"
            "Dispatch subagents for deep artifact work. Pass them a focused task and a "
            "concise context summary.\n"
            "After they return, review findings, record key evidence, and decide: "
            "dispatch another, continue yourself, or wrap up."
        )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

__all__ = [
    "SCOPE_EXEMPT_SUBAGENTS",
    "SUBAGENT_REGISTRY",
    "RootCoordinator",
    "SubAgentSpec",
    "build_subagent_tool_definitions",
    "initial_subagents",
    "is_scope_exempt_subagent",
]
