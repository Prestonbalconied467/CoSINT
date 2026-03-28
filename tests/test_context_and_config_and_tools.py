import asyncio


def test_estimate_tokens_fallback_and_compress_messages():
    import agent_runtime.context_utils as cu

    msgs = [{"role": "system", "content": "s"}]
    # small messages -> no compression
    compressed, changed = cu.compress_messages(msgs, keep_last=24)
    assert compressed == msgs and changed is False

    # create many messages to trigger compression
    messages = [{"role": "system", "content": "s"}]
    for i in range(40):
        messages.append(
            {"role": "user" if i % 3 == 0 else "assistant", "content": "x" * 50}
        )
    compressed, changed = cu.compress_messages(messages, keep_last=10)
    assert changed is True
    assert len(compressed) <= 12  # system + summary + tail

    # estimate_tokens fallback
    est, used = cu.estimate_tokens([{"content": "hello world"}], model=None)
    assert est >= 1 and used is True


def test_config_helpers_and_missing_key_message(monkeypatch):
    import shared.config as cfg

    # missing_key_error_env should mention the key
    msg = cfg.missing_key_error_env("FOO_KEY")
    assert "FOO_KEY" in msg

    # _get_env returns env variable when set
    monkeypatch.setenv("MY_TEST_ENV", "value123")
    assert cfg._get_env("MY_TEST_ENV") == "value123"


def test_company_registry_and_financials_basic(monkeypatch):
    import tools.company as company

    class FakeMCP:
        def __init__(self):
            self.tools = {}

        def tool(self, **kwargs):
            def deco(fn):
                self.tools[fn.__name__] = fn
                return fn

            return deco

    from mcp.server.fastmcp import FastMCP

    # ensure company register block executes by faking env keys
    monkeypatch.setattr(
        company.config,
        "_get_env",
        lambda k: "x" if k in ("OPENCORPORATES_KEY", "NORTHDATA_KEY") else "",
    )
    fake_mcp: FastMCP = FakeMCP()
    company.register(fake_mcp)

    async def fake_get(url, params=None):
        if "opencorporates" in url:
            return {
                "results": {
                    "companies": [{"company": {"name": "X", "opencorporates_url": "u"}}]
                }
            }
        return {"hits": {"hits": []}}

    monkeypatch.setattr(company, "get", fake_get)
    monkeypatch.setattr(company, "rate_limit", lambda *a, **k: asyncio.sleep(0))

    out = asyncio.run(fake_mcp.tools["osint_company_registry_lookup"]("MyCo"))
    assert "OpenCorporates" in out

    out2 = asyncio.run(fake_mcp.tools["osint_company_financials"]("MyCo"))
    assert "Financial data" in out2


def test_network_ip_geolocation_and_reverse_dns(monkeypatch):
    import tools.network as network

    class FakeMCP:
        def __init__(self):
            self.tools = {}

        def tool(self, **kwargs):
            def deco(fn):
                self.tools[fn.__name__] = fn
                return fn

            return deco

    from mcp.server.fastmcp import FastMCP

    fake_mcp: FastMCP = FakeMCP()
    network.register(fake_mcp)

    async def fake_get(url, params=None, headers=None, max_retries=None):
        if "ip-api.com" in url:
            return {
                "status": "success",
                "query": "1.2.3.4",
                "lat": 1,
                "lon": 2,
                "country": "X",
            }
        return {"ip": "1.2.3.4", "loc": "1,2"}

    async def fake_get_text(url, params=None):
        return ""

    monkeypatch.setattr(network, "get", fake_get)
    monkeypatch.setattr(network, "get_text", fake_get_text)
    monkeypatch.setattr(network, "rate_limit", lambda *a, **k: asyncio.sleep(0))

    out = asyncio.run(fake_mcp.tools["osint_network_ip_geolocation"]("1.2.3.4"))
    assert "IP:" in out and "Google Maps" in out or "OpenStreetMap" in out

    # reverse DNS no records
    async def fake_reverse_text(url, params=None):
        return "error"

    monkeypatch.setattr(network, "get_text", fake_reverse_text)
    out2 = asyncio.run(fake_mcp.tools["osint_network_reverse_dns"]("1.2.3.4"))
    assert "No reverse DNS records" in out2 or "Error" in out2


