"""
shared/session_tracker.py

In-memory session tool-run tracking for MCP calls.
"""

from __future__ import annotations

import inspect
import json
import time
import uuid
from collections import deque
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from functools import wraps
from typing import Any, Callable, Deque, get_type_hints


@dataclass
class ToolRunRecord:
    session_id: str
    tool_name: str
    started_at: str
    duration_ms: int
    status: str
    arg_keys: list[str]
    error: str = ""


class SessionRunTracker:
    """Tracks tool executions for the current MCP server session (process lifetime)."""

    def __init__(self, max_events: int = 500) -> None:
        self.session_id = uuid.uuid4().hex
        self._events: Deque[ToolRunRecord] = deque(maxlen=max_events)

    def _append(self, record: ToolRunRecord) -> None:
        self._events.append(record)

    def wrap_tool(self, fn: Callable[..., Any]) -> Callable[..., Any]:
        sig = inspect.signature(fn)
        tool_name = fn.__name__
        try:
            resolved_annotations = get_type_hints(fn, include_extras=True)
        except Exception:
            # Fallback to original annotations if type-hint resolution fails.
            resolved_annotations = dict(getattr(fn, "__annotations__", {}))

        resolved_params = [
            p.replace(annotation=resolved_annotations.get(p.name, p.annotation))
            for p in sig.parameters.values()
        ]
        resolved_sig = sig.replace(
            parameters=resolved_params,
            return_annotation=resolved_annotations.get("return", sig.return_annotation),
        )

        if inspect.iscoroutinefunction(fn):

            @wraps(fn)
            async def async_wrapped(*args: Any, **kwargs: Any) -> Any:
                start = time.perf_counter()
                started_at = datetime.now(timezone.utc).isoformat()
                arg_keys = sorted(kwargs.keys())
                try:
                    result = await fn(*args, **kwargs)
                    self._append(
                        ToolRunRecord(
                            session_id=self.session_id,
                            tool_name=tool_name,
                            started_at=started_at,
                            duration_ms=int((time.perf_counter() - start) * 1000),
                            status="success",
                            arg_keys=arg_keys,
                        )
                    )
                    return result
                except Exception as exc:
                    self._append(
                        ToolRunRecord(
                            session_id=self.session_id,
                            tool_name=tool_name,
                            started_at=started_at,
                            duration_ms=int((time.perf_counter() - start) * 1000),
                            status="error",
                            arg_keys=arg_keys,
                            error=f"{type(exc).__name__}: {exc}",
                        )
                    )
                    raise

            async_wrapped.__signature__ = (
                resolved_sig  # keep original schema for FastMCP
            )
            async_wrapped.__annotations__ = resolved_annotations
            return async_wrapped

        @wraps(fn)
        def sync_wrapped(*args: Any, **kwargs: Any) -> Any:
            start = time.perf_counter()
            started_at = datetime.now(timezone.utc).isoformat()
            arg_keys = sorted(kwargs.keys())
            try:
                result = fn(*args, **kwargs)
                self._append(
                    ToolRunRecord(
                        session_id=self.session_id,
                        tool_name=tool_name,
                        started_at=started_at,
                        duration_ms=int((time.perf_counter() - start) * 1000),
                        status="success",
                        arg_keys=arg_keys,
                    )
                )
                return result
            except Exception as exc:
                self._append(
                    ToolRunRecord(
                        session_id=self.session_id,
                        tool_name=tool_name,
                        started_at=started_at,
                        duration_ms=int((time.perf_counter() - start) * 1000),
                        status="error",
                        arg_keys=arg_keys,
                        error=f"{type(exc).__name__}: {exc}",
                    )
                )
                raise

        sync_wrapped.__signature__ = resolved_sig  # keep original schema for FastMCP
        sync_wrapped.__annotations__ = resolved_annotations
        return sync_wrapped

    def list_runs(self, limit: int = 50) -> list[dict[str, Any]]:
        if limit <= 0:
            return []
        return [asdict(x) for x in list(self._events)[-limit:]]

    def clear(self) -> int:
        count = len(self._events)
        self._events.clear()
        return count

    def summary(self) -> dict[str, Any]:
        total = len(self._events)
        errors = sum(1 for x in self._events if x.status == "error")
        unique_tools = len({x.tool_name for x in self._events})
        return {
            "session_id": self.session_id,
            "total_runs": total,
            "error_runs": errors,
            "unique_tools": unique_tools,
        }

    def summary_text(self) -> str:
        s = self.summary()
        return (
            f"Session ID:  {s['session_id']}\n"
            f"Total runs:  {s['total_runs']}\n"
            f"Error runs:  {s['error_runs']}\n"
            f"Unique tools:{s['unique_tools']}"
        )

    def runs_text(self, limit: int = 50) -> str:
        rows = self.list_runs(limit=limit)
        if not rows:
            return "No tool runs recorded for this session yet."
        return json.dumps(rows, indent=2)
