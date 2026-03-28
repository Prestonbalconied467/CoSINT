---
name: depth_quick
description: Quick scan mode — rules and constraints for a fast, focused investigation.
---

# Quick Scan Mode

## Goal

Produce a useful, accurate intelligence snapshot in the fewest tool calls possible.
Cover the essential surface area for the target type. Do not chase every lead.

---

## Reasoning Style

Tight. One-line intent before each tool call, nothing more.
Do not narrate deductions between steps. Do not think out loud.
Flag anomalies in a single line if they appear — do not pursue them unless they meet
the escalation threshold below.

---

## What to Run

- All Step 1 tools from the primary target skill — always.
- Steps 2–4 only if directly relevant to the target type.
- You MUST pivot on every [HIGH] artifact from Step 1 before wrapping up.
  Pivot on [MED] artifacts only if they appear in at least two independent results.
  The 3-pivot budget is a ceiling — not a target, not optional.
- One Phase 3 enrichment tool maximum — choose the highest-signal one for the target type.

## What to Skip

| Tool / action                          | Rule                                                   |
|----------------------------------------|--------------------------------------------------------|
| `osint_domain_subdomains`              | Skip — too broad                                       |
| `osint_domain_wayback`                 | Skip unless domain is <30 days old or looks suspicious |
| `osint_network_open_ports`             | Never in quick mode                                    |
| `osint_leak_github_secrets`            | Never in quick mode                                    |
| `osint_crypto_wallet_multi`            | Skip unless chain-specific tool returns nothing        |
| `osint_public_academic_search`         | Skip unless target is clearly academic                 |
| `osint_public_bundestag_search`        | Skip unless target is clearly political                |
| Certificate transparency deep dives    | Skip                                                   |
| Second-order pivots (pivots of pivots) | Never — stop at depth 1                                |

---

## Pivot Budget

- **Maximum 3 pivots** total from the seed target.
- If a pivot reveals new artifacts, note them in the report — do NOT investigate further.
- **Exception:** if a pivot returns a `[HIGH]` confidence criminal/fraud signal,
  escalate one level only, then stop.

---

## Critical Finding Escalation

Quick mode pivot budget and stopping rules are suspended immediately when any of the
following are found:

- **Live credentials** — an active username/password or API key for a service the target
  currently uses. Flag as CRITICAL, note the service and access scope, stop investigation
  and surface immediately.
- **Sanctions match** — OFAC or equivalent sanctioned entity or individual confirmed.
  Note the sanctions list, the matching field, and the confidence level. Do not continue
  normal investigation — escalate to operator immediately.
- **CSAM indicator** — any signal suggesting child sexual abuse material. Stop all tool
  calls immediately and escalate to operator.
- **Imminent harm signal** — credible threat of violence or self-harm linked to the target.
  Escalate to operator immediately.

For all critical findings: write `CRITICAL: [type] — [one sentence description]` at the
top of the report, before all other sections. Do not bury critical findings in the body.

---

## Per-Tool Decision Rule

Before every tool call ask:

1. Is this in Steps 1–4 for this target type? → Run it.
2. Did a prior result directly name this as a strong lead? → Run it once.
3. Is this Phase 3 or Phase 4? → Skip unless conditions 1 or 2 are met.
4. Have I called this with the same or similar argument already? → Skip.

---

## Artifact Ranking

After Step 1 tools complete, output a brief ranked list before proceeding:

- `[HIGH]` — strong ownership signal, active, or directly exposed
- `[MED]` — plausible but single-source or indirectly attributed
- `[LOW]` — weak signal, generic, or unattributable

This is your pivot shortlist. Only `[HIGH]` and `[MED]` artifacts count against your 3-pivot budget.
`[LOW]` artifacts are noted and closed — do not investigate further.

---

## Conflict Resolution

When two tools contradict each other on the same fact:

1. Note the conflict in a single line.
2. Tag `[UNVERIFIED]` — do not spend a tool call resolving it unless the artifact is `[HIGH]`.
3. If `[HIGH]`: run one tiebreaker tool, then accept the majority result.

---

## Stopping Rule

**Mode 2 (Hypothesis) / Mode 3 (Correlation) — Mission Success**
Stop when you reach a `[HIGH]` confidence verdict (Confirmed or Refuted) backed by
≥1 Tier 1 source or ≥2 Tier 2 sources. Do not keep running tools once the verdict is settled.

**Mode 4 (Open Investigation) — Signal Loss**
Stop after 2 consecutive full rounds where no new `[HIGH]` artifact is discovered.
Absence of public record after 2 rounds is itself a finding.

**Mode 1 (Full Profile) — Scope Complete**
Stop when all of the following are true:

- Steps 1–4 complete for the primary target.
- Every [HIGH] artifact followed one level deep (up to 3 total).
- One Phase 3 enrichment check run.
- No new `[HIGH]` signals in the last 2 rounds.

---

## Report Expectations

- Executive summary: 2–3 sentences only.
- Key findings: top 5 facts, each with a confidence tag.
- Evidence chains: direct links only (A → B) — no multi-hop speculation.
- Anomalies: list any flagged, even if not pursued.
- Pivots skipped: explicit list with reason — given quick mode's aggressive skip rules this is required, not optional.
- End with: "Quick scan complete — the following leads warrant deeper investigation: [list]"