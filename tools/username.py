"""
tools/username.py  –  Usernames & Handles
Tools: search, github, reddit
"""

import json
import tempfile
from pathlib import Path
from typing import Annotated

from mcp.server.fastmcp import FastMCP
from pydantic import Field

from shared.subprocess_runner import run, is_available
from shared.config import CUSTOM_MAIGRET_DB


def register(mcp: FastMCP) -> None:
    if is_available("maigret"):

        @mcp.tool(annotations={"readOnlyHint": True})
        async def osint_username_search(
            username: Annotated[
                str, Field(description="Username or handle to search for")
            ],
            top_sites: Annotated[
                int,
                Field(
                    description="Number of top sites to check ranked by Alexa (default: 500). Use 0 for all 3000+ sites.",
                    ge=0,
                    le=3000,
                ),
            ] = 500,
        ) -> str:
            """Search a username across 3000+ platforms via Maigret. Returns claimed profiles with URLs and metadata.

            IMPORTANT — FALSE POSITIVE RATE: Maigret detects username existence by checking if a
              URL returns a valid response. Some platforms return 200 for any username regardless
              of whether an account exists. Treat every hit as a candidate, not a confirmation.
            Verification pass (mandatory before treating any hit as confirmed):
              Use osint_scraper_fetch on the profile URL — check for real bio, non-default
              display name, and any user activity. Default/empty content = false positive, discard.
            Maigret already runs socid_extractor internally — do NOT call osint_social_extract
              on URLs from this tool's output (redundant, adds no new information).
            top_sites=500 (default) covers the most relevant platforms. Use 0 for all 3000+ sites
              only on high-priority targets where thoroughness justifies the runtime cost.
            Requires: pip install maigret
            """

            with tempfile.TemporaryDirectory(prefix="maigret_") as outdir:
                outdir_path = Path(outdir)
                outfile = outdir_path / f"report_{username}_simple.json"

                args = ["maigret", username, "-J", "simple", "-fo", outdir]
                if top_sites == 0:
                    args.append("-a")
                else:
                    args += ["--top-sites", str(top_sites)]
                if CUSTOM_MAIGRET_DB:
                    args += ["--db", Path(__file__).parent.parent / CUSTOM_MAIGRET_DB]

                try:
                    await run(*args, timeout=300)

                    if not outfile.exists():
                        matches = sorted(outdir_path.glob("report_*_simple.json"))
                        if not matches:
                            return f"Maigret produced no output file for '{username}'."
                        outfile = matches[0]

                    with outfile.open(encoding="utf-8") as f:
                        data = json.load(f)

                    lines = [f"Profiles for '{username}':\n"]

                    for site, info in sorted(
                        data.items(), key=lambda x: x[1].get("rank") or 9999
                    ):
                        if info.get("status", {}).get("status") != "Claimed":
                            continue

                        url = info.get("url_user", "N/A")
                        rank = info.get("rank")
                        http = info.get("http_status")
                        tags = info.get("status", {}).get("tags", [])
                        ids = {
                            k: v
                            for k, v in info.get("status", {}).get("ids", {}).items()
                            if v
                        }

                        rank_str = f"#{rank}" if rank else "?"
                        line = f"  {rank_str:>6}  {site} {url}"

                        if http and http != 200:
                            line += f"  [HTTP {http}]"
                        if tags:
                            line += f"  ({', '.join(tags)})"

                        lines.append(line)

                        for k, v in ids.items():
                            lines.append(f"  {''} {k}: {v}")

                    if len(lines) == 1:
                        return f"No profiles found for '{username}'."

                    lines.insert(1, f"Found {len(lines) - 1} profile(s):\n")
                    return "\n".join(lines)

                except Exception as e:
                    return f"Maigret error: {e}"
