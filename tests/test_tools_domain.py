import asyncio


def test_osint_domain_whois_rdap_fallback(monkeypatch):
    import tools.domain as domain

    # Fake MCP to capture registered tools
    class FakeMCP:
        def __init__(self):
            self.tools = {}

        def tool(self, **kwargs):
            def deco(fn):
                self.tools[fn.__name__] = fn
                return fn

            return deco

    fake_mcp = FakeMCP()
    domain.register(fake_mcp)

    # ensure no WHOISXML key
    monkeypatch.setattr(domain.config, "WHOISXML_KEY", "")

    async def fake_get(url, params=None, headers=None):
        if url.startswith("https://rdap.org/"):
            return {
                "events": [{"eventAction": "registration", "eventDate": "2020-01-01"}],
                "nameservers": [{"ldhName": "ns1.example.com"}],
                "status": ["active"],
            }
        raise domain.OsintRequestError("fail")

    monkeypatch.setattr(domain, "get", fake_get)
    monkeypatch.setattr(domain, "rate_limit", lambda *a, **k: asyncio.sleep(0))

    out = asyncio.run(fake_mcp.tools["osint_domain_whois"]("example.com"))
    assert "Registered:" in out or "Nameservers" in out


def test_osint_domain_dns_records_no_results(monkeypatch):
    import tools.domain as domain

    class FakeMCP:
        def __init__(self):
            self.tools = {}

        def tool(self, **kwargs):
            def deco(fn):
                self.tools[fn.__name__] = fn
                return fn

            return deco

    fake_mcp = FakeMCP()
    domain.register(fake_mcp)

    async def fake_get_text(url, params=None):
        return "error: no data"

    monkeypatch.setattr(domain, "get_text", fake_get_text)
    monkeypatch.setattr(domain, "rate_limit", lambda *a, **k: asyncio.sleep(0))

    out = asyncio.run(fake_mcp.tools["osint_domain_dns_records"]("jniawgv.com"))
    assert "No DNS records found" in out


def test_osint_domain_subdomains_combines_sources(monkeypatch):
    import tools.domain as domain

    class FakeMCP:
        def __init__(self):
            self.tools = {}

        def tool(self, **kwargs):
            def deco(fn):
                self.tools[fn.__name__] = fn
                return fn

            return deco

    fake_mcp = FakeMCP()
    domain.register(fake_mcp)

    async def fake_get(url, params=None):
        # crt.sh returns list of dicts
        return [{"name_value": "a.example.com\nb.example.com"}]

    async def fake_get_text(url, params=None):
        return "c.example.com,1.2.3.4\nd.example.com,5.6.7.8"

    monkeypatch.setattr(domain, "get", fake_get)
    monkeypatch.setattr(domain, "get_text", fake_get_text)
    monkeypatch.setattr(domain, "rate_limit", lambda *a, **k: asyncio.sleep(0))
    monkeypatch.setattr(domain, "is_available", lambda t: True)

    class SR:
        stdout = "e.example.com\n"

    async def fake_run(*a, **k):
        return SR()

    monkeypatch.setattr(domain, "run", fake_run)

    out = asyncio.run(fake_mcp.tools["osint_domain_subdomains"]("example.com"))
    assert "Found subdomains" in out and "a.example.com" in out


def test_osint_domain_tech_fingerprint_http_headers_fallback(monkeypatch):
    import tools.domain as domain
    import shared.http_client as http_client

    class FakeMCP:
        def __init__(self):
            self.tools = {}

        def tool(self, **kwargs):
            def deco(fn):
                self.tools[fn.__name__] = fn
                return fn

            return deco

    fake_mcp = FakeMCP()
    domain.register(fake_mcp)

    async def fake_head(url, headers=None, params=None):
        return {"server": "nginx", "x-powered-by": "Django"}

    monkeypatch.setattr(domain, "is_available", lambda t: False)
    monkeypatch.setattr(domain, "rate_limit", lambda *a, **k: asyncio.sleep(0))
    monkeypatch.setattr(http_client, "head", fake_head)

    out = asyncio.run(fake_mcp.tools["osint_domain_tech_fingerprint"]("example.com"))
    assert "HTTP Headers (fallback)" in out or "Tech fingerprint" in out

