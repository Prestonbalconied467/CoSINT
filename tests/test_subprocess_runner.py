import asyncio
import shutil
import pytest


def test_is_available_checks_path(monkeypatch):
    import shared.subprocess_runner as sr

    monkeypatch.setattr(
        shutil, "which", lambda name: "/usr/bin/fake" if name == "fake" else None
    )
    assert sr.is_available("fake")
    assert not sr.is_available("missing")


def test_run_success_and_stdout_return(monkeypatch):
    import shared.subprocess_runner as sr

    # Ensure tool path resolves
    monkeypatch.setattr(sr, "_resolve_tool_path", lambda t: "/usr/bin/fake")

    class Proc:
        def __init__(self):
            self.returncode = 0

        async def communicate(self, input=None):
            return (b"hello\n", b"")

    async def fake_create(*args, **kwargs):
        return Proc()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create)

    res = asyncio.run(sr.run("fake", "--version"))
    assert res.returncode == 0
    assert res.stdout.strip() == "hello"
    assert res.ok


def test_run_raises_tool_not_found(monkeypatch):
    import shared.subprocess_runner as sr

    monkeypatch.setattr(sr, "_resolve_tool_path", lambda t: None)
    with pytest.raises(sr.ToolNotFoundError):
        asyncio.run(sr.run("nope"))


def test_run_raises_subprocess_error_on_nonzero_no_output(monkeypatch):
    import shared.subprocess_runner as sr

    monkeypatch.setattr(sr, "_resolve_tool_path", lambda t: "/usr/bin/fake")

    class Proc:
        def __init__(self):
            self.returncode = 2

        async def communicate(self, input=None):
            return (b"", b"")

    async def fake_create(*args, **kwargs):
        return Proc()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create)

    with pytest.raises(sr.SubprocessError):
        asyncio.run(sr.run("fake"))

