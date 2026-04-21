import asyncio
import sys
import types

import pytest


def test_strip_fences_removes_code_fences_and_preserves_plain_text():
    import agent_runtime.llm as llm

    fenced = """```json
    {"ok": true}
    ```"""
    assert llm._strip_fences(fenced) == '{"ok": true}'

    plain = "no fences here"
    assert llm._strip_fences(plain) == plain


def test_tool_call_id_from_obj_handles_various_inputs():
    import agent_runtime.llm as llm

    class ObjWithId:
        def __init__(self, id=None):
            self.id = id

    assert llm._tool_call_id_from_obj({"id": 123}) == "123"
    assert llm._tool_call_id_from_obj({}) is None
    assert llm._tool_call_id_from_obj(ObjWithId("x")) == "x"
    assert llm._tool_call_id_from_obj(ObjWithId()) is None


def test_validate_tool_call_sequence_accepts_correct_tool_block():
    import agent_runtime.llm as llm

    messages = [
        {"role": "system", "content": "s"},
        {"role": "assistant", "tool_calls": [{"id": "a"}, {"id": "b"}]},
        {"role": "tool", "tool_call_id": "a", "content": "r1"},
        {"role": "tool", "tool_call_id": "b", "content": "r2"},
    ]

    # Should not raise
    llm._validate_tool_call_sequence(messages)


def test_validate_tool_call_sequence_raises_when_responses_missing():
    import agent_runtime.llm as llm

    messages = [
        {"role": "assistant", "tool_calls": [{"id": "x"}, {"id": "y"}]},
        {"role": "tool", "tool_call_id": "x"},
    ]

    with pytest.raises(llm.LLMError):
        llm._validate_tool_call_sequence(messages)


def test_validate_tool_call_sequence_raises_on_non_tool_in_block():
    import agent_runtime.llm as llm

    messages = [
        {"role": "assistant", "tool_calls": [{"id": "1"}]},
        {"role": "user", "content": "interrupt"},
    ]

    with pytest.raises(llm.LLMError):
        llm._validate_tool_call_sequence(messages)


def test_validate_tool_call_sequence_raises_on_missing_tool_call_id():
    import agent_runtime.llm as llm

    messages = [
        {"role": "assistant", "tool_calls": [{"id": "1"}]},
        {"role": "tool", "content": "no id here"},
    ]

    with pytest.raises(llm.LLMError):
        llm._validate_tool_call_sequence(messages)


def test_validate_tool_call_sequence_raises_on_mismatched_ids():
    import agent_runtime.llm as llm

    messages = [
        {"role": "assistant", "tool_calls": [{"id": "a"}, {"id": "b"}]},
        {"role": "tool", "tool_call_id": "a"},
        {"role": "tool", "tool_call_id": "z"},
    ]

    with pytest.raises(llm.LLMError):
        llm._validate_tool_call_sequence(messages)


def test_confidence_log_behaviour_add_trim_and_context_block():
    import agent_runtime.llm as llm

    log = llm.ConfidenceLog(max_entries=3)
    entries = [
        llm.ConfidenceEntry(
            kind="k", value=str(i), score=0.5, approved=False, reason="r"
        )
        for i in range(5)
    ]
    log.add_many(entries)
    # Should trim to most recent 3
    assert len(log.entries) == 3
    # as_context_block returns a JSON-like string containing the recent entries
    block = log.as_context_block()
    assert isinstance(block, str) and "Prior confidence decisions" in block
    # disable and verify is_empty
    log.enabled = False
    assert log.is_empty()


def test_llmusage_apply_updates_tokens_and_counts(monkeypatch):
    import agent_runtime.llm as llm

    # Monkeypatch the token extractor used inside LLMUsage.apply
    monkeypatch.setattr(llm, "_extract_token_counts", lambda resp: (2, 3, 5, 0.02))
    usage = llm.LLMUsage()
    usage.apply(object())
    assert usage.prompt_tokens == 2
    assert usage.completion_tokens == 3
    assert usage.total_tokens == 5
    assert usage.cost_usd == pytest.approx(0.02)
    assert usage.calls == 1


def test_is_system_role_error_detects_variants():
    import agent_runtime.llm as llm

    assert llm.is_system_role_error(Exception("System role not allowed"))
    assert llm.is_system_role_error(Exception("Only one system"))
    assert not llm.is_system_role_error(Exception("some other error"))


def test_complete_raises_when_litellm_missing():
    import agent_runtime.llm as llm

    # Ensure litellm is not importable
    if "litellm" in sys.modules:
        del sys.modules["litellm"]

    with pytest.raises(llm.LLMError):
        asyncio.run(
            llm.complete(model="m", messages=[{"role": "user", "content": "x"}])
        )


def test_complete_returns_text_and_applies_usage(monkeypatch):
    import agent_runtime.llm as llm

    # Fake litellm completion that returns a nested message
    def fake_completion(**kwargs):
        return types.SimpleNamespace(
            choices=[
                types.SimpleNamespace(
                    message=types.SimpleNamespace(content="  result  ")
                )
            ]
        )

    fake_mod = types.SimpleNamespace(completion=fake_completion, drop_params=False)
    monkeypatch.setitem(sys.modules, "litellm", fake_mod)

    monkeypatch.setattr(llm, "_extract_token_counts", lambda resp: (1, 1, 2, 0.0))
    usage = llm.LLMUsage()
    out = asyncio.run(
        llm.complete(
            model="m", messages=[{"role": "user", "content": "x"}], usage=usage
        )
    )
    assert out == "result"
    assert usage.calls == 1
    assert usage.total_tokens == 2


def test_complete_json_parses_and_raises_on_bad_json(monkeypatch):
    import agent_runtime.llm as llm

    def ok_completion(**kwargs):
        return types.SimpleNamespace(
            choices=[
                types.SimpleNamespace(
                    message=types.SimpleNamespace(content='```json\n{"ok":true}\n```')
                )
            ]
        )

    def bad_completion(**kwargs):
        return types.SimpleNamespace(
            choices=[
                types.SimpleNamespace(message=types.SimpleNamespace(content="not json"))
            ]
        )

    fake_ok = types.SimpleNamespace(completion=ok_completion, drop_params=False)
    monkeypatch.setitem(sys.modules, "litellm", fake_ok)
    parsed = asyncio.run(
        llm.complete_json(
            model="m", messages=[{"role": "user", "content": "x"}], expect=dict
        )
    )
    assert isinstance(parsed, dict) and parsed.get("ok") is True

    fake_bad = types.SimpleNamespace(completion=bad_completion, drop_params=False)
    monkeypatch.setitem(sys.modules, "litellm", fake_bad)
    with pytest.raises(llm.LLMParseError):
        asyncio.run(
            llm.complete_json(
                model="m", messages=[{"role": "user", "content": "x"}], expect=dict
            )
        )


def test_conversation_complete_validates_tool_sequence_and_passes_tools(monkeypatch):
    import agent_runtime.llm as llm

    # stub completion to capture kwargs and return message
    captured = {}

    def capture_completion(**kwargs):
        captured.update(kwargs)
        return types.SimpleNamespace(
            choices=[
                types.SimpleNamespace(
                    message=types.SimpleNamespace(content="ok", role="assistant")
                )
            ]
        )

    fake_mod = types.SimpleNamespace(completion=capture_completion, drop_params=False)
    monkeypatch.setitem(sys.modules, "litellm", fake_mod)

    conv = llm.Conversation(model="m", messages=[{"role": "system", "content": "s"}])
    msg = asyncio.run(
        conv.complete(
            tools=[{"name": "t"}],
            tool_choice="manual",
            extra_messages=[{"role": "user", "content": "u"}],
        )
    )

    assert "tools" in captured
    assert captured.get("tool_choice") == "manual"
    assert hasattr(msg, "content") and msg.content == "ok"

