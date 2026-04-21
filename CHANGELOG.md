# Changelog

All notable changes to **CoSINT** are documented in this file.

---

## [1.0.0-beta.2] — 2026-04-21

### Added

- New runtime packages to separate responsibilities: `agent_runtime/execution/`, `agent_runtime/investigation/`, and `agent_runtime/reporting/`.
- New scanner support modules for round/state handling and context lifecycle, including `context_compression`, `context_factory`, `context_init`, `decision_types`, `constants`, and `llm_round`.
- Subagent dispatch split into focused components: `dispatch_execution`, `dispatch_preflight`, and `dispatch_records`.

### Changed

- Refactored scanner orchestration to route work through dedicated execution/investigation/reporting helpers, replacing monolithic flow files.
- Updated MCP batch execution path by moving scanner MCP handling into `agent_runtime/execution/mcp_batch.py`.
- Updated imports/exports across `agent_runtime/scanner`, `agent_runtime/scope`, `agent_runtime/subagents`, and display/context utilities to align with the new module layout.
- Refined scope evaluation behavior (`ai`/`explore` guards, policy, and rater) to match the new flow boundaries.
- Refreshed `shared/maigret_db.json` dataset.

### Fixed

- Stabilized round execution and no-tool/interactive decision paths by splitting preflight, routing, and execution concerns into dedicated modules.

### Tests

- Updated test coverage for CLI, context compression, LLM/scanner wiring, and smoke imports (`tests/test_cli.py`, `tests/test_compression.py`, `tests/test_llm.py`, `tests/test_smoke_all_modules.py`).
- Adjusted tool-related test expectations by updating `tools/company.py`, `tools/media.py`, and scraper helpers in `tools/helper/scraper_utils.py`.

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