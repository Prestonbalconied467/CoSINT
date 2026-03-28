"""
tools/person.py  –  Person Research
Tools: username_search, email_to_accounts, reverse_image, fullname_lookup,
       address_lookup, darknet_check, court_records
"""

from typing import Annotated, Optional

import httpx
from bs4 import BeautifulSoup
from mcp.server.fastmcp import FastMCP
from pydantic import Field
import re

from shared import config
from shared.http_client import get, OsintRequestError
from shared.rate_limiter import rate_limit


def register(mcp: FastMCP) -> None:
    if config.FULLCONTACT_KEY:

        @mcp.tool(annotations={"readOnlyHint": True})
        async def osint_person_fullname_lookup(
            name: Annotated[str, Field(description="Full name of the person")],
            location: Annotated[
                Optional[str],
                Field(description="City/country to narrow results (optional)"),
            ] = None,
        ) -> str:
            """Search for a person by full name via FullContact API.

            Returns: social profiles, employer, job title, location, photo URLs, and metadata.
            IMPORTANT: common names produce many false positives. Always use the optional location
              parameter to narrow results when city/country is known.
            If multiple results return: build a disambiguation list before continuing —
              do not assume the first result is the target.
            Key pivot fields: social_profiles (→ username chain), employer (→ company chain),
              photo_url (→ reverse image search), email (→ full email chain).
            Requires: FULLCONTACT_KEY in .env
            """

            try:
                from shared.http_client import post as _post

                body: dict = {"fullName": name}
                if location:
                    body["location"] = location
                await rate_limit("default")
                data = await _post(
                    "https://api.fullcontact.com/v3/person.enrich",
                    headers={"Authorization": f"Bearer {config.FULLCONTACT_KEY}"},
                    post_json=body,
                )
            except OsintRequestError as e:
                return f"FullContact error: {e.message}"

            lines = [
                f"Name:         {data.get('fullName', name)}",
                f"Age:          {data.get('age', 'N/A')}",
                f"Gender:       {data.get('gender', 'N/A')}",
                f"Location:     {data.get('location', 'N/A')}",
            ]
            if data.get("organization"):
                lines.append(f"Company:      {data['organization'].get('name', 'N/A')}")
                lines.append(
                    f"Title:        {data['organization'].get('title', 'N/A')}"
                )
            if data.get("bio"):
                lines.append(f"Bio:          {data['bio'][:300]}")

            profiles = data.get("details", {}).get("profiles", {})
            if profiles:
                lines.append("\n── Social Profiles ──")
                for platform, info in list(profiles.items()):
                    lines.append(
                        f"  {platform} {info.get('url', info.get('username', 'N/A'))}"
                    )

            return "\n".join(lines)

    @mcp.tool(annotations={"readOnlyHint": True})
    async def osint_person_address_lookup(
        query: Annotated[
            str,
            Field(
                description="Address OR 'lat,lon' coordinates, e.g. '49.0069,8.4037'"
            ),
        ],
    ) -> str:
        """Geocode an address string or reverse-geocode coordinates via Nominatim/OSM.

        Returns: normalized address, coordinates, place type, and nearby context.
        Prefer osint_geo_forward for clean address → coordinate conversion.
        Use this tool specifically for address history lookups in person investigation context,
          or when the input is a messy/partial address string that needs normalization.
        No API key required. Rate limit: 1 req/sec (automatically enforced).
        """
        query = query.strip()
        await rate_limit("nominatim")
        coord_match = re.match(r"^(-?\d+\.?\d*),\s*(-?\d+\.?\d*)$", query)
        try:
            if coord_match:
                lat, lon = coord_match.group(1), coord_match.group(2)
                data = await get(
                    "https://nominatim.openstreetmap.org/reverse",
                    params={
                        "lat": lat,
                        "lon": lon,
                        "format": "json",
                        "addressdetails": 1,
                    },
                    headers={"User-Agent": "osint-mcp/1.0"},
                )
            else:
                data = await get(
                    "https://nominatim.openstreetmap.org/search",
                    params={
                        "q": query,
                        "format": "json",
                        "addressdetails": 1,
                        "limit": 3,
                    },
                    headers={"User-Agent": "osint-mcp/1.0"},
                )
                if isinstance(data, list):
                    data = data[0] if data else {}
        except OsintRequestError as e:
            return f"Nominatim error: {e.message}"

        if not data:
            return f"No results for '{query}'."

        addr = data.get("address", {})
        return (
            f"Query:        {query}\n"
            f"Display name: {data.get('display_name', 'N/A')}\n"
            f"OSM type:     {data.get('type', 'N/A')}\n"
            f"Street:       {addr.get('road', 'N/A')} {addr.get('house_number', '')}\n"
            f"City:         {addr.get('postcode', '')} {addr.get('city') or addr.get('town') or addr.get('village', 'N/A')}\n"
            f"State:        {addr.get('state', 'N/A')}\n"
            f"Country:      {addr.get('country', 'N/A')} ({addr.get('country_code', '').upper()})\n"
            f"Coordinates:  {data.get('lat', 'N/A')}, {data.get('lon', 'N/A')}\n"
            f"Bounding box: {data.get('boundingbox', 'N/A')}"
        )

    @mcp.tool(annotations={"readOnlyHint": True})
    async def osint_person_darknet_check(
        query: Annotated[
            str, Field(description="Search term: name, email, username, etc.")
        ],
    ) -> str:
        """Search the darknet for mentions of a query term via Ahmia.fi (public Tor search engine).

        Returns: .onion URLs and page descriptions matching the query.
        CONFIDENCE WARNING: names are not unique — treat all results as [LOW] until a specific
          detail (matching handle, email, or location) ties the mention to the target.
        A darknet mention that includes identifiers already found in the investigation = [MED].
        Do NOT use for: general web presence checks — use osint_web_dork instead.
        No API key required. Ahmia indexes public Tor hidden services only.
        """
        query = query.strip()
        try:
            await rate_limit("default")

            async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
                home = await client.get(
                    "https://ahmia.fi/search/",
                    headers={"User-Agent": "Mozilla/5.0"},
                )
                soup_home = BeautifulSoup(home.text, "html.parser")

                # extract CSRF token from hidden input
                csrf_input = soup_home.find("input", {"type": "hidden"})
                if not csrf_input:
                    return "Ahmia.fi: could not extract CSRF token."
                csrf_name = csrf_input["name"]  # e.g. "4aacd9"
                csrf_value = csrf_input["value"]  # e.g. "162004"

                # search with token appended
                resp = await client.get(
                    "https://ahmia.fi/search/",
                    params={"q": query, csrf_name: csrf_value},
                    headers={"User-Agent": "Mozilla/5.0"},
                )
                html = resp.text

            soup = BeautifulSoup(html, "html.parser")
            results = []
            for li in soup.select("li.result"):
                title_el = li.select_one("h4")
                url_el = li.select_one("cite")
                desc_el = li.select_one("p")
                results.append(
                    {
                        "title": title_el.get_text(strip=True) if title_el else "N/A",
                        "url": url_el.get_text(strip=True) if url_el else "N/A",
                        "desc": desc_el.get_text(strip=True) if desc_el else "",
                    }
                )

            if not results:
                return f"No darknet results for '{query}' on Ahmia.fi."

            lines = [
                f"Ahmia.fi darknet search for '{query}' ({len(results)} results):\n"
            ]
            for r in results[:15]:  # Potentially bump up to 25
                lines.append(
                    f"Title: {r['title']}\n"
                    f"URL:   {r['url']}\n"
                    f"Desc:  {r['desc'][:200]}\n"
                    f"{'─' * 40}"
                )
            return "\n".join(lines)

        except ImportError:
            return "beautifulsoup4 not installed. Run: pip install beautifulsoup4"
        except Exception as e:
            return f"Ahmia.fi error: {e}"
