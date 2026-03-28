# Skills Folder

Skill files are Markdown documents injected into the agent's system prompt.

---

## Folder Structure

```
skills/
  core/       ← Loaded for every investigation, depending on investigation type (correlation vs single-target)
  depth/      ← Loaded based on --depth flag (quick or deep)
  subagents/  ← Loaded by the root agent when subagents activate
  README.md
```

---

## core/

Always loaded. These files establish the investigator's reasoning model, pivot logic, and evidence standards across
every scan.

| File             | Purpose                                                                               |
|------------------|---------------------------------------------------------------------------------------|
| `general.md`     | Master OSINT investigator protocol — phases, pivot logic, confidence rules, narration |
| `reasoning.md`   | Investigator reasoning patterns, anomaly detection, and `ANOMALY:` narration format   |
| `correlation.md` | Cross-artifact correlation rules — how to link findings and build evidence chains     |

---

## depth/

Controls how aggressively the agent investigates. Loaded based on `--depth`.

| File       | Flag            | Purpose                                                                       |
|------------|-----------------|-------------------------------------------------------------------------------|
| `quick.md` | `--depth quick` | Focused scan: Steps 1–4 only, max 3 pivots, skip expensive tools              |
| `deep.md`  | `--depth deep`  | Exhaustive scan: all phases, all pivots, Phase 3+4 tools, second-order pivots |

---

## subagents/

Loaded by the root agent when a specialist subagent is dispatched. Each file is the skill prompt for one subagent.

### Investigation subagents

| File                         | Subagent       | What it investigates                                                 |
|------------------------------|----------------|----------------------------------------------------------------------|
| `subagent_infrastructure.md` | Infrastructure | Domain, DNS, certificates, IP, and network infrastructure            |
| `subagent_social.md`         | Social         | Social platform profiles, identity signals, cross-platform links     |
| `subagent_email.md`          | Email          | Email address as an OSINT seed — identity, breach exposure, accounts |
| `subagent_username.md`       | Username       | Online handles — cross-platform identity picture                     |
| `subagent_person.md`         | Person         | Real-world persons via public records, web presence, platform data   |
| `subagent_company.md`        | Company        | Company ownership, structure, and linked individuals                 |
| `subagent_phone.md`          | Phone          | Phone number identity and fraud risk                                 |
| `subagent_leaks.md`          | Leaks          | Breach databases, paste sites, and exposed secrets                   |
| `subagent_geo.md`            | Geo            | Physical location resolution and validation                          |
| `subagent_media.md`          | Media          | Image/media metadata, provenance, and identity signals               |
| `subagent_finance.md`        | Finance        | Crypto wallet flows, transaction patterns, blockchain counterparties |
| `subagent_scraper.md`        | Scraper        | Contact data, legal notices, operator identity, hidden paths         |

### Reasoning subagents

These subagents have no tools — they reason over evidence passed to them and return a structured assessment.

| File                            | Subagent          | What it does                                                                                 |
|---------------------------------|-------------------|----------------------------------------------------------------------------------------------|
| `subagent_entity_resolution.md` | Entity Resolution | Determines whether two or more seeds belong to the same real-world entity                    |
| `subagent_evidence_linker.md`   | Evidence Linker   | Connects confirmed artifacts into explicit evidence chains                                   |
| `subagent_validator.md`         | Validator         | Applies confidence scoring and contradiction checks to a set of findings                     |
| `subagent_budget_guard.md`      | Budget Guard      | Assesses efficiency and makes a stop/continue recommendation based on tool calls vs findings |
| `subagent_tool_strategist.md`   | Tool Strategist   | Called when stalled — reasons over what has been tried and recommends next steps             |

### Report subagent

| File                             | Subagent           | What it does                                                                             |
|----------------------------------|--------------------|------------------------------------------------------------------------------------------|
| `subagent_report_synthesizer.md` | Report Synthesizer | Writes the final structured investigation report from all accumulated notes and evidence |