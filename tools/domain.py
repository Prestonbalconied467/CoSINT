"""
tools/domain.py  –  Domain & Web Infrastructure
Tools: whois, dns_records, subdomains, certificates, wayback, ip_history, tech_fingerprint
"""
import asyncio
from typing import Annotated, Optional
import dns.asyncresolver
import dns.exception

from mcp.server.fastmcp import FastMCP
from pydantic import Field

from shared import config
from shared.http_client import get, get_text, OsintRequestError
from shared.rate_limiter import rate_limit
from shared.subprocess_runner import run, is_available
from shared.url_utils import extract_domain


async def _dns_fallback(domain: str, types_to_check: list[str]) -> list[str]:
    resolver = dns.asyncresolver.Resolver()
    resolver.timeout = 5
    resolver.lifetime = 10

    async def resolve_one(rtype: str) -> str | None:
        try:
            answers = await resolver.resolve(domain, rtype)
            return f"── {rtype} ──\n" + "\n".join(str(r) for r in answers)
        except (
            dns.resolver.NoAnswer,
            dns.resolver.NXDOMAIN,
            dns.resolver.NoNameservers,
        ):
            return None
        except dns.exception.DNSException:
            return None

    resolved = await asyncio.gather(*[resolve_one(t) for t in types_to_check])
    return [r for r in resolved if r is not None]


def register(mcp: FastMCP) -> None:
    @mcp.tool(annotations={"readOnlyHint": True})
    async def osint_domain_whois(
        domain: Annotated[str, Field(description="Domain name, e.g. 'example.com'")],
    ) -> str:
        """Retrieve WHOIS data for a domain: registrant, registrar, creation date, expiry, nameservers.

        Returns: registrant name/email/org, registrar name, creation_date, updated_date,
          expiry_date, nameservers, and privacy/redaction status.
        Key pivot fields: registrant_email (→ full email chain), registrant_org (→ company chain),
          creation_date (compare against known events — proximity is meaningful),
          updated_date (recent update may indicate active operational use or cleanup).
        Anomaly signals: creation date within days of a known incident; registrar preferred by
          threat actors; updated_date is today or very recent.
        Falls back to RDAP if no WhoisXMLAPI key is set (less structured output).
        """
        domain = extract_domain(domain)

        if config.WHOISXML_KEY:
            try:
                await rate_limit("default")
                data = await get(
                    "https://www.whoisxmlapi.com/whoisserver/WhoisService",
                    params={
                        "apiKey": config.WHOISXML_KEY,
                        "domainName": domain,
                        "outputFormat": "JSON",
                    },
                )
                r = data.get("WhoisRecord", {})
                # Some TLDs (e.g. .de) only populate registryData, not the top-level record
                rd = r.get("registryData", {})

                def pick(*keys):
                    """Return first non-None value from r, falling back to rd."""
                    for key in keys:
                        val = r.get(key) or rd.get(key)
                        if val:
                            return val
                    return "N/A"

                reg = r.get("registrant", {})
                nameservers = (r.get("nameServers") or rd.get("nameServers") or {}).get(
                    "hostNames", []
                )

                return (
                    f"Domain:       {domain}\n"
                    f"Registrar:    {pick('registrarName')}\n"
                    f"Status:       {pick('status')}\n"
                    f"Created:      {pick('createdDate')}\n"
                    f"Expires:      {pick('expiresDate')}\n"
                    f"Updated:      {pick('updatedDate')}\n"
                    f"Registrant:   {reg.get('name', 'N/A')} / {reg.get('organization', 'N/A')}\n"
                    f"Country:      {reg.get('country', 'N/A')}\n"
                    f"Nameservers:  {', '.join(nameservers)}\n"
                    f"DNSSEC:       {pick('dnsSec')}"
                )
            except OsintRequestError:
                pass

        try:
            await rate_limit("default")
            data = await get(f"https://rdap.org/domain/{domain}")
            events = {e["eventAction"]: e["eventDate"] for e in data.get("events", [])}
            ns = [n["ldhName"] for n in data.get("nameservers", [])]
            status = ", ".join(data.get("status", []))
            return (
                f"Domain:       {domain}\n"
                f"Status:       {status}\n"
                f"Registered:   {events.get('registration', 'N/A')}\n"
                f"Expires:      {events.get('expiration', 'N/A')}\n"
                f"Updated:      {events.get('last changed', 'N/A')}\n"
                f"Nameservers:  {', '.join(ns)}\n"
                f"Source:       RDAP"
            )
        except OsintRequestError as e:
            return f"Error: {e.message}"

    @mcp.tool(annotations={"readOnlyHint": True})
    async def osint_domain_dns_records(
        domain: Annotated[str, Field(description="Domain name")],
        record_types: Annotated[
            Optional[str],
            Field(
                description="Comma-separated record types, e.g. 'A,MX,TXT'. Leave empty for all."
            ),
        ] = None,
    ) -> str:
        """..."""
        domain = extract_domain(domain)
        types_to_check = (
            [t.strip().upper() for t in record_types.split(",")]
            if record_types
            else ["A", "AAAA", "MX", "NS", "TXT", "CNAME", "SOA"]
        )

        results: list[str] = [f"DNS records for {domain}:\n"]
        for rtype in types_to_check:
            try:
                await rate_limit("default")
                text = await get_text(
                    "https://api.hackertarget.com/dnslookup/",
                    params={"q": domain, "type": rtype},
                )
                if text and "error" not in text.lower() and "API count" not in text:
                    results.append(f"── {rtype} ──\n{text}")
            except OsintRequestError:
                pass

        if len(results) == 1:
            fallback_results = await _dns_fallback(domain, types_to_check)
            if not fallback_results:
                return f"No DNS records found for {domain}."
            results.extend(fallback_results)
            results[0] += " [via local DNS fallback]"

        return "\n".join(results)

    @mcp.tool(annotations={"readOnlyHint": True})
    async def osint_domain_subdomains(
        domain: Annotated[str, Field(description="Base domain, e.g. 'example.com'")],
    ) -> str:
        """Enumerate subdomains via crt.sh (Certificate Transparency) + HackerTarget + subfinder CLI.

        Returns: deduplicated subdomain list from all available sources.
        Prioritize for follow-up: admin, api, dev, staging, mail, vpn, git, panel, internal.
        These reveal backend infrastructure, internal tooling, and attack surface.
        Cross-check results with osint_domain_certificates — certs often expose subdomains
        passive enumeration misses, and vice versa.
        Uses subfinder CLI if installed (broader coverage). Free, no key required.
        """
        domain = extract_domain(domain)
        subdomains: set[str] = set()

        try:
            await rate_limit("default")
            data = await get(
                "https://crt.sh/", params={"q": f"%.{domain}", "output": "json"}
            )
            if isinstance(data, list):
                for entry in data:
                    for sub in entry.get("name_value", "").splitlines():
                        sub = sub.strip().lstrip("*.")
                        if sub.endswith(domain):
                            subdomains.add(sub)
        except OsintRequestError:
            pass

        try:
            await rate_limit("default")
            text = await get_text(
                "https://api.hackertarget.com/hostsearch/", params={"q": domain}
            )
            if text and "error" not in text.lower():
                for line in text.splitlines():
                    sub = line.split(",")[0].strip()
                    if sub:
                        subdomains.add(sub)
        except OsintRequestError:
            pass

        if is_available("subfinder"):
            try:
                result = await run("subfinder", "-d", domain, "-silent", timeout=60)
                for line in result.stdout.splitlines():
                    if line.strip():
                        subdomains.add(line.strip())
            except Exception:
                pass

        if not subdomains:
            return f"No subdomains found for {domain}."
        sorted_subs = sorted(subdomains)
        return (
            f"Found subdomains for {domain} ({len(sorted_subs)} entries):\n"
            + "\n".join(sorted_subs)
        )

    @mcp.tool(annotations={"readOnlyHint": True})
    async def osint_domain_certificates(
        domain: Annotated[str, Field(description="Domain name")],
        limit: Annotated[
            int,
            Field(description="Maximum number of certificates (1-100)", ge=1, le=100),
        ] = 50,
    ) -> str:
        """Search Certificate Transparency logs for a domain via crt.sh.

        Returns: certificates with Subject Alternative Names (SANs), issuer, and validity dates.
        Key pivot: SANs frequently list the operator's entire domain portfolio — each domain
          in a SAN is a new domain pivot. Multiple unrelated domains on one cert = likely same operator.
        Anomaly signals: cert issued before the domain's WHOIS creation date (timeline inconsistency);
          cert SAN listing dozens of unrelated domains (hosting reseller or operator portfolio).
        Free, no key required.
        """
        domain = extract_domain(domain)
        try:
            await rate_limit("default")
            data = await get("https://crt.sh/", params={"q": domain, "output": "json"})
        except OsintRequestError as e:
            return f"Error: {e.message}"

        if not isinstance(data, list) or not data:
            return f"No certificates found for {domain} in CT logs."

        seen_ids: set[int] = set()
        unique = []
        for entry in data:
            cid = entry.get("id", 0)
            if cid not in seen_ids:
                seen_ids.add(cid)
                unique.append(entry)

        unique = unique[:limit]
        lines = [
            f"SSL certificates for {domain} ({len(unique)} of {len(data)} shown):\n"
        ]
        for cert in unique:
            lines.append(
                f"ID:         {cert.get('id')}\n"
                f"Issuer:     {cert.get('issuer_name', 'N/A')}\n"
                f"Logged:     {cert.get('entry_timestamp', 'N/A')}\n"
                f"Not Before: {cert.get('not_before', 'N/A')}\n"
                f"Not After:  {cert.get('not_after', 'N/A')}\n"
                f"SANs:       {cert.get('name_value', 'N/A')}\n"
                f"{'─' * 50}"
            )
        return "\n".join(lines)

    @mcp.tool(annotations={"readOnlyHint": True})
    async def osint_domain_wayback(
        url: Annotated[str, Field(description="URL or domain")],
        limit: Annotated[
            int, Field(description="Max snapshots to return (1-50)", ge=1, le=50)
        ] = 20,
    ) -> str:
        """Find historical snapshots of a website via the Wayback Machine CDX API.

        Returns: timestamps, HTTP status codes, MIME types, and snapshot URLs.
        Always run on domains that appear newly registered or have sparse current content —
        prior ownership, removed staff pages, and historical contact info are often preserved.
        Content type change across snapshots (blog → corporate → parked) signals ownership transfer.
        Fetch specific snapshots with osint_scraper_fetch on the returned Wayback URLs
        to read historical contact pages, team bios, and footers.
        Free, no key required.
        """
        url = url.strip()
        if not url.startswith("http"):
            url = f"https://{url}"
        try:
            await rate_limit("default")
            data = await get(
                "http://web.archive.org/cdx/search/cdx",
                params={
                    "url": url,
                    "output": "json",
                    "limit": limit,
                    "fl": "timestamp,statuscode,mimetype,length",
                    "collapse": "timestamp:8",
                },
            )
        except OsintRequestError as e:
            return f"Error: {e.message}"

        if not isinstance(data, list) or len(data) < 2:
            return f"No Wayback snapshots found for {url}."

        header = data[0]
        rows = data[1:]
        lines = [f"Wayback Machine – {url} ({len(rows)} snapshots):\n"]
        for row in rows:
            record = dict(zip(header, row))
            ts = record.get("timestamp", "")
            date = (
                f"{ts[:4]}-{ts[4:6]}-{ts[6:8]} {ts[8:10]}:{ts[10:12]}"
                if len(ts) >= 12
                else ts
            )
            snap_url = f"https://web.archive.org/web/{ts}/{url}"
            lines.append(
                f"{date}  |  HTTP {record.get('statuscode', '?')}  |  {snap_url}"
            )
        return "\n".join(lines)

    if config.SECURITYTRAILS_KEY:

        @mcp.tool(annotations={"readOnlyHint": True})
        async def osint_domain_ip_history(
            domain: Annotated[str, Field(description="Domain name")],
        ) -> str:
            """Retrieve historical IP addresses a domain has pointed to via SecurityTrails API.

            Returns: IP addresses with the time ranges they were active for this domain.
            Key signals: move from a reputable host to a bulletproof/offshore provider = OPSEC escalation;
              multiple IP changes in a short window = active operational infrastructure;
              historical IPs may still host related domains → run osint_network_reverse_dns on each.
            Requires: SECURITYTRAILS_KEY in .env
            """

            domain = extract_domain(domain)
            try:
                await rate_limit("securitytrails")
                data = await get(
                    f"https://api.securitytrails.com/v1/history/{domain}/dns/a",
                    headers={"APIKEY": config.SECURITYTRAILS_KEY},
                )
            except OsintRequestError as e:
                return f"SecurityTrails error: {e.message}"

            records = data.get("records", [])
            if not records:
                return f"No historical IP data found for {domain}."

            lines = [f"IP history for {domain}:\n"]
            for rec in records:
                ips = [v.get("ip", "?") for v in rec.get("values", [])]
                first = rec.get("first_seen", "N/A")
                last = rec.get("last_seen", "N/A")
                lines.append(f"{first} → {last}:  {', '.join(ips)}")
            return "\n".join(lines)

    @mcp.tool(annotations={"readOnlyHint": True})
    async def osint_domain_tech_fingerprint(
        url: Annotated[str, Field(description="Full URL or domain")],
    ) -> str:
        """Fingerprint the technology stack of a website: CMS, frameworks, CDN, analytics, ad networks.

        Returns: detected technologies with versions, and tracking/analytics IDs where found.
        KEY PIVOT: analytics and tracking IDs (Google Analytics UA-/G-, GTM container IDs, Hotjar,
          Intercom, Mixpanel, etc.) are among the most reliable cross-domain operator attribution
          signals available. Extract every ID and search each via osint_web_search to find other
          domains sharing it. A shared GA ID links domains to the same operator more reliably than WHOIS.
        Anomaly: commercial site with no analytics IDs = deliberate tracking avoidance.
        Uses WhatWeb CLI (priority) → BuiltWith → Wappalyzer → HTTP header analysis.
        """
        if not url.startswith("http"):
            url = f"https://{url}"

        results: list[str] = [f"Tech fingerprint for {url}:\n"]
        ran = False

        # ── 1. WhatWeb (priority) ──────────────────────────────────────────────
        if not ran and is_available("whatweb"):
            try:
                result = await run("whatweb", "--no-check-certificate", url, timeout=30)
                results.append(f"── WhatWeb ──\n{result.stdout}")
                ran = True
            except Exception as e:
                results.append(f"WhatWeb failed: {e}")

        # ── 2. BuiltWith ───────────────────────────────────────────────────────
        if not ran:
            try:
                import builtwith as _builtwith

                bw_data = _builtwith.parse(url)
                if bw_data:
                    results.append("── BuiltWith ──")
                    for category, techs in bw_data.items():
                        results.append(f"  {category}: {', '.join(techs)}")
                    ran = True
            except ImportError:
                pass
            except Exception as e:
                results.append(f"BuiltWith failed: {e}")

        # ── 3. Wappalyzer API ──────────────────────────────────────────────────
        if not ran and config.WAPPALYZER_KEY:
            try:
                await rate_limit("default")
                data = await get(
                    "https://api.wappalyzer.com/v2/lookup/",
                    params={"urls": url},
                    headers={"x-api-key": config.WAPPALYZER_KEY},
                )
                if isinstance(data, list) and data:
                    techs = data[0].get("technologies", [])
                    if techs:
                        results.append("── Wappalyzer ──")
                        for t in techs:
                            cats = ", ".join(
                                c.get("name", "") for c in t.get("categories", [])
                            )
                            results.append(
                                f"  {t.get('name', '?')}  [{cats}]  v{t.get('version', '?')}"
                            )
                        ran = True
            except OsintRequestError as e:
                results.append(f"Wappalyzer failed: {e}")

        # ── 4. HTTP Header Analysis (fallback) ─────────────────────────────────
        if not ran:
            try:
                from shared.http_client import head as http_head

                hdrs = await http_head(url)
                results.append("── HTTP Headers (fallback) ──")
                for h in [
                    "server",
                    "x-powered-by",
                    "x-generator",
                    "x-drupal-cache",
                    "x-wordpress-cache",
                    "via",
                    "cf-ray",
                    "x-amz-cf-id",
                ]:
                    if h in hdrs:
                        results.append(f"  {h}: {hdrs[h]}")
                ran = True
            except Exception as e:
                results.append(f"HTTP analysis failed: {e}")

        return "\n".join(results)
