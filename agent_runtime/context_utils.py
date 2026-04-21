from __future__ import annotations

from collections import Counter
import re

from shared.config import (
    COMPRESSOR_ASSISTANT_INSIGHT_COUNT,
    COMPRESSOR_ASSISTANT_INSIGHT_LENGTH,
    COMPRESSOR_FALLBACK_CHARS_PER_TOKEN,
    COMPRESSOR_FALLBACK_MSG_OVERHEAD,
    COMPRESSOR_MAX_SUMMARY_CHARS,
    COMPRESSOR_SNIPPET_MAX_COUNT,
    COMPRESSOR_SNIPPET_MAX_LENGTH,
)

try:
    import litellm
except ImportError:
    litellm = None  # type: ignore

# Conservative fallback when the model's real limit cannot be determined.
# Kept intentionally low so compression fires early rather than too late.
_FALLBACK_MAX_TOKENS = 8_192


def get_model_max_tokens(model: str, fallback: int = _FALLBACK_MAX_TOKENS) -> int:
    """Return the input context-window size for *model* using LiteLLM's model info.

    Preference order:
      1. ``max_input_tokens``  – tokens available for the prompt
      2. ``max_tokens``        – total context (input + output), used as a proxy
      3. *fallback*            – caller-supplied or module-level conservative default

    Always returns a positive integer. Using the real limit (rather than a
    config constant) ensures that ``maybe_compress_context`` fires before the
    provider rejects the request with "Request Entity Too Large".
    """
    if litellm is not None:
        try:
            info = litellm.get_model_info(model)
            for key in ("max_input_tokens", "max_tokens"):
                limit = info.get(key)
                if isinstance(limit, int) and limit > 0:
                    return limit
        except Exception:
            pass
    return fallback


def estimate_tokens(messages: list[dict], model: str | None = None) -> tuple[int, bool]:
    """Estimate token usage, preferring LiteLLM's tokenizer with heuristic fallback.

    Returns: (estimated_tokens, used_fallback)
    """
    if litellm is not None and model:
        try:
            est = int(litellm.token_counter(model=model, messages=messages))
            if est > 0:
                return est, False
        except Exception:
            pass

    # Fallback heuristic: approximate chars_per_token + small per-message overhead.
    chars = 0
    for msg in messages:
        content = str(msg.get("content", ""))
        chars += len(content)
        chars += COMPRESSOR_FALLBACK_MSG_OVERHEAD
    return max(1, chars // COMPRESSOR_FALLBACK_CHARS_PER_TOKEN), True


# ---------------------------------------------------------------------------
# Snippet scoring — heuristic value of a tool-output fragment
# ---------------------------------------------------------------------------


# Patterns that indicate a fragment contains a concrete finding worth keeping.
# Each match adds weight; fragments are ranked by total score.
_FINDING_PATTERNS: list[tuple[re.Pattern, int]] = [
    (re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"), 3),  # IPv4
    (
        re.compile(
            r"\b[a-fA-F0-9]{32}\b|\b[a-fA-F0-9]{40}\b|"
            r"\b[a-fA-F0-9]{64}\b"
        ),
        3,
    ),  # MD5/SHA hash
    (re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}"), 3),  # email
    (re.compile(r"https?://[^\s]{8,}"), 2),  # URL
    (
        re.compile(
            r"\b(?:[a-zA-Z0-9\-]{2,63}\.){1,4}"
            r"(?:com|net|org|io|gov|edu|co|uk|de|ru|cn)\b"
        ),
        2,
    ),  # domain
    (
        re.compile(
            r"\b(?:error|fail|exception|denied|timeout|"
            r"vuln|exploit|leak|exposed|open|found)\b",
            re.I,
        ),
        2,
    ),  # event words
    (re.compile(r"\b\d{1,5}/(?:tcp|udp)\b", re.I), 2),  # port/proto
    (re.compile(r"@[A-Za-z0-9_]{2,30}\b"), 1),  # @handle
    (re.compile(r"\+?[\d\s\-().]{7,20}"), 1),  # phone-ish
]


def _score_snippet(text: str) -> int:
    """Return an informativeness score for a tool-output fragment.

    Score = base length bonus + weighted regex hits.
    Higher is more worth keeping in a compressed summary.
    """
    score = min(len(text) // 60, 4)  # up to 4 pts for length
    for pattern, weight in _FINDING_PATTERNS:
        hits = len(pattern.findall(text))
        score += hits * weight
    return score


def _dedup_snippets(
    snippets: list[tuple[str, int]],
) -> list[tuple[str, int]]:
    """Remove near-duplicate snippets using a normalised 64-char prefix key."""
    seen: set[str] = set()
    out: list[tuple[str, int]] = []
    for text, score in snippets:
        key = re.sub(r"\s+", " ", text[:64]).lower().strip()
        if key not in seen:
            seen.add(key)
            out.append((text, score))
    return out


def _extract_assistant_insights(messages: list[dict]) -> list[str]:
    """Pull the last meaningful sentence from each assistant text block.

    Skips pure tool-call turns (no text content) and very short filler.
    Deduplicates by 40-char normalised prefix.
    """
    seen: set[str] = set()
    insights: list[str] = []
    for msg in messages:
        if msg.get("role") != "assistant":
            continue
        content = msg.get("content") or ""
        if not isinstance(content, str) or not content.strip():
            continue
        # split on sentence boundaries, keep substantive ones
        sentences = [
            s.strip()
            for s in re.split(r"(?<=[.!?])\s+", content)
            if len(s.strip()) > 30
        ]
        if not sentences:
            continue
        pick = sentences[-1][
            :COMPRESSOR_ASSISTANT_INSIGHT_LENGTH
        ]  # last sentence = most likely conclusion
        key = re.sub(r"\s+", " ", pick[:40]).lower()
        if key not in seen:
            seen.add(key)
            insights.append(pick)
    return insights


# ---------------------------------------------------------------------------
# Main compressor
# ---------------------------------------------------------------------------


def compress_messages(
    messages: list[dict],
    keep_last: int = 24,
) -> tuple[list[dict], bool]:
    """Compress *messages* by summarising the middle section.

    Improvements over the naïve version:
    - Tool outputs are **scored** by information density (IPs, domains, hashes,
      error keywords, ports, etc.) and **deduplicated** by content prefix before
      being included in the summary — so the most useful fragments survive.
    - Assistant messages are mined for their **concluding sentences** (likely
      to contain decisions or findings) rather than just counted.
    - The summary block is structured so the agent can clearly distinguish
      tool evidence from its own prior reasoning.
    - ``keep_last`` is caller-controlled so ``maybe_compress_context`` can
      tighten the window across successive passes when needed.
    """
    if len(messages) <= keep_last + 2:
        return messages, False

    system = messages[0]
    tail_start = len(messages) - keep_last

    # Never split inside an assistant-tool transaction block.
    while tail_start > 1 and messages[tail_start].get("role") == "tool":
        tail_start -= 1
    if (
        tail_start > 1
        and messages[tail_start - 1].get("role") == "assistant"
        and messages[tail_start - 1].get("tool_calls")
    ):
        tail_start -= 1

    middle = messages[1:tail_start]
    tail = messages[tail_start:]

    # ── Collect raw material from the middle ────────────────────────────
    tool_count = assistant_count = user_count = 0
    tool_names: list[str] = []
    raw_snippets: list[tuple[str, int]] = []  # (text, score)

    for msg in middle:
        role = msg.get("role", "")
        if role == "tool":
            tool_count += 1
            name = str(msg.get("name", "unknown"))
            tool_names.append(name)
            content = str(msg.get("content", "")).strip()
            if content:
                fragment = content[:COMPRESSOR_SNIPPET_MAX_LENGTH].replace("\n", " ")
                raw_snippets.append((fragment, _score_snippet(fragment)))
        elif role == "assistant":
            assistant_count += 1
        elif role == "user":
            user_count += 1

    # ── Build tool summary ───────────────────────────────────────────────
    top_tools = Counter(tool_names).most_common(10)
    tool_summary = ", ".join(f"{name} ×{count}" for name, count in top_tools) or "none"

    # ── Rank, deduplicate, select best snippets ──────────────────────────
    raw_snippets.sort(key=lambda x: x[1], reverse=True)
    deduped = _dedup_snippets(raw_snippets)
    top_snippets = [text for text, _ in deduped[:COMPRESSOR_SNIPPET_MAX_COUNT]]
    snippet_block = (
        "\n".join(f"  • {s}" for s in top_snippets)
        or "  • no significant outputs recorded"
    )

    # ── Extract assistant insights ───────────────────────────────────────
    insights = _extract_assistant_insights(middle)
    insight_count = min(len(insights), COMPRESSOR_ASSISTANT_INSIGHT_COUNT)
    if insight_count > 0:
        insight_block = "\n".join(f"  • {i}" for i in insights[-insight_count:])
    else:
        insight_block = "  • none recorded"

    summary_message = {
        "role": "user",
        "content": (
            "[CONTEXT COMPRESSION SUMMARY]\n"
            "Older conversation history has been compressed to manage context size.\n"
            f"Compressed message counts — user: {user_count}, "
            f"assistant: {assistant_count}, tool: {tool_count}.\n"
            f"Tools invoked: {tool_summary}.\n\n"
            "Highest-value tool findings (scored by information density, deduplicated):\n"
            f"{snippet_block}\n\n"
            "Agent conclusions from compressed turns:\n"
            f"{insight_block}\n\n"
            "Continue the investigation using the above as prior context, "
            "and the uncompressed messages that follow as the current state."
        ),
    }

    # Safety: cap the compressed summary to avoid creating a replacement that's
    # larger than desired. This truncates the summary content if necessary.
    content = summary_message["content"]
    if len(content) > COMPRESSOR_MAX_SUMMARY_CHARS:
        truncated = content[: COMPRESSOR_MAX_SUMMARY_CHARS - 3].rstrip() + "..."
        summary_message["content"] = truncated

    return [system, summary_message, *tail], True


__all__ = ["estimate_tokens", "compress_messages", "get_model_max_tokens", "_FALLBACK_MAX_TOKENS"]