"""
tools/email.py  –  Email Analysis
Tools: breach_check, validate, reputation, social_accounts, header_analyze
"""

import re
from typing import Annotated
import dns.resolver

from mcp.server.fastmcp import FastMCP
from pydantic import Field

from shared import config
from shared.http_client import get, OsintRequestError
from shared.rate_limiter import rate_limit
from shared.subprocess_runner import run, is_available


async def _mx_fallback(email: str) -> list[str]:
    domain = email.split("@", 1)[1]
    try:
        records = dns.resolver.resolve(domain, "MX")
        mx = sorted(str(r.exchange).rstrip(".") for r in records)
        return [
            "\n── MX Fallback (DNS) ──",
            f"Domain:  {domain}",
            f"MX:      {', '.join(mx) if mx else 'none'}",
            f"Deliverable (MX exists): {bool(mx)}",
        ]
    except Exception as e:
        return [f"\n── MX Fallback (DNS) ──\nFailed: {e}"]


def register(mcp: FastMCP) -> None:
    if config.HIBP_KEY or config.LEAKCHECK_KEY:

        @mcp.tool(annotations={"readOnlyHint": True})
        async def osint_email_breach_check(
            email: Annotated[
                str, Field(description="Email address to check for data breaches")
            ],
        ) -> str:
            """Check an email address against breach databases via HaveIBeenPwned and/or LeakCheck.

            Returns: list of breaches with name, date, data_classes (fields exposed), and source.
            Read the pattern, not just the count:
              - Breaches spanning many years → long-lived real account, high attribution confidence
              - Multiple breaches in a short window → account was likely compromised/credential-stuffed
              - Service names in breach records → reveals historical platform usage; cross-reference
                with current platform presence for continuity
              - username field in breach data → often differs from email prefix; pivot both
            Do NOT use for: checking if a password was leaked — that tool is disabled.
            Requires: HIBP_KEY for HaveIBeenPwned and/or LEAKCHECK_KEY for LeakCheck.
            """
            email = email.strip().lower()
            lines: list[str] = [f"Breach check for {email}:\n"]

            if config.HIBP_KEY:
                try:
                    await rate_limit("hibp")
                    data = await get(
                        f"https://haveibeenpwned.com/api/v3/breachedaccount/{email}",
                        headers={
                            "hibp-api-key": config.HIBP_KEY,
                            "User-Agent": "osint-mcp/1.0",
                        },
                        params={"truncateResponse": "false"},
                        max_retries=1,
                    )
                    if isinstance(data, list):
                        lines.append(
                            f"── HaveIBeenPwned: {len(data)} breach(es) found ──"
                        )
                        for b in data:
                            lines.append(
                                f"\nName:          {b.get('Name')}\n"
                                f"Domain:        {b.get('Domain')}\n"
                                f"Date:          {b.get('BreachDate')}\n"
                                f"Affected:      {b.get('PwnCount', 0):,}\n"
                                f"Data classes:  {', '.join(b.get('DataClasses', []))}\n"
                                f"Verified:      {b.get('IsVerified')}"
                            )
                except OsintRequestError as e:
                    if e.status == 404:
                        lines.append("── HaveIBeenPwned: No breaches found ✓")
                    else:
                        lines.append(f"HaveIBeenPwned error: {e.message}")

            if config.LEAKCHECK_KEY:
                try:
                    await rate_limit("leakcheck")
                    data = await get(
                        "https://leakcheck.io/api/public",
                        params={"check": email},
                    )
                    found = data.get("found", 0)
                    sources = data.get("sources", [])
                    lines.append(f"\n── LeakCheck: {found} result(s) ──")
                    for s in sources:
                        lines.append(
                            f"  {s.get('name', 'N/A')} ({s.get('date', 'N/A')})"
                        )
                except OsintRequestError as e:
                    lines.append(f"\nLeakCheck error: {e.message}")

            return "\n".join(lines)

    @mcp.tool(annotations={"readOnlyHint": True})
    async def osint_email_validate(
        email: Annotated[str, Field(description="Email address to validate")],
    ) -> str:
        """Check if an email address is deliverable, disposable, and real via Usercheck.com + Mailcheck.ai.

        Returns: deliverable (bool), disposable (bool), role_account (bool), public_domain (bool),
          spam_flag, domain_age_in_days, mx_providers, and did_you_mean (suggested correction).
        Key fields:
          did_you_mean → check the suggested address too; may be the real address with a typo
          domain_age_in_days → very recent domain = purpose-built or throwaway identity
          role_account → info@/support@ addresses are not individual identity anchors
          mx_providers → reveals email provider even when domain is custom
        Undeliverable but syntactically valid → alias, abandoned, or privacy address; note and continue.
        Uses Usercheck.com (free, optional API key).
        """
        email = email.strip().lower()
        lines: list[str] = [f"Email validation for {email}:\n"]

        try:
            await rate_limit("usercheck")
            data = await get(f"https://api.usercheck.com/email/{email}", max_retries=1)
            lines += [
                "\n── Usercheck.com ──",
                f"Disposable:   {data.get('disposable', 'N/A')}",
                f"Domain:       {data.get('domain', 'N/A')}",
                f"MX:           {data.get('mx', 'N/A')}",
                f"Alias:        {data.get('alias', 'N/A')}",
                f"Spam:         {data.get('spam', 'N/A')}",
                f"Domain Age:   {data.get('domain_age_in_days', 'N/A')} days",
            ]
            if data.get("did_you_mean"):
                lines.append(f"Did you mean: {data['did_you_mean']}")
        except OsintRequestError as e:
            if e.status == 429:
                lines += await _mx_fallback(email)

        return "\n".join(lines)

    if config.EMAILREP_KEY:

        @mcp.tool(annotations={"readOnlyHint": True})
        async def osint_email_reputation(
            email: Annotated[
                str, Field(description="Email address to check for reputation")
            ],
        ) -> str:
            """Check the reputation of an email address via EmailRep.io.

            Returns: spam score, blacklist status, credential_leak flag, domain reputation,
              SPF/DMARC status, and linked social profiles.
            Key pivot: linked_profiles field — direct identity pivots when available.
            Anomaly signals: high spam score on an address with no breach history = likely used for
              bulk outreach; blacklist presence on a corporate domain = flag for domain investigation.
            Do NOT use as the sole basis for any confidence claim — use as corroboration only.
            """
            email = email.strip().lower()
            headers: dict[str, str] = {"Key": config.EMAILREP_KEY}

            try:
                await rate_limit("emailrep")
                data = await get(f"https://emailrep.io/{email}", headers=headers)
            except OsintRequestError as e:
                return f"EmailRep error: {e.message}"

            details = data.get("details", {})
            profiles = ", ".join(details.get("profiles", [])) or "None"
            return (
                f"Email:              {data.get('email')}\n"
                f"Reputation:         {data.get('reputation')}\n"
                f"Suspicious:         {data.get('suspicious')}\n"
                f"References:         {data.get('references')}\n\n"
                f"── Details ──\n"
                f"Blacklisted:        {details.get('blacklisted')}\n"
                f"Malicious Activity: {details.get('malicious_activity')}\n"
                f"Malicious Recent:   {details.get('malicious_activity_recent')}\n"
                f"Credentials Leaked: {details.get('credentials_leaked')}\n"
                f"Leaked Recent:      {details.get('credentials_leaked_recent')}\n"
                f"Data Breach:        {details.get('data_breach')}\n"
                f"First Seen:         {details.get('first_seen')}\n"
                f"Last Seen:          {details.get('last_seen')}\n\n"
                f"── Domain ──\n"
                f"Domain Exists:      {details.get('domain_exists')}\n"
                f"Domain Reputation:  {details.get('domain_reputation')}\n"
                f"New Domain:         {details.get('new_domain')}\n"
                f"Days Since Created: {details.get('days_since_domain_creation')}\n"
                f"Suspicious TLD:     {details.get('suspicious_tld')}\n\n"
                f"── Deliverability ──\n"
                f"Deliverable:        {details.get('deliverable')}\n"
                f"Free Provider:      {details.get('free_provider')}\n"
                f"Disposable:         {details.get('disposable')}\n"
                f"Accept All:         {details.get('accept_all')}\n"
                f"Valid MX:           {details.get('valid_mx')}\n\n"
                f"── Security ──\n"
                f"Spoofable:          {details.get('spoofable')}\n"
                f"SPF Strict:         {details.get('spf_strict')}\n"
                f"DMARC Enforced:     {details.get('dmarc_enforced')}\n"
                f"Spam:               {details.get('spam')}\n\n"
                f"── Profiles ──\n{profiles}"
            )

    if is_available("holehe"):

        @mcp.tool(annotations={"readOnlyHint": True})
        async def osint_email_social_accounts(
            email: Annotated[str, Field(description="Email address")],
        ) -> str:
            """Find services an email address is registered with via Holehe CLI.

            Returns: list of platforms where the email has a registered account (confirmed or likely).
            Each confirmed platform hit is a pivot to a full username investigation.
            Niche platform hits (not just Twitter/Facebook) are higher-confidence identity signals —
              people don't create niche accounts by accident.
            Do NOT use to verify Maigret/osint_username_search hits — those already use socid_extractor.
            Requires: pip install holehe
            """
            email = email.strip().lower()
            try:
                result = await run(
                    "holehe", "--only-used", "--no-color", email, timeout=180
                )
                if not result.stdout:
                    return f"Holehe returned no results for {email}."
                # Cleanup to prevent AI from using credits as results
                result = (
                    result.stdout.split(email)[1]
                    .split("[+] Email used")[0]
                    .replace("****************************", "")
                    .strip()
                )
                return f"Holehe results for {email}:\n\n{result}"
            except Exception as e:
                return f"Holehe error: {e}"

    @mcp.tool(annotations={"readOnlyHint": True})
    async def osint_email_header_analyze(
        raw_header: Annotated[
            str,
            Field(
                description="Raw email header (everything from 'Received:' to 'Subject:')"
            ),
        ],
    ) -> str:
        """Analyze raw email headers for routing path, originating IP, SPF, DKIM and DMARC results.

        Returns: full hop chain with timestamps, originating IP, SPF/DKIM/DMARC verdicts,
          sending mail server, and any anomalies detected.
        Key pivot: originating IP → full IP investigation chain (often more reliable than any
          claimed location). Trace the full hop chain — look for the first non-trusted relay.
        Anomaly signals: SPF/DKIM fail on a corporate address; webmail origin (Gmail/Outlook)
          for a claimed corporate sender; timezone offset in timestamps inconsistent with claimed location.
        Pure local analysis — no API key required. Pass the raw header text as input.
        """
        lines: list[str] = ["── Email Header Analysis ──\n"]

        received = re.findall(
            r"Received:\s*(.+?)(?=Received:|$)", raw_header, re.DOTALL | re.IGNORECASE
        )
        if received:
            lines.append("Routing path (oldest first):")
            for i, hop in enumerate(reversed(received), 1):
                hop_clean = " ".join(hop.split())[:500]
                lines.append(f"  Hop {i}: {hop_clean}")

        ips = re.findall(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", raw_header)
        private_ranges = (
            "10.",
            "172.16.",
            "172.17.",
            "172.18.",
            "172.19.",
            "172.20.",
            "172.21.",
            "172.22.",
            "172.23.",
            "172.24.",
            "172.25.",
            "172.26.",
            "172.27.",
            "172.28.",
            "172.29.",
            "172.30.",
            "172.31.",
            "192.168.",
            "127.",
        )
        public_ips = [
            ip for ip in ips if not any(ip.startswith(p) for p in private_ranges)
        ]
        if public_ips:
            lines.append(
                f"\nPublic IPs in header: {', '.join(dict.fromkeys(public_ips))}"
            )

        important = {
            "From": r"^From:\s*(.+)$",
            "Reply-To": r"^Reply-To:\s*(.+)$",
            "Return-Path": r"^Return-Path:\s*(.+)$",
            "Message-ID": r"^Message-ID:\s*(.+)$",
            "X-Originating-IP": r"X-Originating-IP:\s*(.+)$",
            "X-Mailer": r"X-Mailer:\s*(.+)$",
            "User-Agent": r"User-Agent:\s*(.+)$",
        }
        lines.append("\n── Key Fields ──")
        for label, pattern in important.items():
            m = re.search(pattern, raw_header, re.MULTILINE | re.IGNORECASE)
            if m:
                lines.append(f"{label:20}: {m.group(1).strip()[:500]}")

        lines.append("\n── Authentication ──")
        for auth in ["spf", "dkim", "dmarc"]:
            m = re.search(rf"{auth}=(\S+)", raw_header, re.IGNORECASE)
            lines.append(f"{auth.upper():8}: {m.group(1) if m else 'not found'}")

        return "\n".join(lines)

    if is_available("ghunt"):

        @mcp.tool(annotations={"readOnlyHint": True})
        async def osint_google_account_scan(
            email: Annotated[
                str,
                Field(description="Google account email address to scan with GHunt"),
            ],
        ) -> str:
            """Enumerate Google account information using GHunt CLI.

            Returns: account existence, Google Maps activity, YouTube data, public profile info,
              and linked services where visible.
            Key pivot: profile photo URL → osint_media_reverse_image_search.
            Only use for @gmail.com addresses or confirmed Google Workspace domains.
            Skip entirely for non-Google email providers — results will be empty.
            Requires GHunt installed and configured with valid cookies (cookies.json).
            """
            email = email.strip().lower()

            try:
                # No external API, so use default rate limiter bucket to avoid abuse
                await rate_limit("default")
                result = await run("ghunt", "email", email, timeout=300)
            except Exception as e:
                return f"GHunt error: {e}"

            if not result.stdout and not result.stderr:
                return f"GHunt returned no output for {email}."

            output = result.stdout or ""
            errors = result.stderr or ""

            # Combine stdout and stderr, keeping them distinguishable
            text_parts: list[str] = [f"GHunt scan for {email}:\n"]
            if output.strip():
                output = output.split("You are up to date !")[1]
                text_parts.append("── Output ──")
                text_parts.append(output.strip())
            if errors.strip():
                text_parts.append("\n── Errors (stderr) ──")
                text_parts.append(errors.strip())

            return "\n".join(text_parts)
