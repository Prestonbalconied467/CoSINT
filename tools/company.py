"""
tools/company.py  –  Companies & Organizations
Tools: registry_lookup, employees, tech_stack, financials, jobs
"""

from typing import Annotated, Optional

from mcp.server.fastmcp import FastMCP
from pydantic import Field

from shared import config
from shared.http_client import get, OsintRequestError
from shared.rate_limiter import rate_limit
from shared.url_utils import extract_domain


def register(mcp: FastMCP) -> None:
    @mcp.tool(annotations={"readOnlyHint": True})
    async def osint_company_registry_lookup(
        company_name: Annotated[
            str, Field(description="Company name to search for")
        ],
        country: Annotated[
            Optional[str],
            Field(
                description="ISO country code, e.g. 'de', 'us', 'gb' (default: all)"
            ),
        ] = None,
    ) -> str:
        """Search company registry data via OpenCorporates + Northdata (DACH region).

        Returns: legal name, registration number, incorporation date, registered address,
          directors/officers, legal form, filing status.
        Key pivot fields: director names (→ person chain), registered_address (→ geo + other
          companies at same address), incorporation_date (compare against domain/brand history).
        Anomaly signals: registered-agent address (shell indicator), nominee directors appearing
          on many companies, no filing activity despite claimed operation.
        Do NOT use for: finding employees or email addresses — use osint_company_employees instead.
        Optional: OPENCORPORATES_KEY and NORTHDATA_KEY for higher rate limits.
        """
        lines: list[str] = [f"Company search: '{company_name}'\n"]
        try:
            await rate_limit("opencorporates")
            params: dict = {"q": company_name, "per_page": 5}
            if country:
                params["jurisdiction_code"] = country
            if config.OPENCORPORATES_KEY:
                params["api_token"] = config.OPENCORPORATES_KEY
            data = await get(
                "https://api.opencorporates.com/v0.4/companies/search",
                params=params,
            )
            companies = data.get("results", {}).get("companies", [])
            lines.append("── OpenCorporates ──")
            if not companies:
                lines.append("No results found.")
            for cw in companies[:5]:
                c = cw.get("company", {})
                lines.append(
                    f"\nName:          {c.get('name')}\n"
                    f"Jurisdiction:  {c.get('jurisdiction_code', '').upper()}\n"
                    f"Status:        {c.get('current_status', 'N/A')}\n"
                    f"Reg. number:   {c.get('company_number', 'N/A')}\n"
                    f"Incorporated:  {c.get('incorporation_date', 'N/A')}\n"
                    f"Address:       {c.get('registered_address_in_full', 'N/A')}\n"
                    f"URL:           {c.get('opencorporates_url', 'N/A')}"
                )
        except OsintRequestError as e:
            lines.append(f"OpenCorporates error: {e.message}")

        try:
            await rate_limit("default")
            data = await get(
                "https://www.northdata.com/_api/company/v1/search",
                params={"query": company_name, "api_key": config.NORTHDATA_KEY},
            )
            results = data.get("companies", [])
            if results:
                lines.append("\n── Northdata (DACH) ──")
                for c in results[:3]:
                    lines.append(
                        f"\nName:          {c.get('name')}\n"
                        f"City:          {c.get('address', {}).get('city', 'N/A')}\n"
                        f"Status:        {c.get('status', 'N/A')}\n"
                        f"Reg. court:    {c.get('register', {}).get('court', 'N/A')}\n"
                        f"Reg. number:   {c.get('register', {}).get('id', 'N/A')}"
                    )
        except OsintRequestError as e:
            lines.append(f"\nNorthdata error: {e.message}")

        return "\n".join(lines)

    if config.HUNTER_API_KEY:

        @mcp.tool(annotations={"readOnlyHint": True})
        async def osint_company_employees(
            domain: Annotated[
                str, Field(description="Company domain, e.g. 'example.com'")
            ],
        ) -> str:
            """Find employee email addresses and email patterns for a domain via Hunter.io.

            Returns: found email addresses, names, positions, and the detected email format pattern.
            Key pivot fields: email_pattern (derive addresses for executives not directly returned),
              individual emails (→ full email chain), names+roles (→ person chain for C-suite/founders).
            Note: headcount in results vs. company's claimed size — large gap = inflated figures.
            Do NOT use for: company registration data — use osint_company_registry_lookup instead.
            Requires: HUNTER_API_KEY in .env
            """

            domain = extract_domain(domain)
            try:
                await rate_limit("hunter")
                data = await get(
                    "https://api.hunter.io/v2/domain-search",
                    params={"domain": domain, "api_key": config.HUNTER_API_KEY},
                    max_retries=1,
                )
            except OsintRequestError as e:
                return f"Hunter.io error: {e.message}"

            d = data.get("data", {})
            lines = [
                f"Domain:         {domain}",
                f"Organization:   {d.get('organization', 'N/A')}",
                f"Email pattern:  {d.get('pattern', 'N/A')}",
                f"Emails found:   {len(d.get('emails', []))}",
                f"Webmail:        {d.get('webmail', 'N/A')}",
                f"Disposable:     {d.get('disposable', 'N/A')}",
                "",
            ]
            for email_info in d.get("emails", []):  # [:20]
                lines.append(
                    f"{email_info.get('value', 'N/A')} "
                    f"{email_info.get('first_name', '')} {email_info.get('last_name', '')} "
                    f"[{email_info.get('position', 'N/A')}] "
                    f"Confidence: {email_info.get('confidence', '?')}%"
                )
            return "\n".join(lines)

    @mcp.tool(annotations={"readOnlyHint": True})
    async def osint_company_financials(
        company_name: Annotated[str, Field(description="Company name")],
        country: Annotated[
            Optional[str],
            Field(description="'us' for SEC EDGAR, other for general search"),
        ] = "us",
    ) -> str:
        """Retrieve public financial filings via SEC EDGAR (US) or OpenCorporates.

        Returns: available filings, annual reports, revenue/debt data where public, and links.
        Key signals: declining revenue + increasing debt = financial stress; large asset transfers
          to related parties = potential misconduct; no filings despite legal requirement = non-compliant.
        No API key required for SEC EDGAR. OpenCorporates key improves coverage.
        Do NOT use for: employee data or contact info — use osint_company_employees instead.
        """
        lines: list[str] = [f"Financial data for '{company_name}':\n"]

        if not country or country.lower() == "us":
            try:
                await rate_limit("default")
                data = await get(
                    "https://efts.sec.gov/LATEST/search-index",
                    params={"q": f'"{company_name}"', "forms": "10-K,10-Q,8-K"},
                )
                hits = data.get("hits", {}).get("hits", [])
                if hits:
                    lines.append("── SEC EDGAR (USA) ──")
                    for h in hits[:8]:
                        src = h.get("_source", {})
                        lines.append(
                            f"  {src.get('form_type', 'N/A')} "
                            f"{src.get('file_date', 'N/A')}  "
                            f"{src.get('entity_name', 'N/A')}"
                        )
                else:
                    lines.append(
                        f"SEC EDGAR: Search at https://www.sec.gov/cgi-bin/browse-edgar?company={company_name.replace(' ', '+')}&action=getcompany"
                    )
            except OsintRequestError:
                lines.append(
                    f"SEC EDGAR: https://www.sec.gov/cgi-bin/browse-edgar?company={company_name.replace(' ', '+')}&action=getcompany"
                )

        try:
            await rate_limit("opencorporates")
            params: dict = {"q": company_name, "per_page": 3}
            if config.OPENCORPORATES_KEY:
                params["api_token"] = config.OPENCORPORATES_KEY
            data = await get(
                "https://api.opencorporates.com/v0.4/companies/search", params=params
            )
            companies = data.get("results", {}).get("companies", [])
            if companies:
                lines.append("\n── OpenCorporates ──")
                for cw in companies[:3]:
                    c = cw.get("company", {})
                    lines.append(
                        f"  {c.get('name')} – {c.get('opencorporates_url', 'N/A')}"
                    )
        except OsintRequestError:
            pass

        return "\n".join(lines)

    if config.ADZUNA_APP_ID and config.ADZUNA_API_KEY:

        @mcp.tool(annotations={"readOnlyHint": True})
        async def osint_company_jobs(
            company_name: Annotated[str, Field(description="Company name")],
            country: Annotated[
                Optional[str],
                Field(
                    description="Country code, e.g. 'de', 'gb', 'us' (default: 'gb')"
                ),
            ] = "gb",
        ) -> str:
            """Search current job listings for a company via Adzuna API.

            Returns: job titles, descriptions, locations, salary ranges, and posting dates.
            Key intelligence: tech stack from engineering roles, operational office locations
              (may differ from registered address), strategic direction from hiring patterns,
              compliance/security hiring spikes that correlate with regulatory or breach events.
            Anomaly: job postings list offices in cities with no registered company presence.
            Requires: ADZUNA_APP_ID and ADZUNA_API_KEY in .env
            """

            country_code = (country or "gb").lower()
            try:
                await rate_limit("default")
                data = await get(
                    f"https://api.adzu  na.com/v1/api/jobs/{country_code}/search/1",
                    params={
                        "app_id": config.ADZUNA_APP_ID,
                        "app_key": config.ADZUNA_API_KEY,
                        "what": company_name,
                        "results_per_page": 25,
                    },
                )
            except OsintRequestError as e:
                return f"Adzuna error: {e.message}"

            results = data.get("results", [])
            total = data.get("count", 0)
            if not results:
                return f"No current job listings for '{company_name}' in {country_code.upper()}."

            lines = [
                f"Job listings for '{company_name}' in {country_code.upper()} ({total} total):\n"
            ]
            for job in results:
                desc = job.get("description", "")[:300].replace("\n", " ")
                lines.append(
                    f"Title:    {job.get('title', 'N/A')}\n"
                    f"Location: {job.get('location', {}).get('display_name', 'N/A')}\n"
                    f"Date:     {job.get('created', 'N/A')[:10]}\n"
                    f"Desc:     {desc}...\n"
                    f"URL:      {job.get('redirect_url', 'N/A')}\n"
                    f"{'─' * 50}"
                )
            return "\n".join(lines)
