"""
agent_runtime/display/output.py

Simplified terminal rendering for the live scan UI.
"""

from __future__ import annotations

import re
import textwrap

from .colors import bold, cyan, dim, green, red, yellow, white, blue
from .data import TOOL_INFO, PHASE_PATTERNS, get_phase_label
from ..models import UsageStats, ScanStats

_W = 72
_WRAP = 66

_INTENT_PREFIX_RE = re.compile(
    r"^(?:i(?:'?m| will| am going to| plan to| intend to|'ll)\s*|going to\s*|will now\s*|now (?:i'?ll |going to )?\s*|next[,:]?\s*(?:i(?:'?ll| will|'?m going to)?\s*)?|proceeding to\s*|let(?:'s| me)\s*|(?:the )?(?:next step|plan)(?: is)?(?: to)?\s*)",
    re.I,
)
_SENT_END_RE = re.compile(r"[.!?]")


def _clean_intent_line(raw: str) -> str:
    line = raw.strip().rstrip(".,;:")
    line = re.sub(r"^[-*•]\s+", "", line)
    line = _INTENT_PREFIX_RE.sub("", line).strip()
    m = _SENT_END_RE.search(line)
    if m and m.start() > 12:
        line = line[: m.start() + 1].strip()
    if line:
        line = line[0].upper() + line[1:]
    return line if len(line) >= 12 else ""


def _div() -> str:
    return dim("─" * _W)


def _wrap_print(
    prefix_plain_len: int, prefix_str: str, text: str, cont_indent: str = "    "
) -> None:
    budget = max(_WRAP - prefix_plain_len, 30)
    first_words = textwrap.wrap(text, width=budget)
    if not first_words:
        print(prefix_str)
        return
    print(prefix_str + first_words[0])
    rest = text[len(first_words[0]) :].strip()
    if rest:
        for wl in textwrap.wrap(
            rest, width=_WRAP, initial_indent=cont_indent, subsequent_indent=cont_indent
        ):
            print(wl)


def print_banner() -> None:
    print("\n" + bold(cyan("═" * _W)))
    print(
        bold(cyan("  OSINT AGENT") + white("  │  Interactive Investigation Framework"))
    )
    print(bold(cyan("═" * _W)) + "\n")


def print_phase(label: str, round_num: int) -> None:
    print("\n" + bold(cyan(f"  ┌─ {label}")) + dim(f"  round {round_num}"))
    print(dim(f"  └{'─' * (_W - 4)}"))


def print_report_header() -> None:
    print("\n" + bold(cyan("═" * _W)))
    print(bold(cyan("  INVESTIGATION REPORT")))
    print(bold(cyan("═" * _W)) + "\n")


def print_usage_footer(usage: UsageStats) -> None:
    cost_str = f"${usage.cost_usd:.4f}" if usage.cost_usd else "n/a"
    print("\n" + dim("─" * _W))
    print(
        dim(
            f"  tokens: {usage.total_tokens:,}  (prompt {usage.prompt_tokens:,} / completion {usage.completion_tokens:,})  │  cost: {cost_str}  │  compressions: {usage.compressed_events}"
        )
    )
    print(dim("─" * _W) + "\n")


def usage_line(usage: UsageStats) -> str:
    cost = f"${usage.cost_usd:.4f}" if usage.cost_usd else "n/a"
    return dim(
        f"tokens: {usage.total_tokens:,}  cost: {cost}  compressions: {usage.compressed_events}"
    )


def _tool_label(name: str) -> str:
    return TOOL_INFO.get(name, name.replace("osint_", "").replace("_", " ").title())


def _tool_args_preview(args: dict) -> str:
    priority_keys = [
        "email",
        "domain",
        "username",
        "ip",
        "address",
        "url",
        "query",
        "target",
        "name",
        "phone",
        "wallet",
        "hash",
    ]
    parts = [
        args.get(k, "").strip()
        for k in priority_keys
        if isinstance(args.get(k), str) and args.get(k).strip()
    ]
    return f"  {dim('→')} {dim(', '.join(parts[:2]))}" if parts else ""


def print_tool_start(name: str, args: dict) -> None:
    print(f"  {cyan('◆')} {bold(_tool_label(name))}{_tool_args_preview(args)}")


def print_tool_result(result: str, is_duplicate: bool = False) -> None:
    if is_duplicate:
        print(f"  {dim('  └─')} {dim('already called — cached result reused')}")
        return
    highlights = extract_highlights(result)
    if not highlights:
        first = next(
            (ln.strip() for ln in result.splitlines() if ln.strip()), "(no data)"
        )
        print(f"  {green('  └─')} {dim(first[:120])}")
        return
    for i, h in enumerate(highlights):
        print(f"  {green('  └─') if i == 0 else '           '}{h}")


def extract_highlights(result: str) -> list[str]:
    lines, text = [], result
    m = re.search(r"found\s+(\d+)\s*(profile|account|result|hit|match)", text, re.I)
    if m:
        count = int(m.group(1))
        lines.append((green if count > 0 else dim)(f"Profiles found: {count}"))
    m = re.search(r"(\d+)\s*(breach|leak|exposure|pwned)", text, re.I)
    if m:
        count = int(m.group(1))
        pfx = red("[BREACH]") if count > 0 else green("[CLEAN]")
        lines.append(f"{pfx} {yellow(str(count))} {m.group(2)}(es) found")
    m = re.search(r"full[_\s]?name[:\s]+([^\n]{3,50})", text, re.I) or re.search(
        r"fullname[:\s]+([^\n]{3,50})", text, re.I
    )
    if m:
        val = m.group(1).strip().strip("\"'")
        if val and val.lower() not in ("none", "null", "n/a", ""):
            lines.append(f"{cyan('Name:')} {val}")
    emails = re.findall(r"[\w.+\-]{2,}@[\w\-]{2,}\.[\w.]{2,}", text)
    if emails:
        lines.append(f"{cyan('Email:')} {', '.join(list(dict.fromkeys(emails))[:3])}")
    ips = re.findall(r"\b(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})\b", text)
    if ips:
        lines.append(f"{cyan('IP:')} {', '.join(list(dict.fromkeys(ips))[:4])}")
    m = re.search(r"(country|city|location|region)[:\s]+([A-Za-z ,]{3,50})", text, re.I)
    if m:
        lines.append(f"{cyan('Location:')} {m.group(2).strip()}")
    m = re.search(r"(ASN|organization|org)[:\s]+([^\n]{4,60})", text, re.I)
    if m:
        lines.append(f"{cyan('Org:')} {m.group(2).strip()}")
    m = re.search(r"(registrant|registrar|owner)[:\s]+([^\n]{4,60})", text, re.I)
    if m:
        lines.append(f"{cyan(m.group(1).title() + ':')} {m.group(2).strip()}")
    m = re.search(r"(created|registered)[:\s]+(\d{4}[^\n]{0,20})", text, re.I)
    if m:
        lines.append(f"{cyan('Registered:')} {m.group(2).strip()}")
    m = re.search(r"(\d+)\s*subdomain", text, re.I)
    if m:
        lines.append(f"{cyan('Subdomains:')} {m.group(1)} found")
    if re.search(r"\b(vpn|proxy|tor\b|datacenter|anonymiz)", text, re.I):
        lines.append(yellow("[!] Anonymization layer detected"))
    platforms = re.findall(
        r"\b(GitHub|Reddit|Twitter|Instagram|LinkedIn|TikTok|Facebook|Telegram|Discord|YouTube|Twitch)\b",
        text,
        re.I,
    )
    if platforms:
        unique_p = list(dict.fromkeys(p.title() for p in platforms))[:6]
        lines.append(f"{cyan('Platforms:')} {', '.join(unique_p)}")
    ports = re.findall(r"port[:\s]+(\d+)", text, re.I)
    if ports:
        lines.append(f"{cyan('Ports:')} {', '.join(ports[:6])}")
    if not lines:
        if re.search(
            r"(not found|no results|no data|404|error|failed|empty)", text, re.I
        ):
            lines.append(dim("No results"))
        else:
            nonempty = [ln.strip() for ln in result[:6000].splitlines() if ln.strip()][
                :2
            ]
            for ln in nonempty:
                lines.append(dim(ln[:120]))
    return lines[:6]


def print_narrative(text: str) -> None:
    prev_blank = False
    for line in text.strip().splitlines():
        line = line.strip()
        if not line:
            if not prev_blank:
                print()
            prev_blank = True
            continue
        prev_blank = False
        list_m = re.match(r"^(?:[-*•]|\d+[.)]) +(.+)", line)
        if list_m:
            content = list_m.group(1).strip()
            for wl in textwrap.wrap(
                content,
                width=_WRAP,
                initial_indent=f"    {dim('·')} ",
                subsequent_indent="      ",
            ):
                print(dim(wl))
            continue
        bold_m = re.match(r"^\*\*(.+?)\*\*:?.*$", line)
        if bold_m:
            print(f"  {bold(bold_m.group(1))}")
            continue
        if re.match(r"anomaly[\s:]+", line, re.I):
            _wrap_print(4, f"  {red('⚠')} ", yellow(line), "      ")
            continue
        if re.match(r"pivot[\s:]+", line, re.I):
            rest = re.sub(r"^pivot[\s:]+", "", line, flags=re.I).strip()
            parts = re.split(r"\s*(?:->|→)\s*", rest, maxsplit=1)
            if len(parts) == 2:
                print(
                    f"  {yellow('↪')} {yellow('PIVOT')}  {cyan(parts[0].strip())}  →  {bold(parts[1].strip())}"
                )
            else:
                _wrap_print(
                    11, f"  {yellow('↪')} {yellow('PIVOT')}  ", bold(rest), "         "
                )
            continue
        if re.match(r"phase (complete|summary)", line, re.I):
            print(f"  {green('✔')} {green(line)}")
            continue
        if re.match(r"dispatching\s+\w", line, re.I):
            print(f"  {cyan('⤷')} {cyan(line)}")
            continue
        if re.match(r"subagent\s+\w+\s+returned", line, re.I):
            print(f"  {green('⤶')} {green(line)}")
            continue
        if re.match(
            r"(checking|i'?m going to|going to|will now|now checking|next[,: ]|proceed)",
            line,
            re.I,
        ):
            _wrap_print(4, f"  {dim('·')} ", dim(line), "    ")
            continue
        if re.match(r"(found|discovered|confirmed|identified|located)", line, re.I):
            _wrap_print(4, f"  {green('✓')} ", line, "    ")
            continue
        if re.match(r"(no results?\b|nothing found|empty|not found)", line, re.I):
            print(f"  {dim('○')} {dim(line)}")
            continue
        if re.match(r"pre.?report\s+qa", line, re.I):
            print(f"\n  {bold(yellow('━' * 36))}\n  {bold(yellow(line))}")
            continue
        if re.match(r"qa verdict\s*:", line, re.I):
            verdict = line.split(":", 1)[-1].strip().upper()
            color = red
            if "PASS" in verdict:
                color = green
            print(f"  {bold(color(line))}")

            continue
        if re.match(r"escalate\s*:", line, re.I):
            _wrap_print(4, f"  {yellow('!')} ", yellow(line), "    ")
            continue
        _wrap_print(4, f"  {dim('·')} ", dim(line), "    ")


def print_report(report: str) -> None:
    for line in report.splitlines():
        s = line.rstrip()
        if s.startswith("### "):
            print(bold(white(s)))
        elif s.startswith("## "):
            print("\n" + bold(cyan(s)))
        elif s.startswith("# "):
            print(bold(white(s)))
        elif re.search(r"\[HIGH\]", s):
            print(green(s))
        elif re.search(r"\[MED\]", s):
            print(yellow(s))
        elif re.search(r"\[LOW\]|\[UNVERIFIED\]", s):
            print(dim(s))
        elif s.startswith("```") or re.match(r"\s*-{3,}|={3,}", s):
            print(dim(s))
        else:
            rendered = re.sub(r"\*\*(.+?)\*\*", lambda m: bold(m.group(1)), s)
            print(rendered)


def print_scan_startup(
    target: str,
    target_type: str,
    depth: str,
    role_label: str,
    num_tools: int,
    initial_agent_names: list[str],
) -> None:
    print(f"  {green('[OK]')} Skills loaded  ({role_label})")
    print(f"  {green('[OK]')} MCP connected  │  {bold(str(num_tools))} tools available")
    print(f"  {green('[OK]')} Subagent dispatch ready")
    print(f"  {cyan('[ROOT]')} Initial agents: {cyan(', '.join(initial_agent_names))}")
    print(dim(f"  Target: {bold(target)}  ({target_type})  depth: {depth}"))
    print(dim("  " + "─" * (_W - 2)))


def print_skills_confirmed(role_label: str) -> None:
    print(f"  {green('[OK]')} Skills confirmed  ({role_label})")


def print_warn(msg: str) -> None:
    print(f"  {yellow('[WARN]')} {msg}")


def print_info(msg: str) -> None:
    print(f"  {blue('[INFO]')} {msg}")


def print_context_note(msg: str) -> None:
    print(f"  {dim(f'[{msg}]')}")


def print_token_note(msg: str) -> None:
    print(f"  {green('[OK]')} Context Token:  ({msg})")


def print_subagent_dispatch(agent_name: str, task: str, auto: bool = False) -> None:
    tag = dim("[auto] ") if auto else ""
    header = f"  {cyan('⤷')} {tag}{cyan(agent_name)}  "
    # Wrap the task across lines — no truncation
    task_lines = textwrap.wrap(task.strip(), width=_WRAP)
    if not task_lines:
        print(header)
        return
    print(header + dim(task_lines[0]))
    # Continuation indent: align under the task text, not the arrow
    indent = "      " + ("       " if auto else "")
    for tl in task_lines[1:]:
        print(indent + dim(tl))


def print_scope_promote(kind: str, value: str, reason: str) -> None:
    print(f"  {green('[+scope]')} {kind}: {cyan(value)}  {dim('— ' + reason[:60])}")


def print_pre_report_pause(last_content: str | None) -> str:
    print("\n" + _div())
    print(f"  {yellow('▸')} {bold('Agent paused')}  {dim('— no tool calls queued')}")
    print()
    if last_content:
        preview = [ln.strip() for ln in last_content.strip().splitlines() if ln.strip()]
        for pline in preview[:4]:
            for wl in textwrap.wrap(
                pline, width=_WRAP, initial_indent="  ", subsequent_indent="  "
            ):
                print(dim(wl))
        print()
    # print_agent_paused
    print(f"  {bold('↵')}  {white('Enter')}   {dim('->')}  {cyan('final report')}")
    print(f"  {bold('✎')}  {white('type')}    {dim('->')}  {yellow('override')}")
    print(f"  {bold('^C')} {white('Ctrl+C')}  {dim('->')}  {red('stop')}")
    print("\n" + _div() + "\n")

    try:
        text = input(f"  {cyan('❯')} ").strip()
    except EOFError:
        return ""
    return text


def _extract_next_intents(
    last_content: str | None, extra_hints: list[str] | None
) -> list[str]:
    raw_candidates = list(extra_hints or [])
    if last_content:
        for raw in last_content.strip().splitlines():
            line = raw.strip()
            if not line:
                continue
            ll = line.lower()
            if re.match(
                r"(next[,::\s]|will |going to|proceed|dispatch|plan[:\s]|i('?ll| will)|i'?m going to|now i|let me|i need to|i should|ready to|about to)",
                ll,
            ) and not re.match(r"(found|confirmed|identified|discovered)", ll):
                raw_candidates.append(line)
    seen, cleaned = set(), []
    for raw in raw_candidates:
        c = _clean_intent_line(raw)
        if not c:
            continue
        key = c[:50].lower()
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(c)
        if len(cleaned) >= 3:
            break
    return cleaned


def interactive_pause(
    last_content: str | None = None,
    next_tools: list[str] | None = None,
    next_hints: list[str] | None = None,
    *,
    tools_ran: bool = True,
) -> str | None:
    findings = []
    if last_content:
        for raw in last_content.strip().splitlines():
            line = re.sub(r"^[-*•]\s+", "", raw.strip())
            if not line:
                continue
            if re.match(
                r"(found|confirmed|identified|discovered|pivot[:\s]|anomaly[:\s]|escalat|[✓✔⚠])",
                line.lower(),
            ):
                findings.append(line)
    next_intents = _extract_next_intents(last_content, next_hints)
    unique_tools = []
    if next_tools:
        seen_t = set()
        for t in next_tools:
            if t not in seen_t:
                seen_t.add(t)
                unique_tools.append(t)
    print("\n" + bold(cyan("─" * _W)))
    if tools_ran:
        count_str = (
            f"{len(unique_tools)} tool{'s' if len(unique_tools) != 1 else ''}"
            if unique_tools
            else "no tools"
        )
        print(f"  {cyan('▸')} {bold('Round complete')}  {dim(f'— {count_str} ran')}")
    else:
        count_str = (
            f"{len(unique_tools)} tool{'s' if len(unique_tools) != 1 else ''}"
            if unique_tools
            else ""
        )
        suffix = f"— {count_str} queued" if count_str else "— agent paused"
        print(f"  {cyan('▸')} {bold('Pre-execution pause')}  {dim(suffix)}")
    if unique_tools:
        print()
        if tools_ran:
            print(f"  {dim('Completed:')}")
            marker, label_color = green("✓"), dim
        else:
            print(f"  {dim('About to run:')}")
            marker, label_color = cyan("·"), dim
        for t in unique_tools[:6]:
            print(f"    {marker} {label_color(_tool_label(t))}")
        if len(unique_tools) > 6:
            print(f"    {dim(f'  … and {len(unique_tools) - 6} more')}")
    if findings:
        print(f"\n  {dim('Findings this round:')}")
        for line in findings[:5]:
            is_anomaly = bool(re.match(r"anomaly", line, re.I))
            marker_f = yellow("⚠") if is_anomaly else green("·")
            color_f = yellow if is_anomaly else (lambda s: s)
            wrapped = textwrap.wrap(line, width=_WRAP)
            if wrapped:
                print(f"    {marker_f} {color_f(wrapped[0])}")
                for wl in wrapped[1:]:
                    print(f"      {dim(wl)}")
    if next_intents:
        print(f"\n  {dim('What happens next:')}")
        for intent in next_intents:
            wrapped = textwrap.wrap(intent, width=_WRAP)
            if wrapped:
                print(f"    {cyan('→')} {wrapped[0]}")
                for wl in wrapped[1:]:
                    print(f"      {dim(wl)}")
    print("\n" + dim("  " + "─" * (_W - 2)))
    # interactive_pause
    print(f"  {bold('↵')}  {white('Enter')}   {dim('->')}  {green('continue')}")
    print(f"  {bold('✎')}  {white('type')}    {dim('->')}  {yellow('override')}")
    print(f"  {bold('^C')} {white('Ctrl+C')}  {dim('->')}  {red('stop')}")
    print(bold(cyan("─" * _W)))
    try:
        text = input(f"  {cyan('❯')} ").strip()
    except EOFError:
        return None
    return text or None


def print_scan_summary(usage: UsageStats, stats: ScanStats) -> None:
    cost_str = f"${usage.cost_usd:.4f}" if usage.cost_usd else "n/a"
    print("\n" + bold(cyan("─" * _W)))
    print(bold(cyan("  SCAN SUMMARY")))
    print(dim("─" * _W))
    print(
        f"  {dim('Tokens')}       {usage.total_tokens:,}  {dim('(prompt')} {usage.prompt_tokens:,}  {dim('/')}  {dim('completion')} {usage.completion_tokens:,}{dim(')')}  {dim('│')}  {dim('cost')} {cost_str}"
    )
    blocked_note = (
        f"  {dim('+')} {dim(str(stats.tools_blocked))} {dim('blocked')}"
        if getattr(stats, "tools_blocked", 0)
        else ""
    )
    deduped_note = (
        f"  {dim('+')} {dim(str(stats.tools_deduped))} {dim('deduped')}"
        if stats.tools_deduped
        else ""
    )
    print(
        f"  {dim('Rounds')}       {stats.rounds}  {dim('│')}  {dim('Tools run')}  {stats.tools_run}{blocked_note}{deduped_note}"
    )
    if usage.compressed_events:
        print(f"  {dim('Compressions')} {usage.compressed_events}")
    if stats.pivots_found:
        print(f"  {dim('Pivots')}       {stats.pivots_found}")
    if stats.directives_issued:
        print(f"  {dim('Directives')}   {stats.directives_issued}")
    if stats.subagents_activated:
        from collections import Counter

        counts = Counter(stats.subagents_activated)
        agent_parts = [
            f"{cyan(name)}{dim(f' ×{n}') if n > 1 else ''}"
            for name, n in counts.most_common()
        ]
        print(f"  {dim('Subagents')}    {', '.join(agent_parts)}")
    else:
        print(f"  {dim('Subagents')}    {dim('none dispatched')}")
    print(bold(cyan("─" * _W)) + "\n")


__all__ = [
    "TOOL_INFO",
    "PHASE_PATTERNS",
    "get_phase_label",
    "_W",
    "extract_highlights",
    "interactive_pause",
    "print_banner",
    "print_narrative",
    "print_phase",
    "print_report",
    "print_report_header",
    "print_scan_summary",
    "print_tool_result",
    "print_tool_start",
    "print_usage_footer",
    "usage_line",
    "print_pre_report_pause",
    "print_context_note",
    "print_scan_startup",
    "print_scope_promote",
    "print_skills_confirmed",
    "print_subagent_dispatch",
    "print_warn",
    "print_token_note",
]
