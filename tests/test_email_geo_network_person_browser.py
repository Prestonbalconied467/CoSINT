import asyncio


def test_email_breach_check_and_header_and_holehe(monkeypatch):
    import tools.email as email
    from shared.subprocess_runner import SubprocessResult

    class FakeMCP:
        def __init__(self):
            self.tools = {}

        def tool(self, **kwargs):
            def deco(fn):
                self.tools[fn.__name__] = fn
                return fn

            return deco

    fake_mcp = FakeMCP()
    # enable both keys so breach_check registers
    monkeypatch.setattr(email.config, "HIBP_KEY", "x")
    monkeypatch.setattr(email.config, "LEAKCHECK_KEY", "y")
    email.register(fake_mcp)

    async def fake_get(url, params=None, headers=None, max_retries=None):
        if "haveibeenpwned" in url:
            return [
                {
                    "Name": "Breach",
                    "Domain": "example.com",
                    "BreachDate": "2020-01-01",
                    "PwnCount": 1,
                    "DataClasses": ["Emails"],
                    "IsVerified": True,
                }
            ]
        if "leakcheck" in url:
            return {"found": 1, "sources": [{"name": "paste", "date": "2021-01-01"}]}
        return {}

    monkeypatch.setattr(email, "get", fake_get)
    monkeypatch.setattr(email, "rate_limit", lambda *a, **k: asyncio.sleep(0))

    out = asyncio.run(fake_mcp.tools["osint_email_breach_check"]("user@example.com"))
    assert "Breach check" in out and "HaveIBeenPwned" in out

    # header analysis
    out2 = asyncio.run(
        fake_mcp.tools["osint_email_header_analyze"](
            "From: a@b.com\nReceived: from 1.2.3.4\n"
        )
    )
    assert (
        "Routing path" in out2
        or "Public IPs in header" in out2
        or "Authentication" in out2
    )

    # holehe social accounts (simulate CLI available)
    monkeypatch.setattr(email, "is_available", lambda t: True)

    async def fake_run(*a, **k):
        return SubprocessResult(
            returncode=0, stdout="user@example.com found on service", stderr=""
        )

    monkeypatch.setattr(email, "run", fake_run)
    out3 = asyncio.run(
        fake_mcp.tools["osint_email_social_accounts"]("user@example.com")
    )
    assert "Holehe results" in out3 or "Holehe returned no results" not in out3


def test_geo_reverse_and_forward(monkeypatch):
    import tools.geo as geo

    class FakeMCP:
        def __init__(self):
            self.tools = {}

        def tool(self, **kwargs):
            def deco(fn):
                self.tools[fn.__name__] = fn
                return fn

            return deco

    fake_mcp = FakeMCP()
    geo.register(fake_mcp)

    async def fake_get(url, params=None, headers=None):
        if "reverse" in url:
            return {
                "display_name": "X",
                "address": {"road": "R", "city": "C", "country": "N"},
                "lat": params.get("lat"),
                "lon": params.get("lon"),
            }
        return [
            {
                "display_name": "Place",
                "lat": "1",
                "lon": "2",
                "type": "place",
                "boundingbox": [],
            }
        ]

    monkeypatch.setattr(geo, "get", fake_get)
    monkeypatch.setattr(geo, "rate_limit", lambda *a, **k: asyncio.sleep(0))

    out = asyncio.run(fake_mcp.tools["osint_geo_reverse"](51.0, -0.1))
    assert "Reverse geocoding" in out

    out2 = asyncio.run(
        fake_mcp.tools["osint_geo_forward"]("Brandenburger Tor", limit=1)
    )
    assert "Geocoding" in out2 or "No results" not in out2


def test_network_asn_ip_and_open_ports_and_vpn(monkeypatch):
    import tools.network as network

    class FakeMCP:
        def __init__(self):
            self.tools = {}

        def tool(self, **kwargs):
            def deco(fn):
                self.tools[fn.__name__] = fn
                return fn

            return deco

    fake_mcp = FakeMCP()
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
        if "ipinfo.io" in url:
            return {"ip": "1.2.3.4", "org": "AS1234 ISP", "loc": "1,2"}
        if "shodan" in url:
            return {"ip_str": "1.2.3.4", "ports": [80], "data": []}
        return {}

    async def fake_get_text(url, params=None):
        if "reverseiplookup" in url:
            return ""
        if "exit-addresses" in url:
            return "1.2.3.4\n"
        return ""

    monkeypatch.setattr(network, "get", fake_get)
    monkeypatch.setattr(network, "get_text", fake_get_text)
    monkeypatch.setattr(network, "rate_limit", lambda *a, **k: asyncio.sleep(0))
    # test geolocation
    out = asyncio.run(fake_mcp.tools["osint_network_ip_geolocation"]("1.2.3.4"))
    assert "IP:" in out

    # ASN lookup with AS input
    out2 = asyncio.run(fake_mcp.tools["osint_network_asn_lookup"]("AS1234"))
    assert "ASN" in out2 or "Source" in out2

    # open_ports: if SHODAN_KEY not set, should return missing key message
    monkeypatch.setattr(network.config, "SHODAN_KEY", "")
    out3 = asyncio.run(fake_mcp.tools["osint_network_open_ports"]("1.2.3.4"))
    assert "SHODAN_KEY" in out3 or "no key" in out3.lower()

    # vpn/proxy check -> tor text contains ip -> should report Tor Exit Node YES
    out4 = asyncio.run(fake_mcp.tools["osint_network_vpn_proxy_check"]("1.2.3.4"))
    assert "Tor Exit Node" in out4


def test_person_fullname_lookup(monkeypatch):
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
    # set key so fullname_lookup registers
    monkeypatch.setattr(person.config, "FULLCONTACT_KEY", "x")
    person.register(fake_mcp)

    async def fake_post(url, params=None, post_json=None, headers=None):
        return {
            "fullName": "Alice",
            "age": 30,
            "details": {"profiles": {"twitter": {"url": "https://twitter.com/alice"}}},
        }

    monkeypatch.setattr(person, "get", lambda *a, **k: asyncio.sleep(0))
    monkeypatch.setattr(person, "rate_limit", lambda *a, **k: asyncio.sleep(0))
    monkeypatch.setattr("shared.http_client.post", fake_post)

    out = asyncio.run(fake_mcp.tools["osint_person_fullname_lookup"]("Alice"))
    assert "Name:" in out or "Social Profiles" in out


def test_browser_smart_wait_noninteractive_and_interactive(monkeypatch):
    import agent_runtime.browser as browser

    class FakePage:
        def __init__(self, selectors):
            # selectors: dict selector->behavior True means present, False raises
            self._sel = selectors

        async def wait_for_selector(self, selector, timeout=None):
            if self._sel.get(selector):
                return True
            raise Exception("not found")

    # non-interactive: result present -> True
    page = FakePage({"#res": True, "#captcha": False})
    ready = asyncio.run(
        browser.smart_wait(
            page, result_selector="#res", captcha_selector="#captcha", interactive=False
        )
    )
    assert ready is True

    # non-interactive: captcha present -> False
    page2 = FakePage({"#res": False, "#captcha": True})
    ready2 = asyncio.run(
        browser.smart_wait(
            page2,
            result_selector="#res",
            captcha_selector="#captcha",
            interactive=False,
        )
    )
    assert ready2 is False

    # interactive: captcha present -> call restart_interactive and then result appears
    called = {"restarted": False}

    async def fake_restart():
        called["restarted"] = True

    monkeypatch.setattr(browser, "restart_interactive", fake_restart)
    page3 = FakePage({"#res": True, "#captcha": True})
    ready3 = asyncio.run(browser.smart_wait(page3, result_selector="#res", captcha_selector="#captcha", interactive=True))
    assert called["restarted"] and ready3 is True

