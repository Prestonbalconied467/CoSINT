"""
shared/subprocess_runner.py

Async wrapper for external CLI tools (Maigret, Holehe, ExifTool, truffleHog, etc.)
All subprocess calls go through run() – never use asyncio.create_subprocess_exec directly.
"""

import asyncio
import json
import os
import shutil
from dataclasses import dataclass
from shared.config import SUBPROCESS_DEFAULT_TIMEOUT


@dataclass
class SubprocessResult:
    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0


class ToolNotFoundError(Exception):
    """Raised when the CLI tool is not found in PATH."""

    pass


class SubprocessError(Exception):
    """Raised when the CLI tool exits with a non-zero code."""

    def __init__(self, message: str, stderr: str = ""):
        super().__init__(message)
        self.stderr = stderr


_TOOL_PATH_CACHE: dict[str, str] = {}


def _resolve_tool_path(tool: str) -> str | None:
    """Resolve and cache executable paths for installed tools."""
    cached = _TOOL_PATH_CACHE.get(tool)
    if cached:
        return cached
    resolved = shutil.which(tool)
    if resolved:
        _TOOL_PATH_CACHE[tool] = resolved
    return resolved


async def run(
    *args: str,
    timeout: float = SUBPROCESS_DEFAULT_TIMEOUT,
    run_input: str | None = None,
) -> SubprocessResult:
    """Execute an external CLI tool asynchronously.

    Args:
        *args:   Command and arguments, e.g. ("maigret", "--json", "username")
        timeout: Maximum runtime in seconds (default: 120s)
        run_input:   Optional stdin input

    Returns:
        SubprocessResult with returncode, stdout, stderr

    Raises:
        ToolNotFoundError:    Tool not found in PATH
        SubprocessError:      Process exits with error and no output
        asyncio.TimeoutError: Timeout exceeded
    """
    tool = args[0]
    tool_path = _resolve_tool_path(tool)
    if not tool_path:
        raise ToolNotFoundError(
            f"'{tool}' not found. Install it: pip install {tool} "
            f"or via your package manager."
        )

    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    env["TERM"] = "dumb"
    env["TQDM_DISABLE"] = "1"  # Suppress tqdm progress bars
    env["NO_COLOR"] = "1"  # Disables ANSI color output (e.g. GHunt)
    env["PYTHONIOENCODING"] = "utf-8"  # Prevents Windows cp1252 decoding errors

    proc = await asyncio.create_subprocess_exec(
        tool_path,
        *args[1:],
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,  # Merge stderr into stdout
        stdin=asyncio.subprocess.PIPE if run_input else asyncio.subprocess.DEVNULL,
        env=env,
    )

    try:
        stdin_bytes = run_input.encode() if run_input else None
        stdout_bytes, _ = await asyncio.wait_for(
            proc.communicate(input=stdin_bytes),
            timeout=timeout,
        )
        stderr_bytes = b""  # stderr was merged into stdout
    except asyncio.TimeoutError:
        proc.kill()
        await proc.communicate()
        raise asyncio.TimeoutError(
            f"'{tool}' timed out after {timeout}s. Process terminated."
        )

    result = SubprocessResult(
        returncode=proc.returncode or 0,
        stdout=stdout_bytes.decode(errors="replace").strip(),
        stderr=stderr_bytes.decode(errors="replace").strip(),
    )

    # Only raise if there is no useful output
    if result.returncode != 0 and not result.stdout:
        raise SubprocessError(
            f"'{tool}' exited with code {result.returncode}.",
            stderr=result.stderr,
        )

    return result


async def run_json(
    *args: str,
    timeout: float = SUBPROCESS_DEFAULT_TIMEOUT,
) -> dict | list:
    """Like run(), but expects JSON output from the tool.

    Raises:
        SubprocessError: If output is not valid JSON
    """
    result = await run(*args, timeout=timeout)
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as e:
        raise SubprocessError(
            f"'{args[0]}' did not produce valid JSON: {e}",
            stderr=result.stderr,
        )


def is_available(tool: str) -> bool:
    """Check if a CLI tool is available in PATH."""
    return _resolve_tool_path(tool) is not None
