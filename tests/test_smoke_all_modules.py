import asyncio
import importlib
import inspect
import os
from pathlib import Path
import sys
import types
import asyncio
import pytest


class FakeMCP:
    def __init__(self):
        self.tools = {}

    def tool(self, **kwargs):
        def deco(fn):
            # store the inner function by its name
            self.tools[fn.__name__] = fn
            return fn

        return deco


def test_import_all_modules_and_invoke_registered_tools(monkeypatch):
    """Import all project modules and, for modules that register tools, call them with safe dummy args.

    This is a broad smoke test to ensure every file can be imported and its tool entrypoints
    can be invoked under stubbed external dependencies.
    """

    async def _main():
        root = Path(__file__).resolve().parents[1]

        # --- Patch global dependencies to safe no-op implementations ---
        # litellm stub
        fake_litellm = types.SimpleNamespace(
            completion=lambda **k: types.SimpleNamespace(
                choices=[
                    types.SimpleNamespace(message=types.SimpleNamespace(content="ok"))
                ]
            ),
            token_counter=lambda model, messages: 1,
            completion_cost=lambda completion_response: 0.0,
        )
        sys.modules.setdefault("litellm", fake_litellm)

        # shared.http_client stubs
        import shared.http_client as _hc

        async def _fake_rate_limit(*a, **k):
            return None

        async def _fake_get(*a, **k):
            return {}

        async def _fake_get_text(*a, **k):
            return ""

        async def _fake_get_text_with_url(*a, **k):
            return "", a[0] if a else ""

        async def _fake_post(*a, **k):
            return {}

        def _fake_head(*a, **k):
            return {}

        monkeypatch.setattr("shared.rate_limiter.rate_limit", _fake_rate_limit)
        monkeypatch.setattr(_hc, "get", _fake_get)
        monkeypatch.setattr(_hc, "get_text", _fake_get_text)
        monkeypatch.setattr(_hc, "get_text_with_url", _fake_get_text_with_url)
        monkeypatch.setattr(_hc, "post", _fake_post)
        monkeypatch.setattr(_hc, "head", _fake_head)

        # subprocess runner stubs
        import shared.subprocess_runner as _sr

        async def _fake_run(*a, **k):
            return types.SimpleNamespace(returncode=0, stdout="", stderr="")

        monkeypatch.setattr(_sr, "run", _fake_run)
        monkeypatch.setattr(_sr, "is_available", lambda t: False)

        # browser stubs
        import agent_runtime.browser as _br

        monkeypatch.setattr(_br, "session_ok", lambda: False)
        monkeypatch.setattr(_br, "fetch_page", lambda *a, **k: asyncio.sleep(0))
        monkeypatch.setattr(_br, "open_page", lambda *a, **k: asyncio.sleep(0))

        # shared.config keys: ensure missing keys are blank to avoid external API paths
        import shared.config as _cfg

        for attr in dir(_cfg):
            if attr.isupper() and attr.endswith("_KEY"):
                try:
                    setattr(_cfg, attr, "")
                except Exception:
                    pass

        # Walk repository files and import project .py modules only (skip tests, __pycache__, virtualenvs)
        def is_project_path(p: Path) -> bool:
            s = str(p)
            if "tests" in s or "__pycache__" in s:
                return False
            # avoid imported files from virtualenvs or system site-packages
            if (
                ".venv" in s
                or "venv" in s
                or "site-packages" in s
                or "dist-packages" in s
            ):
                return False
            return True

        py_files = [p for p in root.rglob("*.py") if is_project_path(p)]

        errors = []
        for p in py_files:
            # compute module name
            rel = p.relative_to(root)
            mod_name = str(rel.with_suffix("")).replace(os.path.sep, ".")
            try:
                mod = importlib.import_module(mod_name)
            except Exception as e:
                errors.append((mod_name, f"import failed: {e}"))
                continue

            # If module exposes register(), call it with a FakeMCP and exercise registered tools
            reg = getattr(mod, "register", None)
            if callable(reg):
                # only call register if it accepts exactly one parameter named 'mcp'
                try:
                    rsig = inspect.signature(reg)
                    params = list(rsig.parameters.keys())
                except Exception:
                    params = []
                if len(params) != 1 or params[0] != "mcp":
                    # skip non-conforming register() signatures
                    continue

                fake_mcp = FakeMCP()
                try:
                    reg(fake_mcp)
                except Exception as e:
                    errors.append((mod_name, f"register() failed: {e}"))
                    continue

                # invoke each registered tool function with simple args
                for name, fn in list(fake_mcp.tools.items()):
                    sig = inspect.signature(fn)
                    kwargs = {}
                    args = []
                    for i, (pname, param) in enumerate(sig.parameters.items()):
                        if param.kind in (
                            inspect.Parameter.VAR_POSITIONAL,
                            inspect.Parameter.VAR_KEYWORD,
                        ):
                            continue
                        if param.default is not inspect._empty:
                            val = param.default
                        else:
                            lname = pname.lower()
                            if "url" in lname or "link" in lname:
                                val = "https://example.com"
                            elif "domain" in lname:
                                val = "example.com"
                            elif "email" in lname:
                                val = "user@example.com"
                            elif "ip" in lname or "addr" in lname:
                                val = "1.2.3.4"
                            elif (
                                "limit" in lname
                                or "count" in lname
                                or "num" in lname
                                or "max" in lname
                            ):
                                val = 1
                            elif "bool" in str(param.annotation).lower() or isinstance(
                                param.default, bool
                            ):
                                val = False
                            else:
                                # default fallback string
                                val = "test"
                        # prefer to pass as positional
                        args.append(val)

                    try:
                        if inspect.iscoroutinefunction(fn):
                            await fn(*args)
                        else:
                            # may be async function disguised; call and if returns coroutine await
                            res = fn(*args)
                            if inspect.isawaitable(res):
                                await res
                    except Exception as e:
                        # record invocation error but continue
                        errors.append((f"{mod_name}.{name}", f"call failed: {e}"))

        # Fail the test if any imports or invocations raised
        if errors:
            msgs = "\n".join(f"{m}: {msg}" for m, msg in errors[:50])
            pytest.fail(f"Smoke import/invoke errors:\n{msgs}")

    asyncio.run(_main())

