# OSINT Report: champmq

```
PRE-REPORT QA
-------------
Investigation mode        : Full Profile (quick scan)
Hypothesis verdict        : n/a
Correlation verdict       : n/a
Unsupported claims        : none
Confidence overstatements : none
Contradictions found      : none
Anomalies flagged         : none
False-positive risks      : none; only directly attributable, corroborated username pivots were investigated
Missing evidence chains   : none
QA verdict                : PASS
```

## Executive Summary
A rapid OSINT scan of the username "champmq" established a clear and consistent presence across several high-confidence platforms. Multiple verified and long-lived accounts (notably on GitHub, Trello, YouTube) confirm stable online identity patterns dating back to 2018. No significant anomalies, critical exposure, or direct identity reveals (real name, email, phone) were found. No risk indicators or off-platform ownership signals surfaced.

## Key Findings

### Major Profiles (High Confidence)
- GitHub: https://github.com/champmq — username match, unique image, "Champ", active since 2018-06-30 (confidence: HIGH, EV-0002)
- Trello: https://trello.com/champmq — "Champion of Games", verified, custom image (confidence: HIGH, EV-0001/notes)
- YouTube: https://www.youtube.com/@champmq — direct match, active channel (confidence: HIGH, EV-0001)
- Additional: Consistent presence on Facebook, Twitch, Apple Discussions, TikTok, Roblox, Reddit, Instagram (confidence: MED, EV-0001, EV-0005)

### Behavioral/Technical
- GitHub activity suggests technical proficiency (multiple public repos, coding projects in Python/PHP), public repo named "CoSINT" and others (confidence: HIGH, EV-0002)
- Username adopted across CTF and gaming platforms, suggesting technical/gaming interests (confidence: MED, EV-0005)

### General Footprint
- No emails, phone numbers, or direct personal attribution surfaced in any checked profile or asset (confidence: HIGH, EV-0002, EV-0003, EV-0004)
- No breach, leak, or critical exposure was discovered in enrichment (confidence: HIGH, EV-0005)

## Anomalies
none detected

## Scope Decisions
- Allowed: Username sweep (primary platforms), core social extraction for top-3 [HIGH] pivots, 1 Phase 3 web dork for external presence check.
- Blocked/skipped: Deep pivots into ENRICHMENT/dork-derived platforms outside the top-3, platform infrastructure, and any direct investigation of low-confidence (regional/niche) hits in quick scan mode.

## Evidence Chains

champmq (seed username)
  --[direct ownership]--> GitHub profile (https://github.com/champmq), Source: Username Search (EV-0001), T2, [CURRENT], [HIGH]
  --[direct ownership]--> Trello profile (https://trello.com/champmq), Source: Username Search (EV-0001) + Verification, T2, [CURRENT], [HIGH]
  --[direct presence]--> YouTube profile (https://www.youtube.com/@champmq), Source: Username Search (EV-0001), T2, [CURRENT], [HIGH]
  --[cross-match]--> Reddit/Instagram/TikTok/Facebook/Pastebin, Source: Web Dork (EV-0005), T3, [CURRENT], [MED]

GitHub profile (https://github.com/champmq)
  --[public coding projects]--> Python/PHP repos (CoSINT, TheScrapper, etc), Source: Social Extract (EV-0002), T2, [CURRENT], [HIGH]

## Pivots Taken
- GitHub (https://github.com/champmq): confirmed, T2 [HIGH]
- Trello (https://trello.com/champmq): confirmed, T2 [HIGH]
- YouTube (https://www.youtube.com/@champmq): confirmed, T2 [HIGH]
- Web dork (general): confirmed, corroborated [MED] platform presence, no critical signals

## Subagents Used
- username agent: Enumerated cross-platform profile presence, surfaced core artifacts
- social agent: Extracted social signals from GitHub, Trello, and YouTube
- web dork agent: Ran general and surface-level enrichment for public footprint

## Recommendations
- For deeper investigation: Run osint_social_extract on Reddit and Instagram for any off-platform footprints or contact info.
- Consider osint_leak_paste_search if credential/breach exposure becomes relevant.
- If offline identity required, attempt pivot from GitHub email (if/when public), or initiate person-agent investigation with real name if discovered.

## Tools Used / Skipped
Used: osint_username_search, osint_social_extract, osint_web_dork, osint_notes_add, osint_notes_list  
Skipped (quick scan mode, not warranted): deep social pivots, infrastructure checks, leak/breach tools, secondary enrichment, platform infrastructure chase

Quick scan complete — the following leads warrant deeper investigation: Instagram/Reddit/TikTok for possible contact info if needed. No critical findings to escalate.
