"""
agent_runtime/llm.py – Thin async wrapper for single-shot LLM completions (Simplified)

Centralizes:
  - litellm import guard
  - markdown fence stripping
  - JSON parsing and type validation
  - retry with exponential backoff
  - optional UsageStats collection

Public functions:
  complete      — raw text response
  complete_json — response parsed as JSON dict or list

Both raise LLMError on unrecoverable failure.
Not for multi-turn conversation loops (see Conversation class).
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field
from typing import Any
from agent_runtime.models import _extract_token_counts
from shared.config import LLM_MAX_RETRIES, LLM_RETRY_BACKOFF

_MAX_RETRIES = LLM_MAX_RETRIES
_RETRY_BASE_WAIT = LLM_RETRY_BACKOFF
_RETRYABLE_SUBSTRINGS = frozenset(
    {
        "rate limit",
        "ratelimit",
        "rate_limit",
        "overloaded",
        "capacity",
        "timeout",
        "timed out",
        "connection",
        "service unavailable",
        "internal server error",
        "bad gateway",
        "gateway timeout",
    }
)


# --- Error classes ---
class LLMError(Exception):
    def __init__(self, message: str, *, last_exc: Exception | None = None):
        super().__init__(message)
        self.last_exc = last_exc


class LLMParseError(LLMError):
    def __init__(
        self, message: str, *, raw: str = "", last_exc: Exception | None = None
    ):
        super().__init__(message, last_exc=last_exc)
        self.raw = raw


def _is_retryable(exc: Exception) -> bool:
    return any(s in str(exc).lower() for s in _RETRYABLE_SUBSTRINGS)


def is_system_role_error(exc: Exception) -> bool:
    """Return True if exc looks like a system-role validation error."""
    msg = str(exc).lower()
    return any(
        phrase in msg
        for phrase in (
            "system role",
            "multiple system",
            "only one system",
            "invalid role",
            "unsupported role",
        )
    )


# --- Usage collector ---
@dataclass
class LLMUsage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cost_usd: float = 0.0
    calls: int = 0
    retries: int = 0

    def apply(self, response: Any) -> None:
        p, c, t, cost = _extract_token_counts(response)
        self.prompt_tokens += p
        self.completion_tokens += c
        self.total_tokens += t
        self.cost_usd += cost
        self.calls += 1

    def merge_into(self, stats: Any) -> None:
        for attr in ("prompt_tokens", "completion_tokens", "total_tokens"):
            setattr(stats, attr, getattr(stats, attr, 0) + getattr(self, attr))
        stats.cost_usd = getattr(stats, "cost_usd", 0.0) + self.cost_usd


# --- Confidence log ---
@dataclass
class ConfidenceEntry:
    kind: str
    value: str
    score: float | str
    approved: bool
    reason: str
    round: int = 0
    scope_request: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "value": self.value,
            "score": self.score,
            "approved": self.approved,
            "reason": self.reason,
            "scope_reason": self.scope_request,
        }


@dataclass
class ConfidenceLog:
    entries: list[ConfidenceEntry] = field(default_factory=list)
    max_entries: int = 40
    enabled: bool = True

    def add(self, entry: ConfidenceEntry) -> None:
        if not self.enabled:
            return
        self.entries.append(entry)
        if len(self.entries) > self.max_entries:
            del self.entries[: len(self.entries) - self.max_entries]

    def add_many(self, entries: list[ConfidenceEntry]) -> None:
        for e in entries:
            self.add(e)

    def as_context_block(self, *, max_show: int = 25) -> str:
        if not self.enabled or not self.entries:
            return ""
        recent = self.entries[-max_show:]
        lines = json.dumps([e.as_dict() for e in recent], ensure_ascii=False)
        return (
            "Prior confidence decisions for this investigation (use as reference — you may disagree if new context justifies it):\n"
            f"{lines}"
        )

    def is_empty(self) -> bool:
        return (not self.enabled) or (not self.entries)


# --- Fence stripping ---
_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


def _strip_fences(text: str) -> str:
    return _FENCE_RE.sub("", text).strip()


def _tool_call_id_from_obj(tool_call: Any) -> str | None:
    if isinstance(tool_call, dict):
        tc_id = tool_call.get("id")
        return str(tc_id) if tc_id else None
    tc_id = getattr(tool_call, "id", None)
    return str(tc_id) if tc_id else None


def _validate_tool_call_sequence(messages: list[dict[str, Any]]) -> None:
    for i, msg in enumerate(messages):
        if msg.get("role") != "assistant":
            continue
        tool_calls = msg.get("tool_calls") or []
        if not tool_calls:
            continue
        expected_ids = [
            str(tc.get("id") if isinstance(tc, dict) else getattr(tc, "id", None))
            for tc in tool_calls
        ]
        block_start = i + 1
        block_end = block_start + len(expected_ids)
        if block_end > len(messages):
            missing = ", ".join(expected_ids)
            raise LLMError(
                f"Invalid message history: assistant tool_calls are not followed by complete tool responses (message index {i}, missing ids: {missing})"
            )
        tool_block = messages[block_start:block_end]
        bad_index = next(
            (
                j
                for j, m in enumerate(tool_block, start=block_start)
                if m.get("role") != "tool"
            ),
            None,
        )
        if bad_index is not None:
            raise LLMError(
                f"Invalid message history: non-tool message interrupts tool responses for assistant tool_calls at message index {i} (found role={messages[bad_index].get('role')!r} at index {bad_index})"
            )
        observed_ids = [str(m.get("tool_call_id") or "") for m in tool_block]
        if any(not x for x in observed_ids):
            raise LLMError(
                f"Invalid message history: tool response missing tool_call_id for assistant tool_calls at message index {i}"
            )
        if set(observed_ids) != set(expected_ids):
            raise LLMError(
                f"Invalid message history: tool response ids do not match assistant tool_calls at message index {i} (expected={expected_ids}, observed={observed_ids})"
            )


# --- Core completion (async) ---
async def complete(
    *,
    model: str,
    messages: list[dict[str, str]],
    temperature: float = 0.0,
    usage: LLMUsage | None = None,
) -> str:
    try:
        import litellm
    except ImportError as exc:
        raise LLMError("litellm is not installed") from exc
    litellm.drop_params = True
    last_exc = None
    attempts_made = 0
    wait = _RETRY_BASE_WAIT
    for attempt in range(_MAX_RETRIES + 1):
        attempts_made = attempt + 1
        try:
            response = await asyncio.get_running_loop().run_in_executor(
                None,
                lambda: litellm.completion(
                    model=model,
                    messages=messages,
                    temperature=temperature,
                ),
            )
            if usage is not None:
                usage.apply(response)
            return (response.choices[0].message.content or "").strip()
        except Exception as exc:
            last_exc = exc
            if attempt < _MAX_RETRIES and _is_retryable(exc):
                if usage is not None:
                    usage.retries += 1
                await asyncio.sleep(wait)
                wait *= 2
                continue
            break
    raise LLMError(
        f"LLM call failed after {attempts_made} attempt(s): {last_exc}",
        last_exc=last_exc,
    )


# --- JSON completion ---
async def complete_json(
    *,
    model: str,
    messages: list[dict[str, str]],
    temperature: float = 0.0,
    expect: type = dict,
    usage: LLMUsage | None = None,
) -> Any:
    raw = await complete(
        model=model,
        messages=messages,
        temperature=temperature,
        usage=usage,
    )
    clean = _strip_fences(raw)
    try:
        parsed = json.loads(clean)
    except json.JSONDecodeError as exc:
        raise LLMParseError(
            f"Model returned non-JSON response: {exc}", raw=raw, last_exc=exc
        ) from exc
    if not isinstance(parsed, expect):
        raise LLMParseError(
            f"Expected JSON {expect.__name__}, got {type(parsed).__name__}", raw=raw
        )
    return parsed


# --- Multi-turn conversation wrapper ---
class Conversation:
    def __init__(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        usage: "UsageStats | LLMUsage | None" = None,
    ) -> None:
        self.model = model
        self.messages = messages
        self._usage = usage

    async def complete(
        self,
        *,
        tools: list[dict] | None = None,
        tool_choice: str = "auto",
        extra_messages: list[dict] | None = None,
    ) -> Any:
        try:
            import litellm
        except ImportError as exc:
            raise LLMError("litellm is not installed") from exc
        litellm.drop_params = True
        send_messages = (
            self.messages + extra_messages if extra_messages else self.messages
        )
        _validate_tool_call_sequence(send_messages)
        kwargs = {"model": self.model, "messages": send_messages}
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = tool_choice
        last_exc = None
        attempts_made = 0
        wait = _RETRY_BASE_WAIT
        for attempt in range(_MAX_RETRIES + 1):
            attempts_made = attempt + 1
            try:
                response = await asyncio.get_running_loop().run_in_executor(
                    None, lambda: litellm.completion(**kwargs)
                )
                if self._usage is not None:
                    self._usage.apply(response)
                return response.choices[0].message
            except Exception as exc:
                last_exc = exc
                if attempt < _MAX_RETRIES and _is_retryable(exc):
                    if isinstance(self._usage, LLMUsage):
                        self._usage.retries += 1
                    await asyncio.sleep(wait)
                    wait *= 2
                    continue
                break
        raise LLMError(
            f"LLM conversation call failed after {attempts_made} attempt(s): {last_exc}",
            last_exc=last_exc,
        )

    def append(self, message: dict[str, Any]) -> None:
        self.messages.append(message)

    def append_many(self, messages: list[dict[str, Any]]) -> None:
        self.messages.extend(messages)

    def compress(self, keep_last: int = 24) -> bool:
        """Compress conversation history by summarising the middle section.

        Args:
            keep_last: Number of recent messages to preserve verbatim.
                       Callers can lower this on successive passes when the
                       context is still over budget after a first compression.

        Returns:
            True if the history was modified, False if it was already short
            enough that no compression was possible.
        """
        from .context_utils import compress_messages

        compressed, changed = compress_messages(self.messages, keep_last=keep_last)
        if changed:
            self.messages[:] = compressed
        return changed

    @property
    def history(self) -> list[dict[str, Any]]:
        return self.messages


__all__ = [
    "LLMError",
    "LLMParseError",
    "LLMUsage",
    "ConfidenceEntry",
    "ConfidenceLog",
    "Conversation",
    "complete",
    "complete_json",
    "is_system_role_error",
]
