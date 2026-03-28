import types
import pytest


def test_extract_token_counts_with_attribute_style(monkeypatch):
    import agent_runtime.models as m

    class Resp:
        class usage:
            prompt_tokens = 2
            completion_tokens = 3
            total_tokens = 5

    # monkeypatch litellm completion_cost
    fake_litellm = types.SimpleNamespace(
        completion_cost=lambda completion_response: 0.123
    )
    monkeypatch.setitem(__import__("sys").modules, "litellm", fake_litellm)

    p, c, t, cost = m._extract_token_counts(Resp())
    assert (p, c, t) == (2, 3, 5)
    assert cost == pytest.approx(0.123)


def test_usage_stats_apply_accumulates(monkeypatch):
    import agent_runtime.models as m

    class Resp:
        usage = types.SimpleNamespace(
            prompt_tokens=1, completion_tokens=1, total_tokens=2
        )

    stats = m.UsageStats()
    stats.apply(Resp())
    assert stats.total_tokens == 2
    assert stats.prompt_tokens == 1

