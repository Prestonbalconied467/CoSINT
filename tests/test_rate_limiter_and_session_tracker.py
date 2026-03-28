import asyncio
import pytest


def test_bucket_acquire_no_wait(monkeypatch):
    import shared.rate_limiter as rl

    # small rate, large tokens to ensure immediate acquire
    b = rl._Bucket(rate=100.0, capacity=10.0)
    # post_init sets tokens to capacity
    assert b.tokens == 10.0

    # ensure acquire does not await sleep when tokens available
    called = {"sleep": False}

    async def fake_sleep(t):
        called["sleep"] = True

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    # run acquire
    asyncio.run(b.acquire())
    assert not called["sleep"]


def test_bucket_acquire_waits_when_empty(monkeypatch):
    import shared.rate_limiter as rl

    # Create a bucket with rate 1/sec and capacity 0 so it must wait
    b = rl._Bucket(rate=1.0, capacity=0.0)
    # force tokens to 0 and last to now-0
    b.tokens = 0.0
    import time

    b.last = time.monotonic()

    waited = {"t": 0}

    async def fake_sleep(t):
        waited["t"] = t

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    asyncio.run(b.acquire())
    assert waited["t"] >= 0


def test_rate_limit_uses_default_bucket_for_unknown_api(monkeypatch):
    import shared.rate_limiter as rl

    # monkeypatch a bucket.acquire to record calls
    called = {"acquired": False}

    async def fake_acquire():
        called["acquired"] = True

    monkeypatch.setattr(rl._BUCKETS["default"], "acquire", fake_acquire)

    asyncio.run(rl.rate_limit("unknown_api"))
    assert called["acquired"]


def test_session_tracker_wrap_tool_records_success_and_error(monkeypatch):
    import shared.session_tracker as st

    tracker = st.SessionRunTracker(max_events=10)

    def simple(a=1):
        return a + 1

    wrapped = tracker.wrap_tool(simple)
    res = wrapped(a=2)
    assert res == 3
    runs = tracker.list_runs()
    assert len(runs) == 1
    assert runs[0]["status"] == "success"

    def fails():
        raise ValueError("boom")

    wrapped_fail = tracker.wrap_tool(fails)
    with pytest.raises(ValueError):
        wrapped_fail()
    runs = tracker.list_runs()
    # two runs recorded
    assert len(runs) == 2
    assert any(r["status"] == "error" for r in runs)


def test_session_tracker_wrap_tool_async_records(monkeypatch):
    import shared.session_tracker as st

    tracker = st.SessionRunTracker(max_events=10)

    async def async_ok(x=1):
        await asyncio.sleep(0)
        return x + 5

    wrapped = tracker.wrap_tool(async_ok)
    out = asyncio.run(wrapped(3))
    assert out == 8
    runs = tracker.list_runs()
    assert len(runs) >= 1
    assert runs[-1]["status"] == "success"

