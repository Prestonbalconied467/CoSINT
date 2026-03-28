from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

from shared.config import DEFAULT_SCOPE_MODE
from .scanner import run_scan
from .targeting import detect_type
from .display import (
    print_banner,
    print_report,
    print_report_header,
    print_scan_summary,
    bold,
    cyan,
    dim,
    green,
    red,
    yellow,
    white,
)

try:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
except ImportError as exc:
    raise RuntimeError("Missing dependencies. Install litellm and mcp.") from exc

SERVER_SCRIPT = Path(__file__).resolve().parents[1] / "server.py"


async def main_async(args: argparse.Namespace) -> None:
    print_banner()

    target = (args.target or "").strip()
    policy_flags = [
        flag
        for enabled, flag in [
            (getattr(args, "passive_only", False), "passive_only"),
            (getattr(args, "skip_social", False), "skip_social"),
            (getattr(args, "skip_breaches", False), "skip_breaches"),
        ]
        if enabled
    ]

    # Prompt for target when not provided on the command line
    if not target:
        try:
            target = input(
                f"  {cyan('Target')} (email / domain / IP / username / phone / person / wallet / media): "
            ).strip()
        except (EOFError, KeyboardInterrupt):
            print("\n  Aborted.")
            return
        if not target:
            print(red("  No target provided. Exiting."))
            return

    target_type = args.type or detect_type(target)
    if target_type == "username" and target.startswith("@"):
        target = target[1:]

    # Collect all targets
    extra_targets: list[str] = list(args.targets or [])
    correlate_targets: bool = getattr(args, "correlate_targets", False)
    scope_mode: str = getattr(args, "scope_mode", DEFAULT_SCOPE_MODE)

    # Header
    print(f"  {bold('Target')}  : {bold(white(target))}")
    if extra_targets:
        joined = ", ".join(extra_targets)
        mode = (
            cyan("verify correlation")
            if correlate_targets
            else dim("assumed same entity")
        )
        print(f"  {bold('Targets')} : {bold(white(joined))}  [{mode}]")
    print(
        f"  {bold('Type')}    : {cyan(target_type)}{dim('  (auto-detected)' if not args.type else '')}"
    )
    print(f"  {bold('Depth')}   : {cyan(args.depth)}")
    print(f"  {bold('Scope')}   : {cyan(scope_mode)}")
    print(f"  {bold('Model')}   : {dim(args.model)}")
    print(f"  {bold('Tool cap')}: {dim(str(args.max_tool_calls))} per round")

    instruction = args.instruction
    if instruction:
        print(f"  {bold('Instruction')}: {dim(instruction)}")
    hypothesis = args.hypothesis
    if hypothesis:
        print(f"  {bold('Hypothesis')}: {dim(hypothesis)}")
    if policy_flags:
        print(f"  {bold('Policies')} : {dim(', '.join(policy_flags))}")

    print()
    print(dim("  Starting MCP server and connecting tools — please wait..."))

    server_params = StdioServerParameters(
        command=sys.executable,
        args=[str(SERVER_SCRIPT)],
    )

    report: str | None = None
    case_file = None
    usage = None
    stats = None
    try:
        async with stdio_client(server_params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                report, case_file, usage, stats = await run_scan(
                    session=session,
                    target=target,
                    target_type=target_type,
                    depth=args.depth,
                    model=args.model,
                    verbose=args.verbose,
                    instruction=instruction,
                    hypothesis=hypothesis,
                    extra_targets=extra_targets,
                    correlate_targets=correlate_targets,
                    policy_flags=policy_flags,
                    interactive_root=args.interactive_root,
                    max_context_tokens=args.max_context_tokens,
                    compression_threshold=args.compression_threshold,
                    event_log_size=args.event_log_size,
                    scope_mode=scope_mode,
                    open_ended=args.open,
                    max_tool_calls=args.max_tool_calls,
                )
    except KeyboardInterrupt as e:
        print(f"\n  {yellow('Scan stopped:')} {e}")
        return

    if usage is None or stats is None:
        print()
        print(f"  {yellow('[ended]')} Scan exited before producing a final report.")
        return

    # Final report
    if not report or report.strip() == "(no report generated)":
        print()
        print(f"  {yellow('[ended]')} No final report was generated.")
        print_scan_summary(usage, stats)
        return

    print_report_header()
    print_report(report)
    print_scan_summary(usage, stats)

    if args.save_report:
        if args.out:
            out_path = Path(args.out)
        else:
            # Auto-save to reports/ with a safe timestamped filename
            reports_dir = Path(__file__).resolve().parents[1] / "reports"
            reports_dir.mkdir(exist_ok=True)
            safe_target = re.sub(r"[^\w\-.]", "_", target)[:48]
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            out_path = reports_dir / f"report_{safe_target}_{timestamp}.md"

        clean = report.encode("utf-8", errors="replace").decode("utf-8")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(f"# OSINT Report: {target}\n\n{clean}\n", encoding="utf-8")

        case_path = out_path.with_suffix(".case.json")
        case_payload = case_file.to_dict()
        case_path.write_text(
            json.dumps(case_payload, indent=2, ensure_ascii=False), encoding="utf-8"
        )

        print(f"  {green('[saved]')} {bold(str(out_path))}")
        print(f"  {green('[saved]')} {bold(str(case_path))}")
        print()
