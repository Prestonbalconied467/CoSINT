"""
agent_runtime/scanner/context_compression.py

Adaptive context-compression policy for scanner rounds.
"""

from __future__ import annotations

from shared.config import (
    COMPRESSOR_KEEP_LAST_MAX,
    COMPRESSOR_KEEP_LAST_MIN,
    COMPRESSOR_MAX_COMPRESSION_PASSES,
    COMPRESSOR_PRESSURE,
)

from ..context_utils import estimate_tokens
from ..display import print_context_note
from ..investigation.events import record_event


def maybe_compress_context(ctx: "ScanContext", round_num: int) -> None:
    threshold = int(ctx.max_context_tokens * ctx.compression_threshold)

    for pass_num in range(COMPRESSOR_MAX_COMPRESSION_PASSES):
        est, used_fallback = estimate_tokens(ctx.convo.history, model=ctx.model)

        if used_fallback and not ctx.estimate_fallback_announced:
            ctx.estimate_fallback_announced = True
            print_context_note("token estimate fallback active")
            record_event(
                ctx.events,
                ctx.event_log_size,
                round_num + 1,
                "context",
                "token estimate fallback",
            )

        if est < threshold:
            break

        ratio = est / max(ctx.max_context_tokens, 1)
        adjusted = max(ratio * COMPRESSOR_PRESSURE, 0.001)
        keep_last = max(COMPRESSOR_KEEP_LAST_MIN, int(COMPRESSOR_KEEP_LAST_MAX / adjusted))

        record_event(
            ctx.events,
            ctx.event_log_size,
            round_num + 1,
            "context",
            f"compression_attempt pass={pass_num + 1} est={est:,} used_fallback={used_fallback} "
            f"ratio={ratio:.2f} adjusted={adjusted:.2f} keep_last={keep_last} history_len={len(ctx.convo.history)}",
        )

        before_len = len(ctx.convo.history)
        changed = ctx.convo.compress(keep_last=keep_last)
        after_len = len(ctx.convo.history)
        if changed:
            ctx.usage.compressed_events += 1
            summary_chars = 0
            try:
                summary = (
                    ctx.convo.history[1].get("content", "")
                    if len(ctx.convo.history) > 1
                    else ""
                )
                summary_chars = len(str(summary))
            except Exception:
                summary_chars = 0
            record_event(
                ctx.events,
                ctx.event_log_size,
                round_num + 1,
                "context",
                f"compressed pass={pass_num + 1} est={est:,} "
                f"keep_last={keep_last} threshold={threshold:,} before={before_len} after={after_len} summary_chars={summary_chars}",
            )
            print_context_note(
                f"context compressed (pass {pass_num + 1}/{COMPRESSOR_MAX_COMPRESSION_PASSES}, "
                f"was approx {est:,} tokens, keep_last={keep_last}, removed={before_len - after_len} msgs)"
            )
        else:
            print_context_note(
                f"context compression exhausted after {pass_num} pass(es) "
                f"-- history too short to compress further (est approx {est:,})"
            )
            record_event(
                ctx.events,
                ctx.event_log_size,
                round_num + 1,
                "context",
                f"compression exhausted at pass={pass_num} est={est:,}",
            )
            break


__all__ = ["maybe_compress_context"]

