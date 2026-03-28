"""
tools/public.py  –  Public Sources & Open Data
Tools: news_search, court_records, company_register, academic_search, bundestag_search
"""

from typing import Annotated, Optional

from mcp.server.fastmcp import FastMCP
from pydantic import Field

from shared import config
from shared.http_client import get, OsintRequestError
from shared.rate_limiter import rate_limit


def register(mcp: FastMCP) -> None:
    @mcp.tool(annotations={"readOnlyHint": True})
    async def osint_public_news_search(
        query: Annotated[
            str, Field(description="Search term (person, company, event)")
        ],
        from_date: Annotated[
            Optional[str],
            Field(description="Start date in ISO format, e.g. '2024-01-01'"),
        ] = None,
        language: Annotated[
            Optional[str],
            Field(description="Language code: 'en', 'de', etc. (default: 'en')"),
        ] = "en",
    ) -> str:
        """Search news articles via NewsAPI + GDELT.

        Returns: articles with title, source, published_date, URL, and snippet.
        Use for: press coverage of lawsuits, scandals, leadership changes, funding rounds,
          and professional activity. Absence of press on a company claiming significant
          revenue is itself a signal worth noting.
        GDELT covers a broader international range; NewsAPI has higher quality English coverage.
        Do NOT use for: social media mentions — use osint_web_dork(dork_type=forum_mentions) instead.
        Requires: NEWSAPI_KEY in .env for NewsAPI (GDELT is free, no key needed).
        """
        lines: list[str] = [f"News search for '{query}':\n"]

        if config.NEWSAPI_KEY:
            try:
                await rate_limit("newsapi")
                params: dict = {
                    "q": query,
                    "language": language or "en",
                    "sortBy": "relevancy",
                    "pageSize": 10,
                    "apiKey": config.NEWSAPI_KEY,
                }
                if from_date:
                    params["from"] = from_date
                data = await get("https://newsapi.org/v2/everything", params=params)
                articles = data.get("articles", [])
                total = data.get("totalResults", 0)
                lines.append(f"── NewsAPI: {total} results ──")
                for art in articles:
                    lines.append(
                        f"\nTitle:   {art.get('title', 'N/A')}\n"
                        f"Source:  {art.get('source', {}).get('name', 'N/A')}\n"
                        f"Date:    {art.get('publishedAt', 'N/A')[:10]}\n"
                        f"URL:     {art.get('url', 'N/A')}\n"
                        f"Snippet: {(art.get('description') or '')[:200]}"
                    )
            except OsintRequestError as e:
                lines.append(f"NewsAPI error: {e.message}")
        else:
            lines.append("NewsAPI: no key (NEWSAPI_KEY)")

        try:
            await rate_limit("default")
            data = await get(
                "https://api.gdeltproject.org/api/v2/doc/doc",
                params={
                    "query": query,
                    "mode": "artlist",
                    "maxrecords": 10,
                    "format": "json",
                    "sort": "datedesc",
                },
            )
            articles = data.get("articles", [])
            if articles:
                lines.append(f"\n── GDELT: {len(articles)} articles ──")
                for art in articles:
                    lines.append(
                        f"\nTitle:  {art.get('title', 'N/A')}\n"
                        f"Domain: {art.get('domain', 'N/A')}\n"
                        f"Date:   {art.get('seendate', 'N/A')[:8]}\n"
                        f"URL:    {art.get('url', 'N/A')}"
                    )
        except OsintRequestError:
            pass

        return "\n".join(lines)

    if config.COURT_LISTENER_KEY:

        @mcp.tool(annotations={"readOnlyHint": True})
        async def osint_public_court_records(
            query: Annotated[
                str, Field(description="Name, case number or search term")
            ],
            limit: Annotated[
                int, Field(description="Maximum results (1-20)", ge=1, le=20)
            ] = 10,
        ) -> str:
            """Search public US court records via CourtListener API v4.

            Returns: case names, court, filing date, docket number, case type, and parties.
            T1 source — among the most reliable available for US subjects.
            Key intelligence: case type reveals fraud/debt/criminal history; opposing party names
              may reveal associates, employers, or family members; case dates establish timeline anchors.
            US ONLY — does not cover non-US jurisdictions. For German context use
              osint_public_bundestag_search or general web dorks for court records.
            Requires: COURTLISTENER_API_KEY in .env (free account registration).
            """

            query = query.strip()
            headers = {"Authorization": f"Token {config.COURT_LISTENER_KEY}"}

            try:
                await rate_limit("default")
                data = await get(
                    "https://www.courtlistener.com/api/rest/v4/search/",
                    params={
                        "q": query,
                        "type": "d",  # d = dockets, o = opinions
                        "order_by": "score desc",
                        "page_size": limit,
                    },
                    headers=headers,
                )
            except OsintRequestError as e:
                return f"CourtListener error: {e.message}"

            results = data.get("results", [])
            if not results:
                return f"No court records found for '{query}'."

            lines = [f"Court records for '{query}' ({data.get('count', 0)} total):\n"]
            for case in results:
                lines.append(
                    f"Case:    {case.get('caseName') or case.get('case_name', 'N/A')}\n"
                    f"Court:   {case.get('court', 'N/A')}\n"
                    f"Filed:   {case.get('dateFiled') or case.get('date_filed', 'N/A')}\n"
                    f"Docket:  {case.get('docketNumber') or case.get('docket_number', 'N/A')}\n"
                    f"URL:     https://www.courtlistener.com{case.get('absolute_url', '')}\n"
                    f"{'─' * 40}"
                )
            return "\n".join(lines)

    @mcp.tool(annotations={"readOnlyHint": True})
    async def osint_public_academic_search(
        query: Annotated[str, Field(description="Search term or topic")],
        author: Annotated[
            Optional[str], Field(description="Author name to narrow results (optional)")
        ] = None,
        limit: Annotated[
            int, Field(description="Maximum results (1-20)", ge=1, le=20)
        ] = 10,
    ) -> str:
        """Search academic publications via OpenAlex API.

        Returns: paper titles, DOIs, abstracts, citation counts, authors, and publisher.
        Use for: targets with a research or academic background. Author email addresses in
          academic papers are high-confidence identity anchors (institutional emails are T2).
        Do NOT use for: non-academic targets — results will be empty or irrelevant.
        Free, no API key required.
        """
        params: dict = {
            "search": query,
            "per_page": limit,
            "select": "id,title,doi,publication_year,cited_by_count,authorships,primary_location,abstract_inverted_index",
        }
        if author:
            params["filter"] = f"raw_author_name.search:{author}"

        try:
            await rate_limit("default")
            data = await get("https://api.openalex.org/works", params=params)
        except OsintRequestError as e:
            return f"OpenAlex error: {e.message}"

        results = data.get("results", [])
        total = data.get("meta", {}).get("count", 0)
        if not results:
            return f"No publications found for '{query}'."

        lines = [f"Academic publications for '{query}' ({total} total):\n"]
        for paper in results:
            authors = [
                a.get("author", {}).get("display_name", "?")
                for a in paper.get("authorships", [])
            ]
            venue = paper.get("primary_location", {}).get("source", {})
            doi = paper.get("doi") or "N/A"
            lines.append(
                f"Title:       {paper.get('title', 'N/A')}\n"
                f"Authors:     {', '.join(authors)}\n"
                f"Year:        {paper.get('publication_year', 'N/A')}\n"
                f"Citations:   {paper.get('cited_by_count', 0)}\n"
                f"Publisher:   {venue.get('display_name', 'N/A')}\n"
                f"DOI:         {doi}\n"
                f"{'─' * 40}"
            )
        return "\n".join(lines)

    if config.BUNDESTAG_DIP_KEY:

        @mcp.tool(annotations={"readOnlyHint": True})
        async def osint_public_bundestag_search(
            query: Annotated[
                str, Field(description="Search term for Bundestag documents")
            ],
            limit: Annotated[
                int, Field(description="Maximum results (1-20)", ge=1, le=20)
            ] = 10,
        ) -> str:
            """Search German Bundestag documents and plenary minutes via DIP API.

            Returns: printed papers (Drucksachen), parliamentary questions, speeches, and minutes.
            Use for: German political figures, lobbying activity, regulatory actions, and
              companies with public policy exposure in Germany.
            Do NOT use for: non-German political context or general company research.
            Requires: BUNDESTAG_DIP_KEY in .env (free registration at dip.bundestag.de).
            """

            try:
                await rate_limit("default")
                data = await get(
                    "https://search.dip.bundestag.de/api/v1/drucksache",
                    headers={"Authorization": f"ApiKey {config.BUNDESTAG_DIP_KEY}"},
                    params={
                        "f.wahlperiode": 20,
                        "searchTerm": query,
                        "rows": limit,
                        "sort": "datum_desc",
                    },
                )
            except OsintRequestError as e:
                return f"Bundestag DIP error: {e.message}"

            documents = data.get("documents", [])
            total = data.get("numFound", 0)
            if not documents:
                return f"No Bundestag documents found for '{query}'."
            documents = data.get("documents", [])[:limit]
            lines = [f"Bundestag documents for '{query}' ({total} total):\n"]
            for doc in documents:
                lines.append(
                    f"Title:    {doc.get('titel', 'N/A')}\n"
                    f"Type:     {doc.get('drucksachetyp', 'N/A')}\n"
                    f"Date:     {doc.get('datum', 'N/A')}\n"
                    f"Number:   {doc.get('drucksache_nummer', 'N/A')}\n"
                    f"Authors:  {', '.join((a.get('titel', str(a)) if isinstance(a, dict) else a) for a in (doc.get('autoren_anzeige') or [])) or 'N/A'}\n"
                    f"URL:      https://dip.bundestag.de/drucksache/{doc.get('id', '')}\n"
                    f"{'─' * 40}"
                )
            return "\n".join(lines)
