"""
test_cli.py - Runs all CLI argument combinations and validates output.

Pass criteria:
  1. Process exits with code 0
  2. A report file is created in reports/ (unless --no-report is passed)
  3. Expected keywords are found in stdout (per-test expectations)

Results are written to test_results.log
"""

import subprocess
import sys
import os
import glob
import time
import threading
from datetime import datetime

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

CLI = "cosint.py"
PYTHON = sys.executable
LOG_FILE = "test_results.log"
REPORTS_DIR = "reports"
TIMEOUT = None  # no timeout — scans run until completion

# ---------------------------------------------------------------------------
# Test definitions
# ---------------------------------------------------------------------------
# Each test is a dict:
#   name        - human-readable label
#   args        - list of CLI args (no "python cosint.py" prefix)
#   no_report   - True if --no-report is passed (skip report file check)
#   keywords    - strings that MUST appear in stdout to pass
#   skip_reason - if set, test is skipped with this message
TESTS = [
    # ------------------------------------------------------------------
    # Basic target types — each is a distinct, publicly documented subject
    # ------------------------------------------------------------------
    {
        # Jacob Appelbaum — security researcher, Tor developer, prominent OSINT subject
        "name": "username - ioerror (Jacob Appelbaum)",
        "args": ["ioerror", "--no-interactive"],
        "keywords": ["ioerror"],
    },
    {
        # Kim Dotcom — Megaupload founder, high-profile legal history, lots of public data
        "name": "person - Kim Dotcom",
        "args": ["Kim Dotcom", "--no-interactive"],
        "keywords": ["Kim"],
    },
    {
        # NSO Group — maker of Pegasus spyware, highly documented in open sources
        "name": "company - NSO Group",
        "args": ["NSO Group", "--no-interactive", "--type", "company"],
        "keywords": ["NSO"],
    },
    {
        # Bitcoin genesis block wallet — Satoshi's first ever coins, never moved
        "name": "crypto - Bitcoin genesis wallet (Satoshi)",
        "args": [
            "1A1zP1eP5QGefi2DMPTfTL5SLmv7Divf",
            "--no-interactive",
            "--type",
            "crypto",
        ],
        "keywords": ["1A1zP1eP5QGefi2DMPTfTL5SLmv7Divf"],
    },
    # ------------------------------------------------------------------
    # --type override — intentionally unusual combos to test auto-detect fallback
    # ------------------------------------------------------------------
    {
        # .onion-adjacent domain — tests how the tool handles privacy-infra domains
        "name": "domain with --type domain - archive.org",
        "args": ["archive.org", "--type", "domain", "--no-interactive"],
        "keywords": ["archive.org"],
    },
    {
        # Cloudflare's 1.1.1.1 — well-documented anycast IP, good for infra assertions
        "name": "ip with --type ip - 1.1.1.1 (Cloudflare)",
        "args": ["1.1.1.1", "--type", "ip", "--no-interactive"],
        "keywords": ["1.1.1.1"],
    },
    # ------------------------------------------------------------------
    # --depth — same target, different depth, compare report richness
    # ------------------------------------------------------------------
    {
        # Shodan.io: ironic choice — the scanner being scanned
        "name": "depth quick - shodan.io",
        "args": ["shodan.io", "--depth", "quick", "--no-interactive"],
        "keywords": ["shodan.io"],
    },
    {
        "name": "depth deep - shodan.io",
        "args": ["shodan.io", "--depth", "deep", "--no-interactive"],
        "keywords": ["shodan.io"],
    },
    # ------------------------------------------------------------------
    # --passive-only — should avoid port scans, DNS brute force etc.
    # ------------------------------------------------------------------
    {
        # EFF: digital rights org, passive-only is thematically appropriate
        "name": "passive-only - eff.org",
        "args": ["eff.org", "--passive-only", "--no-interactive"],
        "keywords": ["eff.org"],
    },
    # ------------------------------------------------------------------
    # --skip-social
    # ------------------------------------------------------------------
    {
        # Snowden: huge social footprint — skipping social tests that branch
        "name": "skip-social - snowden",
        "args": ["snowden", "--skip-social", "--no-interactive"],
        "keywords": ["snowden"],
    },
    # ------------------------------------------------------------------
    # --skip-breaches
    # ------------------------------------------------------------------
    {
        # RockYou2021 wordlist author's domain — breach-adjacent but skip breach tools
        "name": "skip-breaches - leakedsource.com",
        "args": ["leakedsource.com", "--skip-breaches", "--no-interactive"],
        "keywords": ["leakedsource"],
    },
    # ------------------------------------------------------------------
    # --instruction — shapes the investigation focus
    # ------------------------------------------------------------------
    {
        # Cellebrite: mobile forensics company — instruction targets infra only
        "name": "instruction - cellebrite.com infra focus",
        "args": [
            "cellebrite.com",
            "--instruction",
            "Map hosting infrastructure and certificate history only",
            "--no-interactive",
        ],
        "keywords": ["cellebrite"],
    },
    # ------------------------------------------------------------------
    # --scope-mode variants — same target, all four modes
    # ------------------------------------------------------------------
    {
        # haveibeenpwned.com: well-documented breach-notification service
        "name": "scope-mode strict - haveibeenpwned.com",
        "args": ["haveibeenpwned.com", "--scope-mode", "strict", "--no-interactive"],
        "keywords": ["haveibeenpwned"],
    },
    {
        "name": "scope-mode guided - haveibeenpwned.com",
        "args": ["haveibeenpwned.com", "--scope-mode", "guided", "--no-interactive"],
        "keywords": ["haveibeenpwned"],
    },
    {
        "name": "scope-mode ai - haveibeenpwned.com",
        "args": ["haveibeenpwned.com", "--scope-mode", "ai", "--no-interactive"],
        "keywords": ["haveibeenpwned"],
    },
    {
        "name": "scope-mode explore - haveibeenpwned.com",
        "args": ["haveibeenpwned.com", "--scope-mode", "explore", "--no-interactive"],
        "keywords": ["haveibeenpwned"],
    },
    # ------------------------------------------------------------------
    # --targets (multi-target enrichment)
    # ------------------------------------------------------------------
    {
        # Ross Ulbricht: Silk Road founder — username + known associated domain
        "name": "multi-target enrichment - Ross Ulbricht",
        "args": [
            "Ross Ulbricht",
            "--targets",
            "dreadpirateroberts",
            "silkroad",
            "--no-interactive",
        ],
        "keywords": ["Ulbricht"],
    },
    # ------------------------------------------------------------------
    # --correlate-targets (correlation mode — make AI prove the link)
    # ------------------------------------------------------------------
    {
        # Is "thegrugq" (security researcher) linked to grugq.com?
        "name": "correlate-targets - thegrugq username vs grugq.com",
        "args": [
            "thegrugq",
            "--targets",
            "grugq.com",
            "--correlate-targets",
            "--no-interactive",
        ],
        "keywords": ["grugq"],
    },
    # ------------------------------------------------------------------
    # --no-report (no report file should be created)
    # ------------------------------------------------------------------
    {
        # Wikileaks: high-profile, but we're just testing the flag behaviour
        "name": "no-report flag - wikileaks.org",
        "args": ["wikileaks.org", "--no-report", "--no-interactive"],
        "no_report": True,
        "keywords": ["wikileaks"],
    },
    # ------------------------------------------------------------------
    # --out custom path
    # ------------------------------------------------------------------
    {
        # Bellingcat: open-source investigation org, fitting subject for an OSINT tool
        "name": "custom --out path - bellingcat.com",
        "args": [
            "bellingcat.com",
            "--out",
            "reports/test_bellingcat.md",
            "--no-interactive",
        ],
        "custom_report": "reports/test_bellingcat.md",
        "keywords": ["bellingcat"],
    },
    # ------------------------------------------------------------------
    # --open (open-ended investigation — no hypothesis, evidence decides)
    # ------------------------------------------------------------------
    {
        # Hacking Team: Italian surveillance vendor whose 400GB leak is public record
        "name": "open-ended mode - hackingteam.com",
        "args": [
            "hackingteam.com",
            "--open",
            "--scope-mode",
            "explore",
            "--no-interactive",
        ],
        "keywords": ["hackingteam"],
    },
    # ------------------------------------------------------------------
    # --verbose
    # ------------------------------------------------------------------
    {
        # Pastebin: massive OSINT pivot source, verbose output should be rich
        "name": "verbose flag - pastebin.com",
        "args": ["pastebin.com", "--verbose", "--no-interactive"],
        "keywords": ["pastebin"],
    },
    # ------------------------------------------------------------------
    # Combinations
    # ------------------------------------------------------------------
    {
        # Spamhaus: anti-spam org — passive + no social + no breaches is natural here
        "name": "passive + skip-breaches + skip-social - spamhaus.org",
        "args": [
            "spamhaus.org",
            "--passive-only",
            "--skip-breaches",
            "--skip-social",
            "--no-interactive",
        ],
        "keywords": ["spamhaus"],
    },
    {
        # Quad9 privacy DNS — deep infra dive with ASN focus
        "name": "deep + instruction + scope explore - 9.9.9.9 (Quad9)",
        "args": [
            "9.9.9.9",
            "--depth",
            "deep",
            "--instruction",
            "Focus on ASN, BGP routing, and abuse history",
            "--scope-mode",
            "explore",
            "--no-interactive",
        ],
        "keywords": ["9.9.9.9"],
    },
]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

RESET = "\033[0m"
GREEN = "\033[32m"
RED = "\033[31m"
YELLOW = "\033[33m"
CYAN = "\033[36m"
BOLD = "\033[1m"
BOX_H = chr(9472)
BOX_V = chr(9474)


def color(text, code):
    return f"{code}{text}{RESET}"


def report_exists_for(target: str, before: float) -> bool:
    """Return True if a new report file appeared in REPORTS_DIR after `before`."""
    if not os.path.isdir(REPORTS_DIR):
        return False
    for f in glob.glob(os.path.join(REPORTS_DIR, "*.md")):
        if os.path.getmtime(f) >= before:
            return True
    return False


def run_test(test: dict, index: int, total: int) -> dict:
    name = test["name"]
    args = test["args"]
    no_report = test.get("no_report", False)
    custom_rep = test.get("custom_report")
    keywords = test.get("keywords", [])
    skip_reason = test.get("skip_reason")

    print(f"\n{color(f'[{index}/{total}]', CYAN)} {color(name, BOLD)}")

    if skip_reason:
        print(color(f"  SKIPPED: {skip_reason}", YELLOW))
        return {
            "name": name,
            "status": "SKIP",
            "reason": skip_reason,
            "elapsed": 0,
            "stdout": "",
            "stderr": "",
        }

    cmd = [PYTHON, CLI] + args
    print(f"  CMD: {' '.join(cmd)}")
    print(color(f"  {BOX_H * 54}", CYAN))

    t0 = time.time()
    snapshot_before = time.time() - 0.5  # small buffer

    stdout_lines = []
    stderr_lines = []

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

        def stream(pipe, store, col):
            for line in pipe:
                line = line.rstrip("\n")
                store.append(line)
                print(f"  {color(BOX_V, col)} {line}")
            pipe.close()

        t_out = threading.Thread(target=stream, args=(proc.stdout, stdout_lines, RESET))
        t_err = threading.Thread(
            target=stream, args=(proc.stderr, stderr_lines, YELLOW)
        )
        t_out.start()
        t_err.start()
        t_out.join()
        t_err.join()
        proc.wait()

    except Exception as e:
        msg = str(e)
        print(color(f"  FAIL: {msg}", RED))
        return {
            "name": name,
            "status": "FAIL",
            "reason": msg,
            "elapsed": 0,
            "stdout": "",
            "stderr": "",
        }

    print(color(f"  {BOX_H * 54}", CYAN))
    elapsed = round(time.time() - t0, 1)
    stdout = "\n".join(stdout_lines)
    stderr = "\n".join(stderr_lines)
    combined = stdout + stderr
    returncode = proc.returncode

    failures = []

    # 1. Exit code
    if returncode != 0:
        failures.append(f"exit code {returncode}")

    # 2. Report file check
    if custom_rep:
        if not os.path.isfile(custom_rep):
            failures.append(f"custom report not found: {custom_rep}")
    elif no_report:
        if report_exists_for(args[0], snapshot_before):
            failures.append("report file was created despite --no-report")
    else:
        if not report_exists_for(args[0], snapshot_before):
            failures.append("no report file was created in reports/")

    # 3. Keyword check in output
    for kw in keywords:
        if kw.lower() not in combined.lower():
            failures.append(f"keyword not found in output: '{kw}'")

    if failures:
        reason = "; ".join(failures)
        print(color(f"  FAIL ({elapsed}s): {reason}", RED))
        return {
            "name": name,
            "status": "FAIL",
            "reason": reason,
            "elapsed": elapsed,
            "stdout": stdout,
            "stderr": stderr,
        }

    print(color(f"  PASS ({elapsed}s)", GREEN))
    return {
        "name": name,
        "status": "PASS",
        "reason": "",
        "elapsed": elapsed,
        "stdout": stdout,
        "stderr": stderr,
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    total = len(TESTS)
    results = []
    suite_start = time.time()

    print(color(f"\n{'=' * 60}", BOLD))
    print(color(f"  osint_mcp CLI test runner  —  {total} tests", BOLD))
    print(color(f"  Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", BOLD))
    print(color(f"{'=' * 60}", BOLD))

    for i, test in enumerate(TESTS, 1):
        r = run_test(test, i, total)
        results.append(r)

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------
    passed = sum(1 for r in results if r["status"] == "PASS")
    failed = sum(1 for r in results if r["status"] == "FAIL")
    skipped = sum(1 for r in results if r["status"] == "SKIP")

    total_elapsed = round(time.time() - suite_start, 1)

    print(f"\n{color('=' * 60, BOLD)}")
    print(
        color(
            f"  Results: {passed} passed  {failed} failed  {skipped} skipped  (total: {total_elapsed}s)",
            BOLD,
        )
    )
    print(color(f"{'=' * 60}", BOLD))

    # ------------------------------------------------------------------
    # Write log
    # ------------------------------------------------------------------
    with open(LOG_FILE, "w", encoding="utf-8") as f:
        f.write(
            f"osint_mcp CLI test run — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
        )
        f.write(f"{'=' * 60}\n\n")
        f.write(
            f"SUMMARY: {passed} passed  {failed} failed  {skipped} skipped  (total: {total_elapsed}s)\n\n"
        )
        f.write(f"{'=' * 60}\n\n")

        for r in results:
            status = r["status"]
            f.write(f"[{status}] {r['name']}  ({r.get('elapsed', 0)}s)\n")
            if r["reason"]:
                f.write(f"  Reason:  {r['reason']}\n")
            if r["stdout"]:
                f.write("  STDOUT:\n")
                for line in r["stdout"].splitlines()[-40:]:  # last 40 lines
                    f.write(f"    {line}\n")
            if r["stderr"] and status == "FAIL":
                f.write("  STDERR:\n")
                for line in r["stderr"].splitlines()[-20:]:
                    f.write(f"    {line}\n")
            f.write("\n")

    print(f"\n  Log written to: {color(LOG_FILE, CYAN)}\n")
    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()

