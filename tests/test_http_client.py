import asyncio
import types

import httpx
import pytest


def make_resp(
    status=200, text="ok", json_obj=None, headers=None, url="https://example.com"
):
    class R:
        def __init__(self):
            self.status_code = status
            self._text = text
            self._json = json_obj
            self.headers = headers or {}
            self.url = url

        def raise_for_status(self):
            if self.status_code >= 400:
                raise httpx.HTTPStatusError(
                    "err", request=types.SimpleNamespace(url=self.url), response=self
                )

        @property
        def text(self):
            return self._text

        @property
        def content(self):
            return self._text.encode()

        def json(self):
            if self._json is None:
                raise ValueError("no json")
            return self._json

    return R()


def test_normalize_headers_and_cache_set_get(monkeypatch):
    import shared.http_client as http

    h = http._normalize_headers({"X-Test": "v"})
    assert "user-agent" in h and "x-test" in h

    key = http._build_get_cache_key("https://a", headers={"A": "b"}, params={"q": 1})
    http._cache_set(key, {"ok": True})
    got = http._cache_get(key)
    assert got == {"ok": True}


def test_parse_json_raises_on_invalid(monkeypatch):
    import shared.http_client as http

    r = make_resp(
        status=200,
        text="not json",
        json_obj=None,
        headers={"content-type": "text/plain"},
    )
    with pytest.raises(http.OsintRequestError):
        http._parse_json(r)


def test_request_with_retry_raises_last_error_after_retries(monkeypatch):
    import shared.http_client as http

    # monkeypatch _get_client to a fake client whose request always raises TimeoutException
    class FakeClient:
        is_closed = False

        async def request(self, *a, **k):
            raise httpx.TimeoutException("t")

    monkeypatch.setattr(http, "_get_client", lambda: FakeClient())

    # speed up sleeps - replace with no-op async
    async def _noop_sleep(t):
        return None

    monkeypatch.setattr(asyncio, "sleep", _noop_sleep)

    with pytest.raises(http.OsintRequestError):
        asyncio.run(
            http._request_with_retry("GET", "https://example.com", max_retries=2)
        )


def test_get_and_get_text_with_cache_and_final_url(monkeypatch):
    import shared.http_client as http

    # monkeypatch _request_with_retry to return a fake response
    resp = make_resp(
        status=200, text="hello", json_obj={"a": 1}, url="https://example.com/final"
    )

    async def fake_req(method, url, **kwargs):
        return resp

    monkeypatch.setattr(http, "_request_with_retry", fake_req)
    # ensure cache is empty
    http._get_cache.clear()

    out = asyncio.run(http.get("https://api", use_cache=True))
    assert out == {"a": 1}
    # second call should hit cache (no exception)
    out2 = asyncio.run(http.get("https://api", use_cache=True))
    assert out2 == {"a": 1}

    txt = asyncio.run(http.get_text_with_url("https://page"))
    assert txt[0] == "hello"
    assert txt[1] == "https://example.com/final"

