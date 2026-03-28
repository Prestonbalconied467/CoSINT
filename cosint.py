"""
cosint_mcp - CLI entry point.

Parses arguments and hands off to the agent runtime.
"""

import argparse
import asyncio
import os
import sys

from shared.config import (
    DEFAULT_COMPRESSION_THRESHOLD,
    DEFAULT_EVENT_LOG_SIZE,
    DEFAULT_MAX_CONTEXT_TOKENS,
    DEFAULT_MAX_TOOL_CALLS,
    DEFAULT_MODEL,
    DEFAULT_SCOPE_MODE,
)


def _configure_utf8_stdio() -> None:
    """Best-effort UTF-8 output setup for CLI logs and rich symbols."""
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass


def main() -> None:
    _configure_utf8_stdio()
    parser = argparse.ArgumentParser(
        description="OSINT MCP CLI - root-agent driven scans via LiteLLM",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python cosint.py example.com\n"
            "  python cosint.py john@example.com --depth deep\n"
            "  python cosint.py                              # prompts for target\n"
            "  python cosint.py @handle --no-interactive\n"
            "  python cosint.py 1.2.3.4 --instruction 'owner name is John, based in Frankfurt'\n"
            "  python cosint.py 1.2.3.4 --hypothesis 'this IP belongs to the same operator as evil.com'\n"
            "  python cosint.py johndoe --targets john@example.com example.com\n"
            "  python cosint.py johndoe --targets john@example.com --correlate-targets\n"
            "  python cosint.py example.com --passive-only --skip-breaches\n"
            "  python cosint.py example.com --out custom/path/report.md\n"
            "  python cosint.py example.com --no-report\n"
        ),
    )

    # Primary target is optional — runner.py will prompt if omitted
    parser.add_argument(
        "target",
        nargs="?",
        default=None,
        help="Primary target to investigate (email / domain / media / IP / username / phone / person / wallet)",
    )

    parser.add_argument(
        "--targets",
        metavar="TARGET",
        nargs="+",
        default=None,
        help=(
            "One or more additional targets that belong to the same subject, e.g. "
            "--targets john@example.com johndoe example.com  "
            "By default the AI treats all supplied targets as confirmed identifiers for one subject and "
            "uses them together for enrichment/pivoting. Use --correlate-targets to make the AI prove the link instead."
        ),
    )

    parser.add_argument(
        "--correlate-targets",
        dest="correlate_targets",
        action="store_true",
        default=False,
        help=(
            "When multiple targets are supplied, switch from shared-subject enrichment mode to explicit "
            "correlation mode and require the AI to verify whether the targets truly belong together."
        ),
    )

    parser.add_argument(
        "--type",
        choices=[
            "email",
            "ip",
            "domain",
            "media",
            "username",
            "phone",
            "person",
            "company",
            "crypto",
        ],
        help="Optional target type override for the primary target (default: auto-detect)",
    )
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help=f"LiteLLM model string (default: {DEFAULT_MODEL})",
    )
    parser.add_argument(
        "--depth",
        default="quick",
        choices=["quick", "deep"],
        help="Scan depth: quick (focused) or deep (exhaustive, more tool calls)",
    )
    parser.add_argument(
        "--out",
        metavar="FILE",
        help="Custom path to save the report (default: reports/<target>_<timestamp>.md)",
    )
    parser.add_argument(
        "--no-report",
        dest="save_report",
        action="store_false",
        help="Disable auto-saving the report to the reports/ folder",
    )
    parser.set_defaults(save_report=True)
    parser.add_argument(
        "--instruction",
        "-i",
        metavar="TEXT",
        help=(
            "Known facts or context to seed the investigation with. "
            "Use this for background info you already have, e.g. 'her name is Jane Doe' or "
            "'he works at Acme Corp in Berlin'. Not a claim to prove — just a starting point."
        ),
    )
    parser.add_argument(
        "--hypothesis",
        metavar="TEXT",
        help=(
            "A falsifiable claim for the investigation to confirm or refute. "
            "Will be restated as a yes/no question and driven to a [HIGH] confidence verdict. "
            "Example: 'this IP belongs to the same operator as domain X'."
        ),
    )
    parser.add_argument(
        "--passive-only",
        action="store_true",
        help="Restrict the investigation to passive collection only; avoid active probing such as port scanning.",
    )
    parser.add_argument(
        "--skip-social",
        action="store_true",
        help="Do not use social-media or social-account discovery tools during the investigation.",
    )
    parser.add_argument(
        "--skip-breaches",
        action="store_true",
        help="Do not use breach, leak, or exposed-secret checks during the investigation.",
    )
    parser.add_argument(
        "--scope-mode",
        choices=["strict", "guided", "ai", "explore"],
        default=DEFAULT_SCOPE_MODE,
        help=(
            "Scope enforcement mode for tool pivots. "
            "strict: deterministic rules only — blocks anything without an explicit identifier match. "
            "guided: rules handle the obvious cases, AI breaks genuine ties. "
            "ai: AI is the sole judge with full scrutiny and a concrete attribution chain required. "
            "explore: minimal filtering, permissive AI — follows weak leads and unconfirmed threads."
        ),
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Print raw tool output and per-round token usage",
    )
    parser.add_argument(
        "--no-interactive",
        dest="interactive_root",
        action="store_false",
        help="Disable interactive prompts and generate the final report automatically",
    )
    parser.add_argument(
        "--open",
        default=False,
        action="store_true",
        help=(
            "Open-ended investigation mode. Use when you have a direction but no specific claim to prove — "
            "the evidence decides the story, not a hypothesis. "
            "Pair with --scope-mode explore for best results."
        ),
    )

    parser.set_defaults(interactive_root=True)
    parser.add_argument(
        "--max-context-tokens",
        type=int,
        default=DEFAULT_MAX_CONTEXT_TOKENS,
        help=f"Estimated context limit used for auto-compression (default: {DEFAULT_MAX_CONTEXT_TOKENS})",
    )
    parser.add_argument(
        "--compression-threshold",
        type=float,
        default=DEFAULT_COMPRESSION_THRESHOLD,
        help=(
            "Compress history when context exceeds this fraction of --max-context-tokens "
            f"(default: {DEFAULT_COMPRESSION_THRESHOLD})"
        ),
    )
    parser.add_argument(
        "--event-log-size",
        type=int,
        default=DEFAULT_EVENT_LOG_SIZE,
        help=f"Number of runtime events kept in memory (default: {DEFAULT_EVENT_LOG_SIZE})",
    )
    parser.add_argument(
        "--max-tool-calls",
        type=int,
        default=DEFAULT_MAX_TOOL_CALLS,
        dest="max_tool_calls",
        help=(
            "Maximum number of tool calls the agent may make in a single round "
            f"(default: {DEFAULT_MAX_TOOL_CALLS}). Lower this if your provider rejects large batches."
        ),
    )

    args = parser.parse_args()
    if args.correlate_targets and not args.targets:
        raise ValueError("You have to use --targets if using correlate targets.")
    if args.open and args.hypothesis:
        raise ValueError("You cant use both --hypothesis and --open.")
    if args.correlate_targets and args.hypothesis:
        raise ValueError("You cant use both --hypothesis and --correlate-targets.")
    from agent_runtime.runner import main_async

    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
