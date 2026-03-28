"""
tools/phone.py  –  Phone Numbers
Tools: lookup
"""

from typing import Annotated

from mcp.server.fastmcp import FastMCP
from pydantic import Field

from shared import config
from shared.http_client import get, OsintRequestError
from shared.rate_limiter import rate_limit
from shared.subprocess_runner import run, is_available


def register(mcp: FastMCP) -> None:
    if config.NUMVERIFY_KEY or is_available("phoneinfoga"):

        @mcp.tool(annotations={"readOnlyHint": True})
        async def osint_phone_lookup(
            phone: Annotated[
                str,
                Field(
                    description="Phone number in E.164 format, e.g. '+4915123456789'"
                ),
            ],
        ) -> str:
            """Full information for a phone number: carrier, line type, country, and geolocation via NumVerify + PhoneInfoga.

            Returns: carrier name, line_type (mobile/landline/VoIP/prepaid), country, region,
              and sometimes a registered name.
            Interpret line type immediately — it shapes the rest of the investigation:
              contract mobile → tied to real identity via carrier KYC; strongest anchor type
              prepaid mobile → anonymous purchase possible; harder to attribute
              VoIP (Google Voice, Twilio, TextNow) → likely throwaway or deliberate privacy tool
              landline → often business or residential address; pair with geo pivot
            Normalize to E.164 before calling: strip spaces/dashes, add country code.
              Example: '0171 123 4567' (DE) → '+491711234567'
            Carrier mismatch with claimed location = foreign SIM, roaming, or SIM swap — flag it.
            Requires: NUMVERIFY_KEY in .env (PhoneInfoga CLI optional for extended output).
            """
            phone = phone.strip()
            lines: list[str] = [f"Phone lookup for {phone}:\n"]

            if config.NUMVERIFY_KEY:
                try:
                    await rate_limit("default")
                    data = await get(
                        "http://apilayer.net/api/validate",
                        params={
                            "access_key": config.NUMVERIFY_KEY,
                            "number": phone,
                            "country_code": "",
                            "format": "1",
                        },
                    )
                    lines += [
                        "── NumVerify ──",
                        f"Valid:           {data.get('valid', False)}",
                        f"Intl. format:    {data.get('international_format', 'N/A')}",
                        f"Local format:    {data.get('local_format', 'N/A')}",
                        f"Country:         {data.get('country_name', 'N/A')} ({data.get('country_code', '')})",
                        f"Dialing code:    {data.get('country_prefix', 'N/A')}",
                        f"Carrier:         {data.get('carrier', 'N/A')}",
                        f"Line type:       {data.get('line_type', 'N/A')}",
                        f"Location:        {data.get('location', 'N/A')}",
                    ]
                except OsintRequestError as e:
                    lines.append(f"NumVerify error: {e.message}")
            else:
                lines.append("NumVerify: no key (NUMVERIFY_KEY)")

            if is_available("phoneinfoga"):
                try:
                    result = await run("phoneinfoga", "scan", "-n", phone, timeout=60)
                    if result.stdout:
                        lines.append(f"\n── PhoneInfoga ──\n{result.stdout}")
                except Exception as e:
                    lines.append(f"\nPhoneInfoga error: {e}")

            return "\n".join(lines)
