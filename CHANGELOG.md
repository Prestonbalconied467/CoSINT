# Changelog

All notable changes to **CoSINT** are documented in this file.

---

## [1.0.0-beta.1] — 2026-03-28

Initial release.

CoSINT is an AI-driven OSINT platform that runs structured investigations against a target — domain, email, username,
person, company, IP, phone number, or crypto address — using a coordinated set of 50+ tools and an autonomous agent
loop.

**Key features:**

- **CLI-driven investigations** — point it at a target and get a structured Markdown report plus a `.case.json` file
  with all evidence records, tool calls, and findings.
- **MCP server** — expose all tools directly to any MCP-compatible client such as Claude Desktop, without running a full
  scan.
- **50+ OSINT tools** across the categories domain, network, email, person, company, social, leaks, crypto, geo, and
  media...
- **Scope enforcement** — four modes (`strict`, `guided`, `ai`, `explore`) control how aggressively the agent is allowed
  to follow pivots outside the original target.
- **Subagent system** — specialized subagents handle focused workstreams (infrastructure, social, leaks, finance, and
  more) and a dedicated synthesizer produces the final report.
- **Structured evidence tracking** — every finding gets an `EV-xxxx` ID; a full case file is written alongside each
  report.

---

*This is a beta release. CLI flags, tool signatures, and configuration may change before 1.0.0 stable.*