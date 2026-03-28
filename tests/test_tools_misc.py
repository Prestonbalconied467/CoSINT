import asyncio


def test_search_calls_google_and_formats(monkeypatch):
    import tools.search as search

    class FakeMCP:
        def __init__(self):
            self.tools = {}

        def tool(self, **kwargs):
            def deco(fn):
                self.tools[fn.__name__] = fn
                return fn

            return deco

    fake_mcp = FakeMCP()
    search.register(fake_mcp)

    monkeypatch.setattr(search.browser, "session_ok", lambda: True)

    out = asyncio.run(fake_mcp.tools["osint_web_search"]("term", interactive=False))
    assert out == "FORMATTED"


def test_email_validate_returns_fields_and_mx_fallback(monkeypatch):
    import tools.email as email

    class FakeMCP:
        def __init__(self):
            self.tools = {}

        def tool(self, **kwargs):
            def deco(fn):
                self.tools[fn.__name__] = fn
                return fn

            return deco

    fake_mcp = FakeMCP()
    email.register(fake_mcp)

    # happy path for validate: get returns dict
    async def fake_get(url, max_retries=None, params=None, headers=None):
        return {
            "disposable": False,
            "domain": "example.com",
            "mx": "mx.example.com",
            "alias": False,
            "spam": False,
            "domain_age_in_days": 100,
        }

    monkeypatch.setattr(email, "get", fake_get)
    monkeypatch.setattr(email, "rate_limit", lambda *a, **k: asyncio.sleep(0))

    out = asyncio.run(fake_mcp.tools["osint_email_validate"]("user@example.com"))
    assert "Email validation" in out or "Disposable" in out

    # simulate OsintRequestError with status 429 -> triggers _mx_fallback
    class FakeErr(Exception):
        pass

    async def fake_get_raises(*a, **k):
        from shared.http_client import OsintRequestError

        raise OsintRequestError("rate limit", status=429)

    async def fake_mx(email_addr):
        return ["\n── MX Fallback (DNS) ──", "MX: mx.example.com"]

    monkeypatch.setattr(email, "get", fake_get_raises)
    monkeypatch.setattr(email, "_mx_fallback", fake_mx)

    out2 = asyncio.run(fake_mcp.tools["osint_email_validate"]("user@example.com"))
    assert "MX Fallback" in out2


def test_person_address_lookup_and_darknet(monkeypatch):
    import tools.person as person

    class FakeMCP:
        def __init__(self):
            self.tools = {}

        def tool(self, **kwargs):
            def deco(fn):
                self.tools[fn.__name__] = fn
                return fn

            return deco

    fake_mcp = FakeMCP()
    person.register(fake_mcp)

    # test address lookup (search path)
    async def fake_get(url, params=None, headers=None):
        return [
            {
                "display_name": "X",
                "address": {"city": "C"},
                "lat": "1",
                "lon": "2",
                "type": "place",
            }
        ]

    monkeypatch.setattr(person, "get", fake_get)
    monkeypatch.setattr(person, "rate_limit", lambda *a, **k: asyncio.sleep(0))

    out = asyncio.run(fake_mcp.tools["osint_person_address_lookup"]("Somewhere"))
    assert "Display name" in out or "Query:" in out

    # darknet check: mock httpx.AsyncClient context to return no csrf input
    class FakeResp:
        def __init__(self, text):
            self.text = text

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, headers=None, params=None):
            return FakeResp("<html><body>No token</body></html>")

    monkeypatch.setattr(person, "rate_limit", lambda *a, **k: asyncio.sleep(0))
    monkeypatch.setattr("httpx.AsyncClient", FakeClient)

    out2 = asyncio.run(fake_mcp.tools["osint_person_darknet_check"]("query"))
    assert "could not extract CSRF token" in out2

