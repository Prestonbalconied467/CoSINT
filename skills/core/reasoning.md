---
name: reasoning
description: Investigator instinct, creative tool usage, and anomaly detection. Loaded for every investigation.
---

# Investigative Reasoning

## Going Off-Script

Target skills define a baseline, not a ceiling. Deviate when evidence demands it.

**Triggers to go off-script:**

- A result contains something that *shouldn't be there* — an unexpected email on a corporate
  WHOIS, a personal handle reused in a commit, a phone number in a paste matching the target's area code.
- Two independent tools return the same value — convergence signals outrank the checklist.
- A result contains something the tool wasn't run to find — a developer name in an HTML comment,
  an API key in a JS bundle fetched for page text, a subdomain in a certificate you pulled for dates.
- The standard next pivot is clearly low-value but a lateral move would be high-signal.

When any trigger fires: **stop the standard chain, follow the signal, document why.**

---

## Creative Tool Usage

These tools have capabilities beyond their primary function. Use them.

**`osint_scraper_fetch`**
Not just a scraper fallback. Fetch these paths proactively on any domain under investigation:

- `/robots.txt` — often lists hidden paths, admin panels, staging URLs
- `/sitemap.xml` — full URL inventory; reveals content structure and hidden sections
- `/.well-known/` — security.txt, email verification files, OAuth metadata
- `/ads.txt`, `/app-ads.txt` — reveals publisher identity and ad network relationships
- `/humans.txt` — sometimes contains real developer names and contact info
- JS bundle files — scan for hardcoded API keys, internal endpoints, analytics IDs, author strings

**`osint_scraper_extract`**
Run proactively on high-value identity pages before falling back to WHOIS:

- `/contact`, `/impressum`, `/about`, `/team`, `/legal`, `/datenschutz`
  These pages regularly expose real names, addresses, phones, and linked social accounts that
  DNS and WHOIS will never return.

**Analytics and tracking IDs**
Every analytics/tracking ID found via `osint_domain_tech_fingerprint` is a cross-domain
attribution pivot. A shared Google Analytics UA or GTM container ID links domains to the
same operator more reliably than WHOIS data (which can be faked). Extract every ID found
and search it directly via `osint_web_search`.

**`osint_domain_wayback`**
Use on any domain that "just launched" or has suspiciously sparse history. Prior ownership,
removed staff pages, historical contact info, and content pivots are often visible even when
the current site is blank or locked down.

**`osint_web_dork` / `osint_web_search` for negative space**
Absence is a signal. A company with no press coverage, no employee footprint, and no court
records may be a shell. A developer with no public commits and no platform presence may be
intentionally obscured. Use dorks to confirm absence, not just find presence.

**`osint_leak_github_secrets`**
Beyond key scanning: commit timestamps reveal working hours and timezone. Committer emails
are high-value pivots. Branch naming conventions expose team structure. Repo creation dates
establish timelines. Run this whenever a repo is linked to the target — not just when you
expect secrets.

---

## Anomaly Detection

After every meaningful result, run this check before continuing:

**Does this fit what I'd expect?**
If not — why not? An unexpected result is more valuable than a confirming one.
State the discrepancy explicitly and decide whether to follow it immediately.

**Is anything missing that should be there?**

- Domain with no TXT records → no email infrastructure, possibly not a real operating company
- Company with no employees on Hunter → either tiny, shell, or heavily privacy-conscious
- Person with zero platform presence → deliberate OPSEC or incorrect identity target
- Wallet with no counterparty labels → either very new or privacy-focused usage

Absence of expected data is intelligence. Note it, don't skip past it.

**Does this contradict earlier findings?**
Contradictions are the highest-value signal in an investigation.

- Claimed location vs IP geolocation mismatch → VPN, SIM fraud, or false claim
- Profile says "founded 2015" but domain registered 2022 → timeline inconsistency
- Two breach records show different password hashes for the same account at the same time → data quality issue or
  account sharing

Follow contradictions before resuming the standard chain.

**Is timing suspicious?**

- Domain registered days before a known incident
- Account created immediately after a breach involving the target
- Wallet dormant for years then large movement correlating with an external event
- Certificate issued to a domain that didn't publicly exist yet

**Known red-flag patterns:**

- New domain + privacy WHOIS + no Wayback history → likely fresh operational infrastructure
- Same handle registered on 10+ platforms within a 48h window → synthetic persona creation
- Company with multiple directors, zero employees, and no financial filings → shell indicator
- VoIP number + throwaway email + brand-new accounts across all platforms → deliberate anonymization stack

**Flag every anomaly:**
`ANOMALY: [description of what is unexpected and why it matters]`

Anomalies must be listed in the Pre-Report QA block and the `## Anomalies` section of the report.
Do not smooth them over — an unexplained anomaly is an open thread, not a resolved one.

---

## When NOT to Follow an Anomaly

Not every anomaly warrants immediate pursuit. Before dropping the current chain to follow
an anomaly, apply this triage:

**Park and note — do not pursue immediately — when:**

- The anomaly is a single data point with no corroborating signal (e.g. a slightly off registration
  date with no other timeline inconsistencies)
- Following it would require ≥3 tool calls with low probability of a HIGH result
- The current chain has unfinished HIGH-confidence pivots that would be abandoned
- The anomaly type is known to produce frequent false positives (geo mismatch on VPN users,
  name collision on common names, single breach record with no other identity signal)

**Follow immediately — drop current chain — when:**

- Two or more independent signals point to the same anomaly (convergence)
- The anomaly directly contradicts a finding you were about to treat as confirmed
- It involves a sanctions match, live credential, or criminal indicator
- It would fundamentally change the investigation direction if true

When parking: `osint_notes_add(title="ANOMALY: [desc]", tags="anomaly")` and continue.
When following: document why you're deviating from the current chain before pivoting.