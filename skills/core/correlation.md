---
name: correlation
description: Entity correlation framework. Loaded when correlate_targets=True or instruction implies identity linking.
---

# Entity Correlation

## Goal

Determine whether two or more seeds are linked, and if so, **what kind of link exists**.
Do not assume. Build independent profiles first, then compare.

---

## Workflow

### Step 1 — Independent Profiles

Investigate each seed as if it's the only target. Do not let findings from one
influence how you interpret the other until both profiles are substantially complete.
Record artifacts per seed separately.

**When to stop independent profiling and move to comparison:**
Move to Step 2 when BOTH of the following are true for each seed:

- ≥3 independent artifacts confirmed (from different tools/sources)
- The primary pivot chain for the seed type is complete (e.g. for a domain: WHOIS + DNS + scrape)

Do not wait for perfect coverage on one seed before starting the other. Investigate both
in parallel, alternating rounds, so the profiles reach the comparison threshold together.

### Step 2 — Hunt for Hard Anchors

Hard anchors are the same concrete value appearing independently in both profiles.
These are the only basis for HIGH-confidence verdicts.

| Anchor type                       | Example                                                                      | Weight  | Relationship it suggests           |
|-----------------------------------|------------------------------------------------------------------------------|---------|------------------------------------|
| Shared PGP key / cryptographic ID | Same key linked to both seeds                                                | Highest | SAME_PERSON                        |
| Shared email                      | In WHOIS for domain A and commit history for username B                      | Highest | OWNS_OPERATES or SAME_PERSON       |
| Shared profile photo              | Same image via reverse search across both seeds                              | High    | SAME_PERSON                        |
| Shared phone                      | In a paste for seed A and a social profile for seed B                        | High    | SAME_PERSON                        |
| Shared IP (non-shared-hosting)    | Dedicated hosting tied to both seeds, confirmed via reverse DNS + cert + ASN | High    | OWNS_OPERATES or SAME_ORGANIZATION |
| Shared analytics/tracking ID      | Same GA/GTM ID on two domains                                                | High    | OWNS_OPERATES or SAME_ORGANIZATION |
| Shared company registration / VAT | Same legal entity number across both seeds                                   | High    | SAME_ORGANIZATION                  |

When a hard anchor is found: record it immediately, note both source tools and evidence IDs,
note which relationship type it supports, and keep investigating — one anchor supports
attribution, two or more confirms it.

### Step 3 — Collect Soft Anchors

Soft anchors raise probability but cannot prove identity or relationship type alone.
Use them to support a `LIKELY_*` verdict or to corroborate hard anchors.

- Overlapping geographic signals (same city across IP geo, EXIF, address data)
- Same technology stack or tooling choices
- Consistent time-of-day activity patterns across platforms
- Matching naming conventions in handles, domain names, or email prefixes
- Consistent writing style, vocabulary, or language quirks across platforms
- Overlapping social network (same followers, same org membership)
- Shared employer or project affiliation visible in both profiles

Tag soft anchors `[LOW]` individually. Three or more consistent soft anchors = `[MED]` combined.

### Step 4 — Check for Contradictions

Contradictions reduce confidence. Document each one explicitly.

- Non-overlapping claimed locations with no VPN/travel explanation
- Simultaneous activity on both seeds that would require two people
- Different skill levels, languages, or technical depth across profiles
- Registration or creation timelines that make co-ownership impossible
- Directly contradictory PII (two different full names, two different birthdates)
- Org registration data that places seeds under clearly distinct legal entities

A single hard contradiction + no hard anchors = `UNRELATED` or `INCONCLUSIVE`.

### Step 5 — Correlation Verdict

Verdicts have two dimensions: **relationship type** and **confidence**. Always state both.

**Note on direction:** Relationship type is directional where relevant. `OWNS_OPERATES` means
the person or entity behind seed A controls seed B — not the reverse. When the direction is
ambiguous (e.g. a person seed and a domain seed), state which is the operator and which is
the operated asset explicitly in the reasoning. Do not assume symmetry.

#### Relationship Type

Use exactly one:

| Verdict                 | Criteria                                                           | Typical anchors                                                                        |
|-------------------------|--------------------------------------------------------------------|----------------------------------------------------------------------------------------|
| `SAME_PERSON`           | Both seeds resolve to the same individual                          | Shared photo, shared personal email, shared PGP key                                    |
| `SAME_ORGANIZATION`     | Both seeds belong to the same company or org                       | Shared VAT/registration, shared org email domain confirmed to same entity              |
| `OWNS_OPERATES`         | The person or entity behind seed A owns or controls seed B         | Email in WHOIS + same person's profile, shared tracking ID across domains              |
| `AFFILIATED`            | Seeds share an employer, org, or project but are distinct entities | Same employer domain, co-authorship, org membership — not the same individual or owner |
| `INFRASTRUCTURE_SHARED` | Seeds share hosting or infra but operator identity is unconfirmed  | Shared IP on shared hosting, same CDN — anchor is too weak to confirm operator         |
| `UNRELATED`             | Hard contradiction present, no hard anchors                        | Contradictory PII, impossible timelines                                                |
| `INCONCLUSIVE`          | Insufficient evidence to decide relationship type                  | Too few anchors, no contradictions                                                     |

#### Confidence

Use exactly one:

| Confidence | Criteria                                                                      |
|------------|-------------------------------------------------------------------------------|
| `HIGH`     | ≥2 hard anchors supporting the same relationship type, no hard contradictions |
| `MED`      | 1 hard anchor OR ≥3 consistent soft anchors, no hard contradictions           |
| `LOW`      | Soft anchors only, or a single weak hard anchor with conflicting signals      |

---

## Output Format

```
Correlation verdict  : [relationship type]
Confidence          : [HIGH | MED | LOW]
Hard anchors        : [list each with source tool, EV-xxxx, and relationship type it supports — or "none"]
Soft anchors        : [list each with confidence tag — or "none"]
Contradictions      : [list each explicitly — or "none"]
Reasoning           : [2-4 sentences tying evidence to verdict — explain why this
                       relationship type, not just that a link exists. Cite specific anchors.
                       If directional (OWNS_OPERATES), state which seed is operator and which is asset.]
```

### Examples

```
Correlation verdict  : OWNS_OPERATES
Confidence          : HIGH
Hard anchors        : j@example.com in WHOIS for seed A (EV-0012, supports OWNS_OPERATES)
                      j@example.com in GitHub commits for seed B (EV-0031, supports SAME_PERSON→OWNS_OPERATES)
Soft anchors        : Matching geo (Latvia) across both seeds [LOW]
Contradictions      : none
Reasoning           : The email j@example.com appears independently in domain WHOIS for
                      seed A and in commit history for the username seed B. This confirms
                      the individual operating seed B (the person) registered and actively
                      maintains seed A (the domain). Two independent high-weight anchors
                      with no contradictions meet the threshold for HIGH confidence.
```

```
Correlation verdict  : AFFILIATED
Confidence          : MED
Hard anchors        : Shared employer domain @tvg.edu.lv visible in both profiles (EV-0044, EV-0051)
Soft anchors        : Overlapping LinkedIn connections [LOW], matching geographic signal (Talsi) [LOW]
Contradictions      : none
Reasoning           : Both seeds are associated with the same educational institution via
                      a shared employer email domain, but no PII or ownership anchor links
                      them as the same individual or as one controlling the other. The
                      relationship is professional affiliation, not identity or ownership.
```

---

## Common False Positive Traps

- **Shared hosting IP** — dozens of domains share an IP; not an anchor unless
  reverse DNS + certificate data + ASN all consistently point to the same operator.
  If uncertain, verdict is `INFRASTRUCTURE_SHARED`, not `OWNS_OPERATES`.
- **Common username** — `john_doe` exists on hundreds of platforms; handle match alone is T5 evidence.
- **Same employer email domain** — `@company.com` proves affiliation to the org, not which individual
  or whether one seed owns the other. Verdict is `AFFILIATED` at most.
- **Similar writing style** — style analysis is T5; never use as a primary anchor.
- **Same breach record** — confirms the email was in a breach, not that both seeds share an owner.
- **Premature `SAME_PERSON`** — if the anchors only confirm one person controls a domain,
  the correct verdict is `OWNS_OPERATES`, not `SAME_PERSON`. Reserve `SAME_PERSON` for
  anchors that directly identify an individual across both seeds.

When a false positive risk is present, note it in the QA block under `False-positive risks`.