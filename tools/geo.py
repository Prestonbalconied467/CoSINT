"""
tools/geo.py  –  Geolocation & Physical Locations
Tools: reverse, forward, ip_location
"""

from typing import Annotated

from mcp.server.fastmcp import FastMCP
from pydantic import Field

from shared.http_client import get, OsintRequestError
from shared.rate_limiter import rate_limit


def register(mcp: FastMCP) -> None:
    @mcp.tool(annotations={"readOnlyHint": True})
    async def osint_geo_reverse(
        lat: Annotated[float, Field(description="Latitude, e.g. 51.5074")],
        lon: Annotated[float, Field(description="Longitude, e.g. -0.1278")],
    ) -> str:
        """Convert GPS coordinates to a street address via Nominatim/OpenStreetMap.

        Returns: full address, street, city, postcode, country, and place type.
        Interpret the location type: residential address → possible home location (high sensitivity);
          business district → workplace; transport hub (airport/hotel) → transient, low attribution value;
          isolated/rural → unusual, note nearby infrastructure.
        Use when: EXIF GPS data found, IP geolocation output needs human-readable form,
          or coordinates appear in documents.
        No API key required. Rate limit: 1 req/sec (automatically enforced).
        """
        try:
            await rate_limit("nominatim")
            data = await get(
                "https://nominatim.openstreetmap.org/reverse",
                params={
                    "lat": lat,
                    "lon": lon,
                    "format": "json",
                    "addressdetails": 1,
                    "zoom": 18,
                },
                headers={"User-Agent": "osint-mcp/1.0"},
            )
        except OsintRequestError as e:
            return f"Nominatim error: {e.message}"

        if not data or "error" in data:
            return f"No address found for coordinates {lat}, {lon}."

        addr = data.get("address", {})
        return (
            f"Reverse geocoding: {lat}, {lon}\n\n"
            f"Display name:  {data.get('display_name', 'N/A')}\n"
            f"OSM type:      {data.get('type', 'N/A')} / {data.get('category', 'N/A')}\n"
            f"OSM ID:        {data.get('osm_id', 'N/A')}\n\n"
            f"── Address ──\n"
            f"Street:        {addr.get('road', 'N/A')} {addr.get('house_number', '')}\n"
            f"Neighbourhood: {addr.get('neighbourhood') or addr.get('suburb', 'N/A')}\n"
            f"City:          {addr.get('postcode', '')} {addr.get('city') or addr.get('town') or addr.get('village', 'N/A')}\n"
            f"County:        {addr.get('county', 'N/A')}\n"
            f"State:         {addr.get('state', 'N/A')}\n"
            f"Country:       {addr.get('country', 'N/A')} ({addr.get('country_code', '').upper()})\n\n"
            f"Google Maps:   https://maps.google.com/?q={lat},{lon}\n"
            f"OpenStreetMap: https://www.openstreetmap.org/?mlat={lat}&mlon={lon}&zoom=15"
        )

    @mcp.tool(annotations={"readOnlyHint": True})
    async def osint_geo_forward(
        address: Annotated[
            str,
            Field(
                description="Address or place name, e.g. 'Brandenburger Tor, Berlin'"
            ),
        ],
        limit: Annotated[
            int, Field(description="Maximum number of results (1-5)", ge=1, le=5)
        ] = 3,
    ) -> str:
        """Convert an address or place name to GPS coordinates via Nominatim/OSM.

        Returns: coordinates (lat, lon), bounding box, place type, and display name.
        Use for: validating a claimed address (coordinates landing in a field/industrial estate
          for a claimed residential address = likely fake), checking if an address is a known
          registered-agent office, or proximity analysis against other known locations.
        Do NOT use for IP geolocation — use osint_network_ip_geolocation instead.
        No API key required. Rate limit: 1 req/sec (automatically enforced).
        """
        try:
            await rate_limit("nominatim")
            data = await get(
                "https://nominatim.openstreetmap.org/search",
                params={
                    "q": address,
                    "format": "json",
                    "addressdetails": 1,
                    "limit": limit,
                },
                headers={"User-Agent": "osint-mcp/1.0"},
            )
        except OsintRequestError as e:
            return f"Nominatim error: {e.message}"

        if not isinstance(data, list) or not data:
            return f"No results for '{address}'."

        lines = [f"Geocoding '{address}' ({len(data)} results):\n"]
        for i, result in enumerate(data, 1):
            lat, lon = result.get("lat"), result.get("lon")
            lines.append(
                f"── Result {i} ──\n"
                f"Display name:  {result.get('display_name', 'N/A')}\n"
                f"Coordinates:   {lat}, {lon}\n"
                f"Type:          {result.get('type', 'N/A')}\n"
                f"Bounding box:  {result.get('boundingbox', 'N/A')}\n"
                f"Google Maps:   https://maps.google.com/?q={lat},{lon}\n"
            )
        return "\n".join(lines)
