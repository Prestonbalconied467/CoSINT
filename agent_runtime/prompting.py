from __future__ import annotations


from .targeting import detect_type
from .display import print_info


def build_instruction_block(instruction: str | None) -> str:
    if not instruction:
        return ""
    return (
        "\n## Operator Context (known facts)\n"
        f"{instruction}\n\n"
        "These are facts provided by the operator as investigation seeds or background context. "
        "Do not treat them as claims to prove or disprove — use them to focus your investigation.\n"
    )


def build_hypothesis_block(hypothesis: str | None) -> str:
    if not hypothesis:
        return ""
    return (
        "\n## Hypothesis\n"
        f"{hypothesis}\n\n"
        "Treat this as a falsifiable hypothesis. "
        "Restate it as a single yes/no claim in your investigation plan.\n"
    )


def build_policy_block(policy_flags: list[str]) -> str:
    if not policy_flags:
        return ""
    lines = []
    if "passive_only" in policy_flags:
        lines.append("- passive_only: avoid active probing such as open-port scans")
    if "skip_social" in policy_flags:
        lines.append(
            "- skip_social: do not use social-media or social-account discovery tools"
        )
    if "skip_breaches" in policy_flags:
        lines.append(
            "- skip_breaches: do not use breach, leak, or exposed-secret tools"
        )
    return (
        "\n## Policy Controls (mandatory — do not override)\n" + "\n".join(lines) + "\n"
    )


def build_multi_target_block(extra_targets: list[str], correlate_targets: bool) -> str:
    if not extra_targets:
        return ""
    all_targets_str = "\n".join(
        f"  - {t}  (type: {detect_type(t)})" for t in extra_targets
    )
    if correlate_targets:
        return (
            "\n## Multi-Target — VERIFY CORRELATION\n"
            "Additional targets to verify against primary:\n"
            f"{all_targets_str}\n\n"
            "Investigate each independently first, then cross-reference. "
            "Dispatch entity_resolution subagent when you have profiles for both seeds.\n"
        )
    return (
        "\n## Multi-Target — CONFIRMED SAME SUBJECT\n"
        "Additional confirmed identifiers for the same subject:\n"
        f"{all_targets_str}\n\n"
        "Use all identifiers as investigation seeds. Do not spend budget proving they're linked.\n"
    )


def build_opening_parts(
    *,
    target: str,
    target_type: str,
    depth: str,
    extra_targets: list[str],
    correlate_targets: bool,
    policy_flags: list[str],
    instruction: str | None,
    hypothesis: str | None,
) -> list[str]:
    opening_parts = [
        "Begin OSINT investigation.",
        f"Primary target: {target}",
        f"Target type: {target_type}",
        f"Depth: {depth}",
    ]
    if extra_targets:
        mode_label = (
            "VERIFY CORRELATION" if correlate_targets else "CONFIRMED SAME SUBJECT"
        )
        opening_parts.append(
            f"Additional targets ({mode_label}): {', '.join(extra_targets)}"
        )
    if policy_flags:
        opening_parts.append(f"Policy controls: {', '.join(policy_flags)}")
    if instruction:
        opening_parts.append(f"Operator context: {instruction}")
    if hypothesis:
        opening_parts.append(f"Hypothesis to verify: {hypothesis}")
    opening_parts.append(
        "\nStart by writing your Investigation Plan (osint_notes_add), "
        "then dispatch your first subagent or call Phase 0 tool directly."
    )
    return opening_parts


def looks_like_final_report(content: str) -> bool:
    if not content:
        return False

    required_headers = [
        "## executive summary",
        "## key findings",
        "## anomalies",
        "## scope decisions",
        "## evidence chains",
        "## pivots taken",
        "## subagents used",
        "## recommendations",
        "## tools used",
    ]

    lower = content.lower()
    matched = sum(1 for h in required_headers if h in lower)
    return matched >= 5


def build_reference_injection(
    *,
    general_skill: str,
    reasoning_skill: str,
    depth_skill: str,
    correlation_skill: str = "",
    report_skill: str = "",
    correlate_targets: bool = False,
) -> str:
    load_correlation = correlate_targets
    parts = [
        "# Investigation Reference Material\nApply the following throughout the investigation.\n",
        general_skill,
        "---",
        reasoning_skill,
    ]
    if load_correlation and correlation_skill:
        parts += ["---", correlation_skill]
    parts += [
        "---",
        depth_skill,
    ]
    if report_skill:
        parts += [
            "---",
            "# Report Synthesis Reference (apply during final write-up)",
            report_skill,
        ]
    parts += ["---\nReference material loaded. Proceed with the investigation."]
    return "\n\n".join(p for p in parts if p)


#  · (The PRE-REPORT QA and report as previously provided
#    remain current; no new high-confidence correlation
#    established.)


def _completion_rule(mode: str, depth: str = "quick") -> str:
    signal_loss_rounds = 2 if depth == "quick" else 3
    signal_loss_threshold = "[HIGH]" if depth == "quick" else "[HIGH] or [MED]"
    stop_condition = {
        "open": f"""\
**Stop (Signal Loss)** after {signal_loss_rounds} consecutive full rounds where no new \
{signal_loss_threshold} artifact is discovered. Absence of public record after \
{signal_loss_rounds} rounds of diverse dorking is itself a finding.""",
        "hypothesis": """\
**Stop (Mission Success)** when you reach a [HIGH] confidence verdict (Confirmed or Refuted) \
backed by ≥1 Tier 1 source or ≥2 Tier 2 sources. Do not keep investigating once the verdict \
is clear.""",
        "correlation": """\
**Stop (Mission Success)** when `entity_resolution` returns a definitive link/no-link verdict \
with clear evidence. Do not seek further confirmation of a settled verdict.""",
        "hypothesis_correlation": """\
**Stop (Mission Success)** when BOTH conditions are met:
- Hypothesis: [HIGH] confidence verdict (Confirmed or Refuted) with sufficient sourcing.
- Correlation: `entity_resolution` returns a definitive verdict.
If one resolves first, keep going only for the other.""",
        "full_profile": """\
**Stop (Exhaustion)** only when ALL of:
- Every primary target skill step is complete.
- Every [HIGH] and [MED] artifact has been pivoted at least one level deep.
- All Phase 3 tools are run or explicitly skipped with a documented reason.
- Every dead end is documented.
When unsure whether to stop — run one more round.""",
    }[mode]

    wrap_up = """\

**Wrap-up — MANDATORY IMMEDIATE EXECUTION**
The moment the stop condition above is met, your NEXT output must begin the wrap-up. \
Do NOT call any further tools. Do NOT pause. Do NOT announce readiness. Execute:

Step 1 — If total rounds > 12: dispatch `budget_guard`. Wait for result.
Step 2 — Call `osint_notes_list`. Then output the PRE-REPORT QA block verbatim.

Saying "ready for QA" does NOT satisfy this requirement. \
The QA block itself must appear in your output.\

**NEVER say** phrases like "QA as previously provided remains current", \
"report remains current", or any variant implying the QA block was already output \
and need not be repeated. \
Every wrap-up sequence MUST produce a complete, freshly written PRE-REPORT QA block. \

Once you output the PRE-REPORT QA block, stop. Do NOT call any further tools. \
Do NOT attempt to dispatch the report yourself. The system will handle the next step.
"""

    return f"## When to Stop\n{stop_condition}\n{wrap_up}"


def build_initial_messages(
    *,
    system_prompt: str,
    reference_injection: str,
    opening_parts: list[str],
    model: str,
    prefer_system: bool = True,
) -> tuple[list[dict], str]:
    """
    Build initial message list. If prefer_system=True (default), attempts to inject
    reference material as a second system message. If prefer_system=False, uses the
    user-prompt fallback unconditionally (called after a system-role rejection).
    """
    base: list[dict] = [{"role": "system", "content": system_prompt}]
    if prefer_system:
        base.append({"role": "system", "content": reference_injection})
        role_label = "system prompt"
    else:
        base.append({"role": "user", "content": reference_injection})
        base.append(
            {
                "role": "assistant",
                "content": "Reference material loaded. Starting investigation.",
            }
        )
        role_label = "user prompt (fallback)"
    base.append({"role": "user", "content": "\n".join(opening_parts)})
    return base, role_label


def build_system_prompt(
    *,
    target: str,
    target_type: str,
    depth: str,
    dispatch_hint: str,
    instruction_block: str,
    hypothesis_block: str,
    policy_block: str,
    multi_target_block: str,
    interactive: bool = False,
    instruction_text: str = "",
    hypothesis_text: str = "",
    correlate_targets: bool = False,
    open_ended: bool = False,
) -> str:
    if correlate_targets and hypothesis_text:
        mode_line = "Mode: Hypothesis + Entity Correlation"
        completion_rule = _completion_rule("hypothesis_correlation", depth)
    elif correlate_targets:
        mode_line = "Mode: Entity Correlation"
        completion_rule = _completion_rule("correlation", depth)
    elif open_ended:
        mode_line = "Mode: Open Investigation"
        completion_rule = _completion_rule("open", depth)
    elif hypothesis_text:
        mode_line = "Mode: Hypothesis"
        completion_rule = _completion_rule("hypothesis", depth)
    else:
        mode_line = "Mode: Full Profile"
        completion_rule = _completion_rule("full_profile", depth)
    print_info(f"Investigation {mode_line}")
    return f"""\
You are the ROOT INVESTIGATOR in an OSINT investigation system.

Your role is to PLAN, COORDINATE, and SYNTHESIZE. You:
1. Write the investigation plan.
2. Run Phase 0 classification yourself.
3. Dispatch specialist subagents for deep artifact work (via call_subagent tool).
4. Review subagent findings, record key evidence, and decide next steps.
5. Wrap up with QA and final report when investigation is complete.

## Current Investigation
Target           : {target}
Target type      : {target_type}
Depth            : {depth}
Interactive Mode : {interactive}
Hypothesis       : {hypothesis_text or "n/a"}
Context/Notes    : {instruction_text or "none"}
{mode_line}

{dispatch_hint}
{hypothesis_block}{instruction_block}{policy_block}{multi_target_block}
---

## Narration Rules (follow on every action)

1. BEFORE each tool call or subagent dispatch, one line:
   "Checking [what] because [specific reason tying back to evidence or active goal]."

2. AFTER each result, one line:
   "Found: [key fact]" OR "No results — [what this means / next step]."

3. When dispatching a subagent:
   "Dispatching [agent]: [what I need from them and why]."

4. When a subagent returns:
   "Subagent [agent] returned. Key findings: [summary]. Next: [decision]."

5. New pivot discovered:
   "PIVOT: [type] -> [value]  (reason: [why this matters])"

6. Unexpected result:
   "ANOMALY: [description]" then osint_notes_add(title="ANOMALY: ...", tags="anomaly")

7. Phase complete:
   "Phase complete: [2-3 sentences on what was established]"

---

## Scope Guardrails
- Only investigate identifiers directly attributable to the target.
- Do NOT pivot into third-party platform infrastructure (social-media host domains,
  map providers, generic web hosts) unless confirmed as target-owned assets.
- A discovered username from a dork result IS a valid pivot — dispatch identity subagent.

---

{completion_rule}

---

## Pre-Report QA (mandatory before final report)

List all notes with osint_notes_list first. Then output:
```
PRE-REPORT QA
-------------
Investigation mode        : [Full Profile | Hypothesis | Open Investigation | Correlation | Hypothesis + Correlation]
Hypothesis verdict        : [CONFIRMED / REFUTED / INCONCLUSIVE — or "n/a"]
Correlation verdict       : [SAME_PERSON | SAME_ORGANIZATION | OWNS_OPERATES | AFFILIATED | INFRASTRUCTURE_SHARED | UNRELATED | INCONCLUSIVE — or "n/a"] + [HIGH | MED | LOW]
Unsupported claims        : [list or "none"]
Confidence overstatements : [list or "none"]
Contradictions found      : [list or "none"]
Anomalies flagged         : [list every ANOMALY raised, or "none"]
False-positive risks      : [list or "none"]
Missing evidence chains   : [list any [HIGH] without a chain, or "none"]
QA verdict                : PASS / PASS WITH NOTES / FAIL
```
If FAIL — stop. Do NOT write the report. State what must be resolved.

---

## Final Report
Default path: after QA verdict is PASS or PASS WITH NOTES, dispatch `report_synthesizer`
to write the final report.

Fallback path: if report_synthesizer fails (error, empty output, or invalid structure),
write the final report directly as root investigator.

The report will contain these sections (collect evidence toward all of them):
```
## Executive Summary
## Key Findings
## Anomalies
## Scope Decisions
## Evidence Chains
## Pivots Taken
## Subagents Used
## Recommendations
## Tools Used / Skipped
## QA Notes          ← only if QA = PASS WITH NOTES
```
Use the required section structure above exactly. Evidence_linker may still be used
to connect artifacts into explicit chains before report generation.


---
**First action:** Write your Investigation Plan with osint_notes_add, \
then begin Phase 0 — run the first tool for target type `{target_type}` \
or dispatch your first subagent.
"""


__all__ = [
    "build_initial_messages",
    "build_instruction_block",
    "build_hypothesis_block",
    "build_multi_target_block",
    "build_opening_parts",
    "build_policy_block",
    "build_system_prompt",
    "build_reference_injection",
    "looks_like_final_report",
]
