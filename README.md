# CoSINT

AI-assisted OSINT Investigator (beta release).

Verify hypotheses about an entity, run a full profile scan or find a correlation between two entites and more.
Supports scans and pivots on email, domain, media, IP, username, phone, person and wallet.

> **Note:** This is a personal hobby project, it's a work in progress and rough around the edges (there is a lot to
> clean up atm). If you notice anything
> flawed or have suggestions, please open an issue or submit a PR!

**Docs & guides:** [Wiki](https://github.com/champmq/cosint/wiki)

## Quick Start

Requires: Python 3.11 or newer

```bash
# 1. Install dependencies
python3 -m venv .venv
source .venv/bin/activate # On Windows: .venv\Scripts\activate
pip install -r requirements.txt

# 2. Configure API keys and model via the setup wizard, install playwright browsers...
python setup.py # optional --skip-keys flag to skip API key setup

# 4. Run a scan
python cosint.py example.com
python cosint.py @username --depth deep --scope-mode guided
```

See [Example](https://github.com/champmq/CoSINT/tree/main/example) for a sample report and case file.

The setup wizard walks you through setting your AI provider credentials, adding API keys (with free-tier labels),
and installing optional packages. You can re-run it at any time without overwriting existing values.

If you prefer to configure manually, copy `.env.example` to `.env` and fill in the keys you need. Missing keys only
disable the specific tools that require them, everything else keeps working.

## Model setup

CoSINT uses [LiteLLM](https://docs.litellm.ai/docs/providers) and supports any provider it supports. Set your model
and credentials in `.env`:

```bash
OSINT_MODEL=
ANTHROPIC_API_KEY=...

# Other supported providers
OPENAI_API_KEY=...
GEMINI_API_KEY=...
AZURE_API_KEY=...
```

## CLI

```
python cosint.py <target> [options]
```

| Option                                     | Description                                                                                                 |
|--------------------------------------------|-------------------------------------------------------------------------------------------------------------|
| `--depth quick\|deep`                      | Scan depth (default: `quick`)                                                                               |
| `--type <type>`                            | Override target type (`email`, `ip`, `domain`, `username`, `phone`, `person`, `company`, `crypto`, `media`) |
| `--targets <ids>`                          | Additional identifiers for the same subject                                                                 |
| `--correlate-targets`                      | Verify that `--targets` belong to the same subject before proceeding                                        |
| `--instruction "..."`                      | Extra information or instruction                                                                            |
| `--hypothesis "..."`                       | Hypothesis for the AI to follow                                                                             |
| `--scope-mode strict\|guided\|ai\|explore` | Scope enforcement policy (default: `guided`)                                                                |
| `--passive-only`                           | Skip active probing                                                                                         |
| `--skip-social`                            | Skip social platform lookups                                                                                |
| `--skip-breaches`                          | Skip breach/leak checks                                                                                     |
| `--open`                                   | Open-ended investigation, evidence drives the story, no fixed hypothesis                                    |
| `--out <path>`                             | Custom report output path                                                                                   |
| `--no-interactive`                         | Non-interactive mode, runs to completion without pausing                                                    |

Reports are written to `reports/` as a Markdown file and a `.case.json` after each scan.

## MCP server

CoSINT can also run as a standalone MCP server, exposing all 50+ tools directly to any MCP-compatible client such as
Claude Desktop without running a full CLI scan:

```bash
python server.py
```

See [`wiki/MCP-Server.md`](./wiki/MCP-Server.md) for client integration instructions.

## Testing

```bash
# Smoke test — confirms all tool modules load correctly (its a bit experimental)
python -m unittest tests.test_smoke_all_modules -v

# Full test suite
python -m pytest -q
```

## Legal Notice

Use this software only for lawful, authorised OSINT work. You are solely responsible for compliance with applicable law,
platform terms of service, and API provider policies.

## License

Licensed under the [GNU Affero General Public License v3.0](./LICENSE) (AGPL-3.0).
