"""
tools/network.py  –  IP Addresses & Networks
Tools: ip_geolocation, asn_lookup, open_ports, reputation, vpn_proxy_check, reverse_dns
"""

from typing import Annotated

from mcp.server.fastmcp import FastMCP
from pydantic import Field

from shared import config
from shared.http_client import get, get_text, OsintRequestError
from shared.rate_limiter import rate_limit


def register(mcp: FastMCP) -> None:
    @mcp.tool(annotations={"readOnlyHint": True})
    async def osint_network_ip_geolocation(
        ip: Annotated[str, Field(description="IPv4 or IPv6 address")],
    ) -> str:
        """Geolocate an IP address: country, region, city, ISP, ASN, coordinates, timezone, map links.

        Returns: full location data plus Google Maps and OpenStreetMap links.
        Interpret ISP type immediately — it shapes the rest of the IP investigation:
          Consumer ISP → likely end user; location is probably accurate
          Datacenter/cloud provider → hosting customer; reverse DNS matters more than location
          ISP name is obscure/offshore LLC → possible bulletproof or front provider
        If both ip-api and ipinfo return data and disagree on city → country [MED], city [LOW].
        If both disagree on country → [UNVERIFIED]; one source is likely stale.
        VPN/proxy confirmed → geolocation is invalidated; note the provider identity instead.
        Primary: ip-api.com (no key, 45 req/min). Fallback: ipinfo.io (IPINFO_TOKEN optional).
        """
        ip = ip.strip()
        try:
            await rate_limit("ip_api")
            data = await get(
                f"http://ip-api.com/json/{ip}",
                params={
                    "fields": "status,message,country,countryCode,region,regionName,city,zip,lat,lon,timezone,isp,org,as,query"
                },
            )
            if data.get("status") == "success":
                lat, lon = data.get("lat"), data.get("lon")
                return (
                    f"IP:            {data.get('query')}\n"
                    f"Country:       {data.get('country')} ({data.get('countryCode')})\n"
                    f"Region:        {data.get('regionName')} ({data.get('region')})\n"
                    f"City:          {data.get('city')} {data.get('zip')}\n"
                    f"Coordinates:   {lat}, {lon}\n"
                    f"Timezone:      {data.get('timezone')}\n"
                    f"ISP:           {data.get('isp')}\n"
                    f"Org:           {data.get('org')}\n"
                    f"ASN:           {data.get('as')}\n\n"
                    f"Google Maps:   https://maps.google.com/?q={lat},{lon}\n"
                    f"OpenStreetMap: https://www.openstreetmap.org/?mlat={lat}&mlon={lon}&zoom=12"
                )
        except OsintRequestError:
            pass

        try:
            await rate_limit("default")
            headers = (
                {"Authorization": f"Bearer {config.IPINFO_TOKEN}"}
                if config.IPINFO_TOKEN
                else {}
            )
            data = await get(f"https://ipinfo.io/{ip}/json", headers=headers)
            loc = data.get("loc", "")
            lat, lon = loc.split(",") if "," in loc else (None, None)
            map_lines = (
                (
                    f"\nGoogle Maps:   https://maps.google.com/?q={lat},{lon}\n"
                    f"OpenStreetMap: https://www.openstreetmap.org/?mlat={lat}&mlon={lon}&zoom=12"
                )
                if lat and lon
                else ""
            )
            return (
                f"IP:            {data.get('ip')}\n"
                f"Hostname:      {data.get('hostname', 'N/A')}\n"
                f"City:          {data.get('city')}\n"
                f"Region:        {data.get('region')}\n"
                f"Country:       {data.get('country')}\n"
                f"Coordinates:   {loc}\n"
                f"Org:           {data.get('org')}\n"
                f"Timezone:      {data.get('timezone')}\n"
                f"Source:        ipinfo.io"
                f"{map_lines}"
            )
        except OsintRequestError as e:
            return f"Error: {e.message}"

    if config.IPINFO_TOKEN:

        @mcp.tool(annotations={"readOnlyHint": True})
        async def osint_network_asn_lookup(
            query: Annotated[
                str, Field(description="ASN (e.g. 'AS15169') or IP address")
            ],
        ) -> str:
            """Look up ASN information for an IP or ASN number via ipinfo.io + HackerTarget.

            Returns: ASN number, network name, organization, country, and announced prefixes.
            Known bulletproof hosting ASNs are an immediate red flag: M247, Frantech/BuyVM,
              Serverius, Ecatel, Novogara, Quasi Networks, QuadraNet (when used for abuse).
            ASN owned by an entity that doesn't match the claimed operator → investigate the gap.
            Very small ASN with few announced prefixes → may be purpose-built infrastructure.
            IP input: ipinfo.io resolves ASN + org metadata (IPINFO_TOKEN improves coverage).
            ASN input: HackerTarget plaintext lookup.
            """
            query = query.strip()
            try:
                await rate_limit("ipinfo")

                if query.upper().startswith("AS"):
                    # ASN → HackerTarget (plaintext)
                    asn_num = query.upper().replace("AS", "")
                    raw = await get_text(
                        f"https://api.hackertarget.com/aslookup/?q=AS{asn_num}"
                    )
                    # response: "AS15169","15169","8.8.8.0/24","GOOGLE, US"
                    parts = [p.strip().strip('"') for p in raw.split(",")]
                    name_country = parts[3] if len(parts) > 3 else "N/A"
                    lines = [
                        f"ASN:     AS{asn_num}",
                        f"Info:    {name_country}",
                        f"Prefix:  {parts[2] if len(parts) > 2 else 'N/A'}",
                        "Source:  HackerTarget",
                    ]

                else:
                    # IP → ipinfo lite
                    data = await get(f"https://ipinfo.io/{query}/json")
                    org = data.get("org", "")  # "AS15169 GOOGLE"
                    asn_num, _, asn_name = org.partition(" ")
                    lines = [
                        f"IP:       {data.get('ip')}",
                        f"ASN:      {asn_num}",
                        f"Org:      {asn_name}",
                        f"Country:  {data.get('country', 'N/A')}",
                        f"Region:   {data.get('region', 'N/A')}",
                        f"City:     {data.get('city', 'N/A')}",
                        f"Hostname: {data.get('hostname', 'N/A')}",
                        f"Timezone: {data.get('timezone', 'N/A')}",
                        "Source:   ipinfo.io (lite)",
                    ]

                return "\n".join(lines)

            except Exception as e:
                return f"ASN lookup failed: {e}"

    @mcp.tool(annotations={"readOnlyHint": True})
    async def osint_network_open_ports(
        ip: Annotated[str, Field(description="IPv4 address")],
    ) -> str:
        """Retrieve open ports, service banners, and CVEs for an IP via Shodan API.

        Returns: open ports, service names, banners, software versions, and known CVEs.
        Only run when justified: suspicious infrastructure confirmed, C2 suspicion from abuse data,
          or reverse DNS revealed an interesting hostname worth fingerprinting.
        Never run in quick mode or on residential IPs without prior abuse/infrastructure signals.
        Port interpretation: 4444/8443/9001/31337 → potential C2 or tunneling;
          80/443/25/22 only → generic web/mail/admin hosting;
          22 open + no web ports → backend server or jump host.
        Requires: SHODAN_KEY in .env
        """
        if not config.SHODAN_KEY:
            return config.missing_key_error_env("SHODAN_KEY")

        ip = ip.strip()
        try:
            await rate_limit("shodan")
            data = await get(
                f"https://api.shodan.io/shodan/host/{ip}",
                params={"key": config.SHODAN_KEY},
                max_retries=1,
            )
        except OsintRequestError as e:
            return f"Shodan error: {e.message}"

        lines = [
            f"IP:           {data.get('ip_str')}",
            f"Hostnames:    {', '.join(data.get('hostnames', []) or ['N/A'])}",
            f"Organization: {data.get('org', 'N/A')}",
            f"ISP:          {data.get('isp', 'N/A')}",
            f"ASN:          {data.get('asn', 'N/A')}",
            f"Country:      {data.get('country_name', 'N/A')} ({data.get('country_code', '')})",
            f"City:         {data.get('city', 'N/A')}",
            f"OS:           {data.get('os', 'N/A')}",
            f"Last Scan:    {data.get('last_update', 'N/A')}",
            f"Open Ports:   {', '.join(str(p) for p in data.get('ports', []))}",
            "",
        ]

        for svc in data.get("data", []):
            port = svc.get("port")
            transport = svc.get("transport", "tcp")
            product = svc.get("product", "")
            version = svc.get("version", "")
            banner = svc.get("data", "").strip()[:500]
            vulns = list(svc.get("vulns", {}).keys())
            lines.append(f"Port {port}/{transport}  {product} {version}")
            lines.append(f"  Banner: {banner}")
            if vulns:
                lines.append(f"  CVEs:   {', '.join(vulns)}")
            lines.append("")

        return "\n".join(lines)

    if config.ABUSEIPDB_KEY or config.VIRUSTOTAL_KEY:

        @mcp.tool(annotations={"readOnlyHint": True})
        async def osint_network_reputation(
            ip: Annotated[str, Field(description="IPv4 address")],
        ) -> str:
            """Check reputation and abuse history of an IP via AbuseIPDB + VirusTotal.

            Returns: abuse_score (0-100), abuse_categories, report_count, last_reported,
              and VirusTotal detection count.
            Interpret recency and source count together:
              Recent reports (<30 days) + multiple independent reporters → [HIGH] active malicious use
              Old reports (>90 days) on residential IP → likely previous occupant, not current user
              C2/phishing categories > spam categories → more significant finding
              High score but zero recent reports → may have been cleaned up or rotated out
            Requires: ABUSEIPDB_KEY in .env (VIRUSTOTAL_KEY optional for VT detections).
            """
            ip = ip.strip()
            lines: list[str] = [f"Reputation for IP {ip}:\n"]

            if config.ABUSEIPDB_KEY:
                try:
                    await rate_limit("abuseipdb")
                    data = await get(
                        "https://api.abuseipdb.com/api/v2/check",
                        headers={
                            "Key": config.ABUSEIPDB_KEY,
                            "Accept": "application/json",
                        },
                        params={"ipAddress": ip, "maxAgeInDays": 90, "verbose": ""},
                        max_retries=1,
                    )
                    d = data.get("data", {})
                    lines += [
                        "── AbuseIPDB ──",
                        f"Abuse Score:    {d.get('abuseConfidenceScore')}%",
                        f"Total Reports:  {d.get('totalReports')}",
                        f"Last Reported:  {d.get('lastReportedAt', 'N/A')}",
                        f"ISP:            {d.get('isp', 'N/A')}",
                        f"Domain:         {d.get('domain', 'N/A')}",
                        f"Country:        {d.get('countryCode', 'N/A')}",
                        f"Usage Type:     {d.get('usageType', 'N/A')}",
                        f"Proxy/Tor:      {d.get('isPublicProxy')} / {d.get('isTor')}",
                    ]
                except OsintRequestError as e:
                    lines.append(f"AbuseIPDB error: {e.message}")
            else:
                lines.append("AbuseIPDB: no key (ABUSEIPDB_KEY)")

            if config.VIRUSTOTAL_KEY:
                try:
                    await rate_limit("virustotal")
                    data = await get(
                        f"https://www.virustotal.com/api/v3/ip_addresses/{ip}",
                        headers={"x-apikey": config.VIRUSTOTAL_KEY},
                    )
                    stats = (
                        data.get("data", {})
                        .get("attributes", {})
                        .get("last_analysis_stats", {})
                    )
                    lines += [
                        "\n── VirusTotal ──",
                        f"Malicious:   {stats.get('malicious', 0)}",
                        f"Suspicious:  {stats.get('suspicious', 0)}",
                        f"Harmless:    {stats.get('harmless', 0)}",
                        f"Undetected:  {stats.get('undetected', 0)}",
                    ]
                except OsintRequestError as e:
                    lines.append(f"\nVirusTotal error: {e.message}")

            return "\n".join(lines)

    @mcp.tool(annotations={"readOnlyHint": True})
    async def osint_network_vpn_proxy_check(
        ip: Annotated[str, Field(description="IPv4 address")],
    ) -> str:
        """Check if an IP is a VPN, proxy, hosting provider, or Tor exit node via IPHub + Tor list.

        Returns: block_type (0=residential, 1=non-residential/hosting, 2=VPN/proxy),
          isp name, country, and Tor exit node status.
        If VPN/Tor confirmed: IP-based geolocation is invalidated entirely. Note the provider
          identity instead (e.g. 'Mullvad exit node' is still useful intelligence).
        block_type=1 (hosting/datacenter) without VPN flag + abuse reports = more likely
          dedicated malicious infrastructure than a consumer user on a VPN.
        Uses IPHub API (free: 1000 req/day). Tor check works without a key.
        Requires: IPHUB_KEY in .env (optional — Tor check works without it).
        """
        ip = ip.strip()
        lines: list[str] = [f"VPN/Proxy/Tor check for {ip}:\n"]

        try:
            await rate_limit("default")
            tor_text = await get_text("https://check.torproject.org/exit-addresses")
            is_tor = ip in tor_text
            lines.append(f"Tor Exit Node: {'YES' if is_tor else 'No'}")
        except OsintRequestError:
            lines.append("Tor check: unavailable")

        if config.IPHUB_KEY:
            try:
                await rate_limit("default")
                data = await get(
                    f"https://v2.api.iphub.info/ip/{ip}",
                    headers={"X-Key": config.IPHUB_KEY},
                )
                block_map = {
                    0: "Residential (not recommended to block)",
                    1: "Non-Residential / VPN / Proxy",
                    2: "Non-Residential but possibly legitimate",
                }
                lines += [
                    "\n── IPHub ──",
                    f"Rating: {block_map.get(data.get('block', -1), 'Unknown')}",
                    f"ISP:    {data.get('isp', 'N/A')}",
                    f"Country:{data.get('countryCode', 'N/A')}",
                ]
            except OsintRequestError as e:
                lines.append(f"\nIPHub error: {e.message}")
        else:
            lines.append("\nIPHub: no key (IPHUB_KEY) – Tor check only")

        return "\n".join(lines)

    @mcp.tool(annotations={"readOnlyHint": True})
    async def osint_network_reverse_dns(
        ip: Annotated[str, Field(description="IPv4 or IPv6 address")],
    ) -> str:
        """Reverse DNS lookup (PTR records) for an IP address via HackerTarget API.

        Returns: PTR hostname(s) associated with the IP.
        PTR records identify purpose and owner faster than any other single signal:
          mail.company.com → email infrastructure for that company
          vpn.company.com → corporate VPN exit (not a threat actor)
          customerXX.hosting.com → shared hosting; pivot to the hosting provider
          No PTR on a datacenter IP → deliberately unconfigured; minor OPSEC signal
        Anomaly: PTR hostname that does not forward-resolve back to the same IP =
          misconfiguration or deliberate misdirection.
        Free, no key required.
        """
        ip = ip.strip()
        try:
            await rate_limit("default")
            text = await get_text(
                "https://api.hackertarget.com/reverseiplookup/", params={"q": ip}
            )
            if not text or "error" in text.lower() or "API count" in text:
                return f"No reverse DNS records found for {ip}."
            return f"Reverse DNS for {ip}:\n{text}"
        except OsintRequestError as e:
            return f"Error: {e.message}"
