"""
shared/http_client.py

Central async HTTP client for all OSINT tool modules.
All API calls in this project go through get() / post() –
never create separate httpx.AsyncClient instances in tool modules.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from copy import deepcopy
from typing import Any
from shared.config import (
    HTTP_CLIENT_GET_CACHE_TTL_SECONDS,
    HTTP_CLIENT_GET_CACHE_MAX_ENTRIES,
    HTTP_CLIENT_MAX_RETRIES,
    HTTP_CLIENT_RETRY_BACKOFF,
    HTTP_CLIENT_DEFAULT_TIMEOUT,
)
import httpx

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = HTTP_CLIENT_DEFAULT_TIMEOUT
MAX_RETRIES = HTTP_CLIENT_MAX_RETRIES
RETRY_BACKOFF = HTTP_CLIENT_RETRY_BACKOFF
GET_CACHE_TTL_SECONDS = HTTP_CLIENT_GET_CACHE_TTL_SECONDS
GET_CACHE_MAX_ENTRIES = HTTP_CLIENT_GET_CACHE_MAX_ENTRIES

DEFAULT_HEADERS = {
    "User-Agent": "osint-mcp/1.0 (local research tool)",
    "Accept": "application/json",
}


class OsintRequestError(Exception):
    """HTTP or connection error with a readable message for the AI."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.message = message
        self.status = status


_client: httpx.AsyncClient | None = None
_get_cache: dict[str, tuple[float, Any]] = {}


def _normalize_headers(headers: dict[str, str] | None) -> dict[str, str]:
    merged = dict(DEFAULT_HEADERS)
    if headers:
        merged.update(headers)
    return {str(k).lower(): str(v) for k, v in merged.items()}


def _build_get_cache_key(
    url: str,
    *,
    headers: dict[str, str] | None,
    params: dict[str, Any] | None,
) -> str:
    key_payload = {
        "url": str(url),
        "headers": _normalize_headers(headers),
        "params": params or {},
    }
    return f"GET:{json.dumps(key_payload, sort_keys=True, separators=(',', ':'), default=str)}"


def _cache_get(key: str) -> Any | None:
    item = _get_cache.get(key)
    if not item:
        return None
    expires_at, payload = item
    if expires_at < time.monotonic():
        _get_cache.pop(key, None)
        return None
    return deepcopy(payload)


def _cache_set(key: str, payload: Any) -> None:
    now = time.monotonic()
    # Lazy prune expired entries first.
    expired_keys = [k for k, (expires_at, _) in _get_cache.items() if expires_at < now]
    for k in expired_keys:
        _get_cache.pop(k, None)

    if len(_get_cache) >= GET_CACHE_MAX_ENTRIES:
        _get_cache.pop(next(iter(_get_cache)), None)

    _get_cache[key] = (now + GET_CACHE_TTL_SECONDS, deepcopy(payload))


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            timeout=httpx.Timeout(HTTP_CLIENT_DEFAULT_TIMEOUT),
            headers=DEFAULT_HEADERS,
            follow_redirects=True,
        )
    return _client


async def close() -> None:
    """Cleanly close the shared client – call on server shutdown."""
    global _client
    if _client and not _client.is_closed:
        await _client.aclose()
        _client = None
    _get_cache.clear()


async def _request_with_retry(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    params: dict[str, Any] | None = None,
    retry_json: dict[str, Any] | None = None,
    data: dict[str, Any] | None = None,
    max_retries: int = MAX_RETRIES,
) -> httpx.Response:
    """Execute an HTTP request with retry and exponential backoff.

    Retried on: connection errors, timeouts, HTTP 429, HTTP 5xx.
    Not retried on: HTTP 4xx (except 429) – client errors won't improve.
    """
    client = _get_client()
    last_error: Exception | None = None

    for attempt in range(max_retries):
        try:
            response = await client.request(
                method,
                url,
                headers=headers,
                params=params,
                json=retry_json,
                data=data,
            )
            if response.status_code == 429:
                last_error = OsintRequestError(
                    f"Rate limit reached for {url}. Please wait a moment.", status=429
                )
                if attempt < max_retries - 1:  # only sleep if retrying
                    wait = float(
                        response.headers.get(
                            "Retry-After", RETRY_BACKOFF ** (attempt + 1)
                        )
                    )
                    logger.warning("Rate limit hit for %s – waiting %.1fs", url, wait)
                    await asyncio.sleep(wait)
                continue
            if response.status_code >= 500:
                wait = RETRY_BACKOFF ** (attempt + 1)
                await asyncio.sleep(wait)
                last_error = OsintRequestError(
                    f"Server error {response.status_code} from {url}.",
                    status=response.status_code,
                )
                continue
            response.raise_for_status()
            return response
        except httpx.TimeoutException:
            await asyncio.sleep(RETRY_BACKOFF ** (attempt + 1))
            last_error = OsintRequestError(f"Timeout requesting {url}.")
        except httpx.ConnectError:
            await asyncio.sleep(RETRY_BACKOFF ** (attempt + 1))
            last_error = OsintRequestError(
                f"Connection to {url} failed. Check network."
            )
        except httpx.HTTPStatusError as e:
            raise _map_http_error(e) from e

    raise last_error or OsintRequestError(
        f"Request to {url} failed after {MAX_RETRIES} attempts."
    )


def _map_http_error(e: httpx.HTTPStatusError) -> OsintRequestError:
    """Translate HTTP status codes into actionable error messages."""
    status = e.response.status_code
    url = str(e.request.url)
    messages = {
        400: f"Bad request to {url} (HTTP 400). Check parameters.",
        401: f"Authentication failed for {url} – check API key in .env.",
        403: f"Access denied for {url} – check API key permissions.",
        404: f"Resource not found: {url}.",
        422: f"Request validation failed for {url}. Check parameters.",
    }
    return OsintRequestError(
        messages.get(status, f"HTTP {status} from {url}."), status=status
    )


def _merge_user_agent(
    headers: dict[str, str] | None,
    user_agent: str | None,
) -> dict[str, str] | None:
    """Return a headers dict with User-Agent overridden, or the original if no override."""
    if user_agent is None:
        return headers
    merged = dict(headers or {})
    merged["user-agent"] = user_agent
    return merged


async def get(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    params: dict[str, Any] | None = None,
    max_retries: int = MAX_RETRIES,
    use_cache: bool = True,
) -> dict[str, Any]:
    """Async HTTP GET – returns parsed JSON response.

    Args:
        url: Full URL including https://
        headers: Optional additional headers (e.g. Authorization)
        params:  Query parameters as dict

    Returns:
        Parsed JSON response as dict

    Raises:
        OsintRequestError: On network, timeout or HTTP errors
    """
    cache_key = _build_get_cache_key(url, headers=headers, params=params)
    if use_cache:
        cached = _cache_get(cache_key)
        if cached is not None:
            return cached

    response = await _request_with_retry(
        "GET", url, headers=headers, params=params, max_retries=max_retries
    )
    payload = _parse_json(response)
    if use_cache:
        _cache_set(cache_key, payload)
    return payload


async def get_text(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    params: dict[str, Any] | None = None,
    user_agent: str | None = None,
) -> str:
    """GET raw text response (for APIs that don't return JSON).

    Args:
        user_agent: Override the default User-Agent for this request.
    """
    response = await _request_with_retry(
        "GET", url, headers=_merge_user_agent(headers, user_agent), params=params
    )
    return response.text


async def get_text_with_url(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    params: dict[str, Any] | None = None,
    user_agent: str | None = None,
) -> tuple[str, str]:
    """GET raw text and return (text, final_url) after redirects.

    Like get_text, but also exposes the resolved URL so callers that need to
    resolve relative links against the final location (e.g. scrapers) can do so.

    Args:
        user_agent: Override the default User-Agent for this request.

    Returns:
        (response_text, final_url) where final_url is the URL after any redirects.
    """
    response = await _request_with_retry(
        "GET", url, headers=_merge_user_agent(headers, user_agent), params=params
    )
    return response.text, str(response.url)


async def get_bytes(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    params: dict[str, Any] | None = None,
) -> bytes:
    """GET raw bytes response (for images or other binary content)."""
    merged_headers = {"Accept": "*/*", **(headers or {})}
    response = await _request_with_retry(
        "GET", url, headers=merged_headers, params=params
    )
    return response.content


async def head(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    params: dict[str, Any] | None = None,
) -> dict[str, str]:
    """HTTP HEAD — returns response headers as a lowercase-key dict.

    Useful when only headers are needed (e.g. tech fingerprinting) without
    downloading the full response body.
    """
    response = await _request_with_retry("HEAD", url, headers=headers, params=params)
    return {k.lower(): v for k, v in response.headers.items()}


async def post(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    params: dict[str, Any] | None = None,
    post_json: dict[str, Any] | None = None,
    data: dict[str, Any] | None = None,
    max_retries: int = MAX_RETRIES,
) -> dict[str, Any]:
    """Async HTTP POST – returns parsed JSON response.

    Args:
        url:     Full URL
        headers: Optional additional headers
        post_json:    Request body as dict (sent as JSON)
        data:    Form data as dict

    Returns:
        Parsed JSON response as dict

    Raises:
        OsintRequestError: On network, timeout or HTTP errors
    """
    response = await _request_with_retry(
        "POST",
        url,
        headers=headers,
        params=params,
        retry_json=post_json,
        data=data,
        max_retries=max_retries,
    )
    return _parse_json(response)


def _parse_json(response: httpx.Response) -> dict[str, Any]:
    """Parse JSON with a clear error if content is not valid JSON."""
    try:
        return response.json()
    except Exception:
        raise OsintRequestError(
            f"API did not return valid JSON "
            f"(Content-Type: {response.headers.get('content-type', 'unknown')})."
        )
