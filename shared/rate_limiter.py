"""
shared/rate_limiter.py

Token-bucket rate limiter per API.
Prevents bans when making parallel or rapid successive requests.

Usage:
    from shared.rate_limiter import rate_limit

    async def my_tool():
        await rate_limit("nominatim")   # waits if necessary
        result = await get(url)
"""

import asyncio
import time
from dataclasses import dataclass, field


@dataclass
class _Bucket:
    """Token bucket for a single API."""

    rate: float  # Allowed requests per second
    capacity: float  # Max stored tokens (burst)
    tokens: float = field(init=False)
    last: float = field(init=False)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False)

    def __post_init__(self) -> None:
        self.tokens = self.capacity
        self.last = time.monotonic()

    async def acquire(self) -> None:
        async with self.lock:
            now = time.monotonic()
            delta = now - self.last
            self.last = now
            self.tokens = min(self.capacity, self.tokens + delta * self.rate)

            if self.tokens >= 1.0:
                self.tokens -= 1.0
            else:
                wait = (1.0 - self.tokens) / self.rate
                await asyncio.sleep(wait)
                self.tokens = 0.0


# ── Configured limits ─────────────────────────────────────────────────────────
# Format: "api_name": _Bucket(rate=req/sec, capacity=burst)
# TODO Should add all apis here
_BUCKETS: dict[str, _Bucket] = {
    "ip_api": _Bucket(rate=0.75, capacity=5),  # 45/min
    "nominatim": _Bucket(rate=1.0, capacity=1),  # 1/sec  (OSM requirement!)
    "shodan": _Bucket(rate=1.0, capacity=1),  # 1/sec
    "virustotal": _Bucket(rate=0.067, capacity=2),  # 4/min
    "hunter": _Bucket(rate=0.1, capacity=2),  # 6/min
    "hibp": _Bucket(rate=0.167, capacity=3),  # 10/min
    "abuseipdb": _Bucket(rate=0.5, capacity=5),  # 30/min
    "securitytrails": _Bucket(rate=0.5, capacity=3),
    "emailrep": _Bucket(rate=0.5, capacity=3),
    "leakcheck": _Bucket(rate=0.167, capacity=2),  # 10/min free
    "intelx": _Bucket(rate=0.033, capacity=1),  # 2/min free
    "github": _Bucket(rate=1.0, capacity=10),  # 5000/hr with token
    "bgpview": _Bucket(rate=1.0, capacity=5),
    "blockchair": _Bucket(rate=0.5, capacity=3),
    "etherscan": _Bucket(rate=0.2, capacity=2),  # 5/sec free
    "newsapi": _Bucket(rate=0.5, capacity=5),
    "opencorporates": _Bucket(rate=0.5, capacity=3),
    # Search engines — be conservative to avoid bot detection
    "google_search": _Bucket(rate=0.1, capacity=1),  # 1 per 10s
    "bing_search": _Bucket(rate=0.2, capacity=2),  # 1 per 5s
    "ddg_search": _Bucket(rate=0.17, capacity=2),  # 1 per 6s
    "usercheck": _Bucket(rate=1.0, capacity=1),  # 1/sec; 1000/month hard cap
    "default": _Bucket(rate=2.0, capacity=5),  # fallback
}


async def rate_limit(api: str) -> None:
    """Wait if necessary to respect the rate limit for `api`.

    Args:
        api: API name (see _BUCKETS). Unknown names fall back to "default".
    """
    bucket = _BUCKETS.get(api, _BUCKETS["default"])
    await bucket.acquire()
