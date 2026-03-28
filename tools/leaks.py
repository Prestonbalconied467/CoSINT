"""
tools/leaks.py  –  Credentials & Data Leaks
Tools: email_check, paste_search, github_secrets
"""

import asyncio
import json
from typing import Annotated

from mcp.server.fastmcp import FastMCP
from pydantic import Field

from shared import config
from shared.http_client import get, OsintRequestError
from shared.rate_limiter import rate_limit
from shared.subprocess_runner import run, is_available


def register(mcp: FastMCP) -> None:
    # osint_leak_password_check is intentionally disabled.
    #
    # @mcp.tool(annotations={"readOnlyHint": True})
    # async def osint_leak_password_check(
    #         password: Annotated[str, Field(description="Password to check (NOT transmitted – only SHA1 prefix)")],
    # ) -> str:
    #     """Check if a password appears in known data breaches via HaveIBeenPwned Passwords API.
    #
    #     Uses k-anonymity: only the first 5 characters of the SHA1 hash are sent.
    #     The plaintext password never leaves your local machine. No API key required.
    #     """
    #     sha1 = hashlib.sha1(password.encode("utf-8")).hexdigest().upper()
    #     prefix, suffix = sha1[:5], sha1[5:]
    #
    #     try:
    #         await rate_limit("hibp")
    #         text = await get_text(
    #             f"https://api.pwnedpasswords.com/range/{prefix}",
    #             headers={"Add-Padding": "true"},
    #         )
    #     except OsintRequestError as e:
    #         return f"HIBP Passwords error: {e.message}"
    #
    #     count = 0
    #     for line in text.splitlines():
    #         if ":" in line:
    #             hash_suffix, n = line.split(":", 1)
    #             if hash_suffix.upper() == suffix:
    #                 count = int(n.strip())
    #                 break
    #
    #     if count == 0:
    #         return "✓ Password not found in any known breach (checked via k-anonymity)."
    #     return (
    #         f"PASSWORD COMPROMISED!\n"
    #         f"This password has been found {count:,} times in data breaches.\n"
    #         f"SHA1 prefix: {prefix}... (plaintext was never transmitted)\n"
    #         f"Recommendation: Change this password immediately!"
    #     )

    if config.INTELX_KEY:

        @mcp.tool(annotations={"readOnlyHint": True})
        async def osint_leak_paste_search(
            query: Annotated[
                str, Field(description="Search term: email, username, domain, etc.")
            ],
        ) -> str:
            """Search paste sites for a query term via IntelX API.

            Returns: paste previews with source URL, date, and matching content.
            QUOTA WARNING: IntelX has limited free-tier requests. Use only for specific identifiers:
              email addresses, confirmed usernames, phone numbers, wallet addresses.
            Do NOT use for: full names, company names, or broad keyword searches — use
              osint_web_dork(dork_type=paste_exposure) instead (faster, no quota cost).
            Key action: when preview text contains PII, fetch the full paste via
              osint_scraper_fetch on the paste URL to extract all available fields.
            Anomaly: multiple pastes with the same PII but slight variations = data was scraped
              and republished; treat as one source, not multiple independent confirmations.
            Requires: INTELX_KEY in .env (free tier has limited requests)
            """

            from shared.http_client import post as _post

            try:
                await rate_limit("intelx")
                search_data = await _post(
                    "https://free.intelx.io/intelligent/search",
                    headers={"x-key": config.INTELX_KEY},
                    post_json={
                        "term": query,
                        "maxresults": 100,
                        "media": 0,
                        "target": 0,
                        "timeout": 20,
                    },
                    max_retries=1,
                )
                search_id = search_data.get("id")
                if not search_id:
                    return "IntelX: No search ID received."

                await asyncio.sleep(3)

                await rate_limit("intelx")
                results = await get(
                    "https://free.intelx.io/intelligent/search/result",
                    headers={"x-key": config.INTELX_KEY},
                    params={"id": search_id, "limit": 100},
                )
            except OsintRequestError as e:
                return f"IntelX error: {e.message}"

            records = results.get("records", [])
            if not records:
                return f"No paste results for '{query}' on IntelX."

            lines = [f"IntelX paste search for '{query}' ({len(records)} results):\n"]
            for r in records:
                lines.append(
                    f"Name:   {r.get('name', 'N/A')}\n"
                    f"Storage I: {r.get('storageid', 'N/A')}\n"
                    f"Date:   {r.get('date', 'N/A')}\n"
                    f"Bucket: {r.get('bucket', 'N/A')}\n"
                    f"{'─' * 40}"
                )
            return "\n".join(lines)

    if is_available("trufflehog3") or is_available("trufflehog"):

        @mcp.tool(annotations={"readOnlyHint": True})
        async def osint_leak_github_secrets(
            repo_url: Annotated[
                str,
                Field(
                    description="GitHub repository URL, e.g. 'https://github.com/user/repo'"
                ),
            ],
        ) -> str:
            """Scan a GitHub repository's git history for exposed secrets via TruffleHog.

            Returns: secret type, matched pattern, file path, commit hash, committer email, and timestamp.
            Beyond the secrets themselves:
              committer_email → often a pre-OPSEC personal email; pivot with full email chain
              commit_timestamp → establishes working hours and likely timezone
              secret type prefix (STRIPE_, AWS_, TWILIO_) → reveals what services the developer uses
              revoked/expired credentials → still confirm service usage and establish timeline
            Run on any GitHub repo linked to the target — not just when secrets are expected.
            Do NOT use for: GitHub profile investigation — use osint_social_extract instead.
            Requires: pip install trufflehog
            """

            try:
                tool_name = "trufflehog"
                if is_available("trufflehog3"):
                    tool_name = "trufflehog3"
                result = await run(
                    tool_name,
                    "--format",
                    "json",
                    repo_url,
                    timeout=180,
                )
            except Exception as e:
                return f"truffleHog error: {e}"

            if not result.stdout:
                return f"✓ No secrets found in {repo_url}."

            lines = [f"Secrets found in {repo_url}:\n"]
            output = json.loads(result.stdout)
            trufflehog_skip_paths = {
                "poetry.lock",
                "package-lock.json",
                "yarn.lock",
                "go.sum",
                "Cargo.lock",
            }
            seen = set()

            for finding in output:
                # Remove duplicates
                secret = finding.get("secret") or ""
                if secret in seen:
                    continue
                seen.add(secret)
                # Skip findings in common lockfiles which often contain false positives
                if any(
                    skip in (finding.get("path") or "")
                    for skip in trufflehog_skip_paths
                ):
                    continue
                try:
                    lines.append(
                        f"Rule:   {finding.get('rule', {}).get('message', 'N/A')} "
                        f"[{finding.get('rule', {}).get('severity', 'N/A')}]\n"
                        f"File:   {finding.get('path', 'N/A')}:{finding.get('line', 'N/A')}\n"
                        f"Secret: {str(finding.get('secret') or 'N/A')}\n"
                        f"Commit: {str(finding.get('commit') or 'N/A')}  {str(finding.get('date') or 'N/A')[:10]}\n"
                        f"Author: {finding.get('author') or 'N/A'}\n"
                        f"Msg:    {str(finding.get('message') or 'N/A')[:300]}\n"
                        f"{'─' * 40}"
                    )
                except json.JSONDecodeError:
                    lines.append(result.stdout)

            return "\n".join(lines)
