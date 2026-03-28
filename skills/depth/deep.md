---
name: depth_deep
description: Deep scan mode — rules and goals for a comprehensive, exhaustive investigation.
---

# Deep Scan Mode

## Goal

Build a complete, high-fidelity intelligence picture by working the way a real investigator
does: broad first, then deep on what earns it. Tool budget is finite — spend it where the
signal is, not uniformly across everything.

---

## Investigative Phases

### Phase 1 — Broad Enumeration (1–2 rounds)

Run all Phase 0 and Phase 1 tools applicable to the target type. The goal here is
*discovery*, not explanation. You are casting a wide net to find out what exists.

- Do not full-chain anything yet.
- Do not write lengthy analysis of each result as it comes in.
- At the end of Phase 1, output a ranked artifact list:
    - `[HIGH]` — strong ownership signal, active, cross-verified, or directly exposed
    - `[MED]` — plausible, single-source, or indirectly attributed
    - `[LOW]` — weak signal, generic, or unattributable

This list is your investigation plan for Phase 2. Do not skip it.

### Phase 2 — Signal Triage

Before going deeper on anything, assess what you have:

- Which artifacts are independently verifiable?
- Which appear across multiple sources (cross-corroboration)?
- Which are surprising given what you know about the target?
- Which are likely noise?

Promote to Phase 3 only if you can state a specific reason. "It exists" is not a reason.
"It appeared in two independent sources and the domain part matches the target's known
employer" is a reason.

**Documenting skipped leads:** When skipping a lead, record it using the notes system with
the `skip` tag so it survives context compression and appears in the final report:

```
osint_notes_add(title="SKIP: [artifact type] [value]", content="Reason: [why skipped]", tags="skip")
```

Use the `skip` tag specifically — do not use `pivot`. The pivot tracker treats `pivot`-tagged
notes as unfollowed leads and will re-queue them. `skip` is terminal.

### Phase 3 — Depth on High-Signal Artifacts

Full pivot chains only for `[HIGH]` artifacts. `[MED]` artifacts get one level of follow-up.
`[LOW]` artifacts are noted and closed.

**Full chains by artifact type:**

- **Email** → validate → breach → reputation → social accounts → domain part
- **Domain** → WHOIS → DNS → subdomains → certificates → Wayback → IP history → tech fingerprint → scrape
- **IP** → geolocation → ASN → reputation → VPN check → reverse DNS → open ports (if warranted)
- **Username** → cross-platform search → verification pass → GitHub → Reddit
- **Phone** → lookup → web dorks → paste check
- **Wallet** → chain-specific → multi-chain → web mentions → off-chain identity pivots
- **Person name** → fullname lookup → username derivation → court records → darknet check → news
- **Company** → registry → financials → employees → domain chain → tech stack → job postings

If a `[MED]` artifact produces a `[HIGH]` result during its one-level follow-up, promote it
to full chain immediately and note the promotion.

### Phase 4 — Cross-Linking and Second-Order Pivots

After Phase 3 chains are complete, look across them for connections:

- Same value appearing in multiple independent chains (e.g. same IP hosting two domains)
- Shared infrastructure indicators (analytics IDs, certificate SANs, registrar patterns)
- Temporal correlations (registration dates, first-seen dates, activity windows)

When you find a cross-link, state it explicitly:
> "Domain X resolves to IP Y, which also appeared in the certificate chain for domain Z.
> Both were registered within 48 hours of each other — suggests common operator."

Second-order pivots (pivots of pivot results) only on confirmed `[HIGH]` leads from Phase 3.
Third-order requires a strong specific justification — document it before proceeding.

**Phase 4 high-signal tools** (run when justified, not by default):

- `osint_network_open_ports` — suspicious IP or confirmed self-hosted infrastructure
- `osint_leak_github_secrets` — GitHub repo directly linked to target
- `osint_google_account_scan` — Gmail or Google Workspace address confirmed

---

## Reasoning Style

**Non-interactive:** After Phase 1 and after each Phase 3 chain completes, output a brief
deduction — what this confirms, what it contradicts, what it opens. Flag anomalies
explicitly. No inner monologue between individual tool calls.

**Interactive:** Think out loud at phase transitions and when something unexpected appears.
Explain *why* you are choosing one pivot over another. Narrate surprises as you hit them:
> "The WHOIS came back clean but the creation date is 3 days before the campaign started.
> That's not random. Checking certificate history for sibling domains registered in the
> same window."

Crucially: do not ask the operator what to do next. Explain your reasoning and execute.
The operator is an observer, not a co-pilot.

---

## What Deep Mode Does NOT Mean

- Do not full-chain every artifact regardless of signal strength.
- Do not run Phase 3 tools on `[LOW]` artifacts to increase coverage numbers.
- Do not repeat tool calls with the same arguments.
- Do not run tools that clearly don't apply to the target type.
- Thoroughness means covering what matters completely — not covering everything equally.

---

## Conflict Resolution

When two tools contradict each other on the same fact:

1. Note the conflict explicitly — do not pick one silently.
2. Run a third tool to break the tie if one exists.
3. Tag `[UNVERIFIED]` until resolved.

---

## Stopping Rule

Stop when one of the following conditions is met:

**Mode 2 (Hypothesis) / Mode 3 (Correlation) — Mission Success**
You have a `[HIGH]` confidence verdict (Confirmed or Refuted) backed by ≥1 Tier 1 source
or ≥2 Tier 2 sources. Do not keep running tools once the verdict is settled.

**Mode 4 (Open Investigation) — Signal Loss**
No new `[HIGH]` or `[MED]` artifact discovered in the last 3 full rounds of diverse
tool execution. Absence of record after 3 rounds is a finding.

**Mode 1 (Full Profile) — Total Exhaustion**

- All Phase 1 tools run for the target type.
- Every `[HIGH]` artifact fully chained.
- Every `[MED]` artifact followed one level.
- All Phase 3 tools run or explicitly skipped with a documented reason (use `skip` tag).
- Every dead end documented.

When unsure whether to stop — run one more round. Thoroughness matters more than brevity.

---

## Report Expectations

- **Executive summary:** 4–6 sentences covering the full intelligence picture, not a list
  of tools run.
- **Key findings:** grouped by confidence tier, each with an evidence reference.
- **Anomalies:** every anomaly flagged during the investigation, resolved or not.
- **Evidence chains:** full multi-hop chains showing how artifacts connect, with EV-xxxx IDs,
  source tier (T1–T5), and recency label.
- **Pivots taken:** complete list with rationale for each promotion decision.
- **Pivots skipped:** explicit list with reason — this is as important as pivots taken.
- **Recommendations:** specific tool-level follow-up actions for a human analyst.
- **Tools used / skipped:** full accounting.