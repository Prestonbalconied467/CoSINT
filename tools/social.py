"""
tools/social.py  –  Social Media Platform Tools
Tools: extract
"""

from typing import Annotated

import httpx
import socid_extractor
from mcp.server.fastmcp import FastMCP
from pydantic import Field

from shared.rate_limiter import rate_limit
from .socials import route


def register(mcp: FastMCP) -> None:
    @mcp.tool(annotations={"readOnlyHint": True})
    async def osint_social_extract(
        url: Annotated[
            str,
            Field(
                description=(
                    "Any social-platform URL to extract profile data from. "
                    "Supports 30+ platforms via dedicated handlers (GitHub, Reddit, "
                    "Instagram, Twitter/X, TikTok, Bluesky, GitLab, Bitbucket, Steam, "
                    "Chess.com, Lichess, HackerNews, Keybase, PyPI, npm, Stack Overflow, "
                    "ORCID, Dev.to, Linktree, Docker Hub, YouTube, Twitch, Spotify, "
                    "Last.fm, SoundCloud, Flickr, Medium, VK, Tumblr, Pastebin, "
                    "Gravatar, Duolingo, Lobste.rs). "
                    "Falls back to socid_extractor for any other URL."
                )
            ),
        ],
    ) -> str:
        """Extract social media profile data from a URL.

        For supported platforms a dedicated handler returns rich structured output
        (profile fields, email pivots, cross-platform links, ratings, etc.).

        Dedicated handlers (selected highlights):
          github.com/<user>          → name, bio, emails from commits, repos
          reddit.com/u/<user>        → karma, subreddit fingerprint, post history
          instagram.com/<user>       → bio, follower counts, tagged accounts
          twitter.com/<user>         → bio, metrics, pinned tweet, user ID
          chess.com/member/<user>    → real name, country, ratings
          lichess.org/<user>         → bio, country, ratings
          keybase.io/<user>          → cryptographic identity proofs
          linktr.ee/<user>           → all aggregated social links
          … and 20+ more

        Falls back to socid_extractor for any URL without a dedicated handler.
        Do NOT call on URLs already returned by osint_username_search — Maigret
          runs socid_extractor internally; calling it again adds nothing new.
        """
        if not url.startswith("http"):
            url = f"https://{url}"

        # Try dedicated platform handlers first
        result, platform = await route(url)
        if result is not None:
            return f"[{platform}]\n\n{result}"

        # Fall back to socid_extractor for unrecognised URLs
        try:
            headers = {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                )
            }
            await rate_limit("default")
            async with httpx.AsyncClient(follow_redirects=True, timeout=20) as client:
                resp = await client.get(url, headers=headers)
            results = socid_extractor.extract(resp.text)

            if not results:
                return f"No social IDs found at {url}."

            lines = [f"Extracted from {url}:\n"]
            for k, v in results.items():
                lines.append(f"  {k:30} {v}")
            return "\n".join(lines)

        except Exception as e:
            return f"There was an error: {e}"
