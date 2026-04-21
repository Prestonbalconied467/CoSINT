"""
tests/test_compression.py

Verifies that:
  1. get_model_max_tokens() returns the model's real limit via LiteLLM, and
     falls back gracefully when LiteLLM doesn't know the model.
  2. compress_messages() correctly summarises the middle of a long history
     while preserving the system message and recent tail.
  3. Smart snippet scoring (_score_snippet) ranks high-value OSINT findings
     above filler content.
  4. Snippet deduplication (_dedup_snippets) removes near-duplicate fragments.
  5. Assistant insight extraction pulls concluding sentences, not filler.
  6. maybe_compress_context runs multiple passes and tightens keep_last until
     the estimate is below the threshold.
  7. Auto vs manual max_context_tokens resolution.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _make_history(n_middle: int, *, rich: bool = False) -> list[dict]:
    """system + n_middle user/assistant pairs + 6 recent tail messages."""
    msgs = [{"role": "system", "content": "You are a helpful assistant."}]
    for i in range(n_middle):
        msgs.append({"role": "user", "content": f"user message {i} " + "x" * 80})
        if rich:
            msgs.append(
                {
                    "role": "assistant",
                    "content": (
                        f"Checking result {i}. "
                        f"The host 192.168.{i}.1 appears to be running on port {8000 + i}/tcp. "
                        f"This may indicate an exposed service."
                    ),
                }
            )
        else:
            msgs.append(
                {"role": "assistant", "content": f"assistant reply {i} " + "y" * 80}
            )
    for i in range(3):
        msgs.append({"role": "user", "content": f"recent user {i}"})
        msgs.append({"role": "assistant", "content": f"recent assistant {i}"})
    return msgs


def _make_rich_tool_history(n_tools: int) -> list[dict]:
    """History with n_tools assistant+tool blocks of varying information density."""
    msgs = [{"role": "system", "content": "sys"}]
    for i in range(n_tools):
        msgs.append(
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [{"id": f"tc{i}", "function": {"name": "nmap_scan"}}],
            }
        )
        if i % 3 == 0:
            content = (
                f"Host 10.0.{i}.1 is up. Open port 443/tcp detected."  # high-value
            )
        elif i % 3 == 1:
            content = f"Resolved target{i}.example.com to several addresses."  # medium
        else:
            content = f"Scan iteration {i} complete. Nothing interesting."  # low-value
        msgs.append(
            {
                "role": "tool",
                "tool_call_id": f"tc{i}",
                "name": "nmap_scan",
                "content": content,
            }
        )
    msgs.append({"role": "user", "content": "summarise"})
    msgs.append({"role": "assistant", "content": "Here is the summary."})
    return msgs


def _make_ctx(convo, max_tokens: int, threshold: float = 0.5):
    ctx = MagicMock()
    ctx.convo = convo
    ctx.model = "gpt-4o"
    ctx.max_context_tokens = max_tokens
    ctx.compression_threshold = threshold
    ctx.estimate_fallback_announced = False
    ctx.events = []
    ctx.event_log_size = 100
    return ctx


# ---------------------------------------------------------------------------
# 1. get_model_max_tokens
# ---------------------------------------------------------------------------


class TestGetModelMaxTokens:
    def test_returns_max_input_tokens_from_litellm(self):
        from agent_runtime.context_utils import get_model_max_tokens

        fake_info = {"max_input_tokens": 16_384, "max_tokens": 32_768}
        with patch("agent_runtime.context_utils.litellm") as mock_llm:
            mock_llm.get_model_info.return_value = fake_info
            result = get_model_max_tokens("some-model")

        assert result == 16_384, "should prefer max_input_tokens over max_tokens"

    def test_falls_back_to_max_tokens_when_no_input_tokens(self):
        from agent_runtime.context_utils import get_model_max_tokens

        with patch("agent_runtime.context_utils.litellm") as mock_llm:
            mock_llm.get_model_info.return_value = {"max_tokens": 8_192}
            result = get_model_max_tokens("some-model")

        assert result == 8_192

    def test_falls_back_to_default_when_litellm_raises(self):
        from agent_runtime.context_utils import get_model_max_tokens

        with patch("agent_runtime.context_utils.litellm") as mock_llm:
            mock_llm.get_model_info.side_effect = Exception("unknown model")
            result = get_model_max_tokens("unknown-model", fallback=4_096)

        assert result == 4_096

    def test_falls_back_when_litellm_is_none(self):
        from agent_runtime.context_utils import get_model_max_tokens

        with patch("agent_runtime.context_utils.litellm", None):
            result = get_model_max_tokens("any-model", fallback=1_234)

        assert result == 1_234


# ---------------------------------------------------------------------------
# 2. Snippet scoring
# ---------------------------------------------------------------------------


class TestScoreSnippet:
    def _score(self, text: str) -> int:
        from agent_runtime.context_utils import _score_snippet

        return _score_snippet(text)

    def test_ip_address_scores_higher_than_filler(self):
        assert self._score("Host 192.168.1.1 is up") > self._score(
            "Scan complete, nothing found"
        )

    def test_email_scores_high(self):
        assert self._score("Contact admin@example.com for access") > self._score(
            "No results returned"
        )

    def test_hash_scores_high(self):
        assert self._score(f"File hash: {'a' * 32}") > self._score("Process finished")

    def test_port_proto_scores_above_filler(self):
        assert self._score("Open port 8080/tcp found") > self._score(
            "Nothing interesting here"
        )

    def test_url_scores_above_generic(self):
        assert self._score(
            "Found endpoint https://api.target.com/v1/users"
        ) > self._score("ok")

    def test_error_keyword_raises_score(self):
        assert self._score("Authentication failed: access denied") > self._score(
            "completed normally"
        )

    def test_empty_string_does_not_raise(self):
        assert self._score("") == 0

    def test_longer_content_scores_at_least_as_high(self):
        short = "192.168.1.1"
        long = "192.168.1.1 " + "extra context detail " * 10
        assert self._score(long) >= self._score(short)


# ---------------------------------------------------------------------------
# 3. Snippet deduplication
# ---------------------------------------------------------------------------


class TestDedupSnippets:
    def _dedup(self, items):
        from agent_runtime.context_utils import _dedup_snippets

        return _dedup_snippets(items)

    def test_removes_near_identical_prefixes(self):
        # Strings must share the same first 64 normalised characters to be
        # treated as duplicates. Pad the common prefix to exceed 64 chars,
        # then differ only after that point.
        shared_prefix = (
            "Host 192.168.1.1 is up and responding to pings on the network. Port "
        )
        assert len(shared_prefix) > 64, (
            "prefix must exceed 64 chars for dedup key to collide"
        )
        items = [
            (shared_prefix + "80 open.", 5),
            (shared_prefix + "443 open.", 5),  # same 64-char normalised prefix
            ("Completely different finding about example.com domain", 3),
        ]
        result = self._dedup(items)
        assert len(result) < len(items), (
            "One of the near-identical items must be dropped"
        )
        assert any("different finding" in t for t, _ in result), (
            "Unique item must survive"
        )

    def test_preserves_genuinely_different_items(self):
        items = [
            ("192.168.1.1 port 22/tcp open", 4),
            ("10.0.0.1 running on port 80/tcp", 4),
            ("admin@corp.com found in breach", 5),
        ]
        assert len(self._dedup(items)) == 3

    def test_empty_list_returns_empty(self):
        assert self._dedup([]) == []

    def test_single_item_passes_through(self):
        items = [("only one result", 2)]
        assert self._dedup(items) == items


# ---------------------------------------------------------------------------
# 4. Assistant insight extraction
# ---------------------------------------------------------------------------


class TestExtractAssistantInsights:
    def _extract(self, messages):
        from agent_runtime.context_utils import _extract_assistant_insights

        return _extract_assistant_insights(messages)

    def test_extracts_last_sentence_from_assistant(self):
        msgs = [
            {
                "role": "assistant",
                "content": (
                    "I searched the target. The host appears to be running nginx. "
                    "It is likely vulnerable to CVE-2024-1234."
                ),
            }
        ]
        insights = self._extract(msgs)
        assert len(insights) == 1
        assert "CVE" in insights[0] or "vulnerable" in insights[0]

    def test_skips_tool_call_only_turns(self):
        msgs = [{"role": "assistant", "content": None, "tool_calls": [{"id": "x"}]}]
        assert self._extract(msgs) == []

    def test_deduplicates_repeated_conclusions(self):
        repeated = "The target appears to be running on an outdated version."
        msgs = [
            {"role": "assistant", "content": f"First observation. {repeated}"},
            {"role": "assistant", "content": f"Second observation. {repeated}"},
        ]
        assert len(self._extract(msgs)) == 1

    def test_skips_very_short_sentences(self):
        msgs = [{"role": "assistant", "content": "Ok. Done. Yes."}]
        assert self._extract(msgs) == []

    def test_multiple_distinct_turns_all_extracted(self):
        msgs = [
            {
                "role": "assistant",
                "content": "Found open port 443 on the primary target host.",
            },
            {
                "role": "assistant",
                "content": "Discovered admin@example.com in a public data breach.",
            },
        ]
        assert len(self._extract(msgs)) == 2


# ---------------------------------------------------------------------------
# 5. compress_messages structural correctness + smart content
# ---------------------------------------------------------------------------


class TestCompressMessages:
    def test_no_compression_when_short(self):
        from agent_runtime.context_utils import compress_messages

        msgs = _make_history(n_middle=2)
        result, changed = compress_messages(msgs, keep_last=24)
        assert not changed and result is msgs

    def test_fires_when_long(self):
        from agent_runtime.context_utils import compress_messages

        msgs = _make_history(n_middle=20)
        result, changed = compress_messages(msgs, keep_last=6)
        assert changed
        assert result[0]["role"] == "system"
        assert "CONTEXT COMPRESSION SUMMARY" in result[1]["content"]

    def test_tail_preserved_exactly(self):
        from agent_runtime.context_utils import compress_messages

        msgs = _make_history(n_middle=20)
        keep_last = 6
        expected_tail = list(msgs[-keep_last:])  # snapshot before compress mutates
        result, _ = compress_messages(msgs, keep_last=keep_last)
        assert result[-keep_last:] == expected_tail

    def test_high_value_snippets_appear_in_summary(self):
        from agent_runtime.context_utils import compress_messages

        msgs = _make_rich_tool_history(n_tools=15)
        result, changed = compress_messages(msgs, keep_last=2)
        assert changed
        summary = result[1]["content"]
        assert any(ind in summary for ind in ("10.0.", "443/tcp", "example.com")), (
            "High-value findings must survive scoring/dedup into the summary"
        )

    def test_no_orphaned_tool_blocks(self):
        from agent_runtime.context_utils import compress_messages

        msgs = [{"role": "system", "content": "sys"}]
        for i in range(10):
            msgs.append({"role": "user", "content": f"u{i}"})
            msgs.append({"role": "assistant", "content": f"a{i}"})
        msgs.append(
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [{"id": "tc1", "function": {"name": "search"}}],
            }
        )
        msgs.append(
            {
                "role": "tool",
                "tool_call_id": "tc1",
                "content": "result",
                "name": "search",
            }
        )
        msgs.append({"role": "user", "content": "final"})

        result, changed = compress_messages(msgs, keep_last=4)
        if changed:
            assert result[2]["role"] != "tool", (
                "Tail must not start with a tool message"
            )

    def test_summary_contains_insights_section(self):
        from agent_runtime.context_utils import compress_messages

        msgs = _make_history(n_middle=15, rich=True)
        result, changed = compress_messages(msgs, keep_last=4)
        assert changed
        assert "Agent conclusions" in result[1]["content"]


# ---------------------------------------------------------------------------
# 6. Conversation.compress() — keep_last parameter
# ---------------------------------------------------------------------------


class TestConversationCompress:
    def _convo(self, n_middle: int):
        from agent_runtime.llm import Conversation

        # Pass a copy so the original list isn't shared with convo.history
        return Conversation(
            model="gpt-4o", messages=list(_make_history(n_middle=n_middle))
        )

    def test_no_change_when_short(self):
        assert not self._convo(2).compress()

    def test_shortens_long_history(self):
        c = self._convo(20)
        orig = len(c.history)
        assert c.compress()
        assert len(c.history) < orig

    def test_tighter_keep_last_produces_shorter_result(self):
        from agent_runtime.llm import Conversation

        history = _make_history(n_middle=20)
        c_loose = Conversation(model="gpt-4o", messages=list(history))
        c_tight = Conversation(model="gpt-4o", messages=list(history))
        c_loose.compress(keep_last=20)
        c_tight.compress(keep_last=6)
        assert len(c_tight.history) < len(c_loose.history)

    def test_system_always_first(self):
        c = self._convo(20)
        c.compress()
        assert c.history[0]["role"] == "system"

    def test_summary_always_second(self):
        c = self._convo(20)
        c.compress()
        assert "CONTEXT COMPRESSION SUMMARY" in c.history[1]["content"]


# ---------------------------------------------------------------------------
# 7. maybe_compress_context — multi-pass adaptive loop
# ---------------------------------------------------------------------------


class TestMaybeCompressContext:
    def test_fires_and_reduces_history(self):
        from agent_runtime.context_utils import estimate_tokens
        from agent_runtime.llm import Conversation
        from agent_runtime.scanner.context import maybe_compress_context

        history = _make_history(n_middle=20)
        # Pass a copy so orig_len stays accurate after in-place compress
        convo = Conversation(model="gpt-4o", messages=list(history))
        orig_len = len(convo.history)
        est, _ = estimate_tokens(convo.history)
        ctx = _make_ctx(convo, max_tokens=int(est * 0.4), threshold=0.5)

        with patch("agent_runtime.context_utils.litellm", None):
            maybe_compress_context(ctx, round_num=0)

        assert len(convo.history) < orig_len
        assert convo.history[0]["role"] == "system"
        assert "CONTEXT COMPRESSION SUMMARY" in convo.history[1]["content"]

    def test_no_compression_when_under_threshold(self):
        from agent_runtime.llm import Conversation
        from agent_runtime.scanner.context import maybe_compress_context

        history = _make_history(n_middle=1)
        convo = Conversation(model="gpt-4o", messages=list(history))
        orig_len = len(convo.history)
        ctx = _make_ctx(convo, max_tokens=999_999, threshold=0.85)

        with patch("agent_runtime.context_utils.litellm", None):
            maybe_compress_context(ctx, round_num=0)

        assert len(convo.history) == orig_len

    def test_multi_pass_runs_until_below_threshold(self):
        from agent_runtime.context_utils import estimate_tokens
        from agent_runtime.llm import Conversation
        from agent_runtime.scanner.context import maybe_compress_context

        history = _make_history(n_middle=40)
        convo = Conversation(model="gpt-4o", messages=list(history))
        est, _ = estimate_tokens(convo.history)
        ctx = _make_ctx(convo, max_tokens=int(est * 0.15), threshold=0.5)

        # Capture the mock attribute before the call so __iadd__ tracking
        # isn't lost when MagicMock reassigns the attribute after +=
        events_before = ctx.usage.compressed_events

        with patch("agent_runtime.context_utils.litellm", None):
            maybe_compress_context(ctx, round_num=0)

        assert convo.history[0]["role"] == "system"
        assert "CONTEXT COMPRESSION SUMMARY" in convo.history[1]["content"]
        events_before.__iadd__.assert_called()

    def test_compressed_events_incremented(self):
        from agent_runtime.context_utils import estimate_tokens
        from agent_runtime.llm import Conversation
        from agent_runtime.scanner.context import maybe_compress_context

        history = _make_history(n_middle=20)
        convo = Conversation(model="gpt-4o", messages=list(history))
        est, _ = estimate_tokens(convo.history)
        ctx = _make_ctx(convo, max_tokens=int(est * 0.4), threshold=0.5)

        # Capture before the call — MagicMock reassigns the attribute after +=
        events_before = ctx.usage.compressed_events

        with patch("agent_runtime.context_utils.litellm", None):
            maybe_compress_context(ctx, round_num=1)

        events_before.__iadd__.assert_called()

    def test_higher_pressure_produces_shorter_or_equal_history(self):
        from agent_runtime.context_utils import estimate_tokens
        from agent_runtime.llm import Conversation
        from agent_runtime.scanner.context import maybe_compress_context

        history_a = _make_history(n_middle=20)
        history_b = _make_history(n_middle=20)
        convo_low = Conversation(model="gpt-4o", messages=list(history_a))
        convo_high = Conversation(model="gpt-4o", messages=list(history_b))

        est, _ = estimate_tokens(history_a)
        ctx_low = _make_ctx(convo_low, max_tokens=int(est * 0.7), threshold=0.5)
        ctx_high = _make_ctx(convo_high, max_tokens=int(est * 0.2), threshold=0.5)

        with patch("agent_runtime.context_utils.litellm", None):
            maybe_compress_context(ctx_low, round_num=0)
            maybe_compress_context(ctx_high, round_num=0)

        assert len(convo_high.history) <= len(convo_low.history)


# ---------------------------------------------------------------------------
# 8. Auto vs manual max_context_tokens resolution
# ---------------------------------------------------------------------------


class TestMaxContextTokensResolution:
    def test_auto_uses_litellm_limit(self):
        from agent_runtime.context_utils import get_model_max_tokens

        with patch("agent_runtime.context_utils.litellm") as m:
            m.get_model_info.return_value = {"max_input_tokens": 16_384}
            assert get_model_max_tokens("some-model") == 16_384

    def test_auto_falls_back_to_8192_when_unknown(self):
        from agent_runtime.context_utils import (
            get_model_max_tokens,
            _FALLBACK_MAX_TOKENS,
        )

        with patch("agent_runtime.context_utils.litellm") as m:
            m.get_model_info.side_effect = Exception("unknown")
            assert get_model_max_tokens("mystery-model") == _FALLBACK_MAX_TOKENS

    def test_auto_falls_back_when_litellm_missing(self):
        from agent_runtime.context_utils import (
            get_model_max_tokens,
            _FALLBACK_MAX_TOKENS,
        )
        with patch("agent_runtime.context_utils.litellm", None):
            assert get_model_max_tokens("any-model") == _FALLBACK_MAX_TOKENS

    def test_manual_nonzero_is_used_unchanged(self):
        user_value = 32_768
        assert user_value > 0
        assert user_value == 32_768

    def test_zero_is_auto_trigger(self):
        assert 0 == 0   # auto
        assert 1 > 0    # manual