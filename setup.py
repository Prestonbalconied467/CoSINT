"""
setup.py – CoSINT AI Setup Wizard

Interactive wizard for configuring API keys, installing dependencies,
and checking external CLI tools.

Run via:  python setup.py
"""

import argparse
import getpass
import os
import shutil
import subprocess
import sys
from pathlib import Path

from dotenv import dotenv_values

from shared.setup_data import (
    EXTERNAL_TOOL_CHECKS,
    LITELLM_DOCS_URL,
    LITELLM_PROVIDER_PRESETS,
    OPTIONAL_PY_PACKAGES,
    RUNTIME_VARS,
    TOOLS,
)


# ── .env helpers ──────────────────────────────────────────────────────────────


def load_env(path: Path) -> dict:
    if not path.exists():
        return {}
    return dict(dotenv_values(path))


def write_env(path: Path, values: dict) -> None:
    lines = [f"{key}={value or ''}\n" for key, value in values.items()]
    path.write_text("".join(lines), encoding="utf-8")


# ── Secret input helpers ──────────────────────────────────────────────────────

# Fields that are not secrets and can be shown/entered in plaintext
_PLAINTEXT_VARS = {"REDDIT_USER_AGENT"}


def _is_secret(var: str) -> bool:
    return var not in _PLAINTEXT_VARS


def _mask(value: str) -> str:
    """Return a masked version of a secret value for confirmation display."""
    if len(value) <= 4:
        return "*" * len(value)
    return value[:4] + "*" * (len(value) - 4)


def _prompt_secret(prompt: str) -> str:
    """Prompt for a secret value using getpass (hidden input)."""
    try:
        return getpass.getpass(prompt)
    except getpass.GetPassWarning:
        # Fallback if getpass can't hide input (e.g. some CI environments)
        return input(prompt)


def _read_value(var: str, prompt: str) -> str:
    """Read a value from the user, hiding input for secret fields."""
    if _is_secret(var):
        value = _prompt_secret(prompt)
        if value:
            print(f"  Set: {_mask(value)}")
        return value
    return input(prompt)


# ── Section: API keys ─────────────────────────────────────────────────────────


def _configure_api_keys(env_values: dict) -> bool:
    updated = False
    for tool in TOOLS:
        name = tool["name"]
        var = tool["env"]
        url = tool.get("url", "")
        has_free = tool.get("free")
        free_label = (
            "free tier available"
            if has_free is True
            else "no free tier"
            if has_free is False
            else None
        )
        label_suffix = f" - {free_label}" if free_label else ""

        current = env_values.get(var)
        if current:
            print(f"{name} [{var}]{label_suffix} - already set.")
            if url:
                print(f"  Docs / key signup: {url}")
            choice = (
                input(
                    "  Keep value? (Enter = keep, c = change, d = delete, s = skip): "
                )
                .strip()
                .lower()
            )
            if choice == "c":
                new_val = _read_value(
                    var, f"  Enter new value for {var} (or leave empty to cancel): "
                )
                if new_val:
                    env_values[var] = new_val
                    updated = True
            elif choice == "d":
                env_values[var] = ""
                updated = True
        else:
            print(f"{name} [{var}]{label_suffix}")
            if url:
                print(f"  Get key: {url}")
            ans = _read_value(var, "  Enter value (or press Enter to skip): ")
            if ans:
                env_values[var] = ans
                updated = True
        print()
    return updated


# ── Section: Runtime vars ─────────────────────────────────────────────────────


def _configure_runtime_vars(env_values: dict) -> bool:
    updated = False
    print("Runtime / model variables:")
    for item in RUNTIME_VARS:
        var = item["env"]
        default = item.get("default", "")
        hint = item.get("hint", "")
        current = env_values.get(var)

        if current:
            print(f"{item['name']} [{var}] - already set.")
            if hint:
                print(f"  {hint}")
            choice = (
                input("  Keep value? (Enter = keep, c = change, d = delete): ")
                .strip()
                .lower()
            )
            if choice == "c":
                new_val = input(
                    f"  Enter new value for {var} (or leave empty to cancel): "
                )
                if new_val:
                    env_values[var] = new_val
                    updated = True
            elif choice == "d":
                env_values[var] = ""
                updated = True
        else:
            print(f"{item['name']} [{var}]")
            if hint:
                print(f"  {hint}")
            prompt = f"  Enter value (or press Enter to skip{f', default: {default}' if default else ''}): "
            ans = input(prompt)
            if ans:
                env_values[var] = ans
                updated = True
            elif default:
                env_values[var] = default
                updated = True
        print()
    return updated


# ── Section: LiteLLM provider ─────────────────────────────────────────────────


def _configure_litellm_provider(env_values: dict) -> bool:
    updated = False
    print("LiteLLM provider setup:")
    print(f"  Docs: {LITELLM_DOCS_URL}")
    print(
        "  Pick provider preset: [1] openai  [2] anthropic  [3] gemini  [4] azure  [5] other  [Enter] skip"
    )
    provider_choice = input("  Choice: ").strip().lower()

    provider_map = {
        "1": "openai",
        "2": "anthropic",
        "3": "gemini",
        "4": "azure",
        "5": "other",
    }
    provider = provider_map.get(provider_choice, provider_choice)

    provider_vars: list[str] = []
    if provider in LITELLM_PROVIDER_PRESETS:
        provider_vars = LITELLM_PROVIDER_PRESETS[provider]
    elif provider == "other":
        raw = input(
            "  Enter env var names for your provider (comma-separated, e.g. OPENROUTER_API_KEY,OPENROUTER_API_BASE):: "
        ).strip()
        if raw:
            provider_vars = [x.strip().upper() for x in raw.split(",") if x.strip()]
    elif provider:
        print("  Unknown choice; skipping provider-specific setup.")

    for var in provider_vars:
        current = env_values.get(var)
        if current:
            print(f"{var} - already set.")
            choice = (
                input("  Keep value? (Enter = keep, c = change, d = delete): ")
                .strip()
                .lower()
            )
            if choice == "c":
                new_val = _read_value(
                    var, f"  Enter new value for {var} (or leave empty to cancel): "
                )
                if new_val:
                    env_values[var] = new_val
                    updated = True
            elif choice == "d":
                env_values[var] = ""
                updated = True
        else:
            ans = _read_value(
                var, f"  Enter value for {var} (or press Enter to skip): "
            )
            if ans:
                env_values[var] = ans
                updated = True
        print()
    return updated


# ── Section: Optional Python packages ────────────────────────────────────────


def _install_optional_packages() -> None:
    print("\nOptional Python OSINT tool packages:")
    for item in OPTIONAL_PY_PACKAGES:
        pkg = item["package"]
        label = f"{item['name']} ({pkg})"
        if _is_python_package_installed(pkg):
            print(f"  - {label}: already installed")
            continue
        print(f"\n{label}")
        print(f"  Why: {item['why']}")
        print(f"  Necessity: {item['required']}")
        choice = input("  Install now? (y/N): ").strip().lower()
        if choice == "y":
            ok = _install_python_package(pkg)
            print(f"  {'Installed' if ok else 'Failed to install'} {pkg}.")
        else:
            print(f"  Skipped {pkg}.")


# ── Section: External CLI tools ───────────────────────────────────────────────


def _check_external_tools() -> None:
    print("\nExternal CLI tool checks:")
    for tool in EXTERNAL_TOOL_CHECKS:
        name = tool["name"]
        if _is_tool_available(name):
            print(f"  - {name}: found")
        else:
            print(f"  - {name}: missing")
            print(f"    Why: {tool['why']}")
            print(f"    Install hint: {_external_install_hint(tool)}")


# ── Package / tool detection ───────────────────────────────────────────────────


def _is_python_package_installed(package_name: str) -> bool:
    result = subprocess.run(
        [sys.executable, "-m", "pip", "show", package_name],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return result.returncode == 0


def _install_python_package(package_name: str) -> bool:
    cmd = [sys.executable, "-m", "pip", "install", package_name]
    print(f"Running: {' '.join(cmd)}")
    return subprocess.call(cmd) == 0


def _is_tool_available(tool_name: str) -> bool:
    return shutil.which(tool_name) is not None


def _external_install_hint(tool: dict) -> str:
    return tool["windows"] if os.name == "nt" else tool["unix"]


# ── Main wizard ───────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(description="CoSINT AI Setup Wizard")
    parser.add_argument(
        "--skip-keys",
        action="store_true",
        help="Skip the API key configuration section.",
    )
    args = parser.parse_args()

    project_root = Path(__file__).resolve().parent
    env_example = project_root / ".env.example"
    env_file = project_root / ".env"

    first_run = not env_file.exists()

    print("OSINT AI - Setup Wizard")
    print("========================\n")

    if first_run:
        print("First run detected - showing free-tier info and setup hints.\n")

    if not env_file.exists():
        if env_example.exists():
            shutil.copy(env_example, env_file)
            print("Created .env from .env.example\n")
        else:
            env_file.touch()
            print("Created empty .env\n")

    env_values = load_env(env_file)
    updated = False

    if not args.skip_keys:
        updated |= _configure_api_keys(env_values)
    else:
        print("Skipping API key configuration (--skip-keys).\n")
    updated |= _configure_runtime_vars(env_values)
    updated |= _configure_litellm_provider(env_values)

    if updated:
        write_env(env_file, env_values)
        print(f"Saved updated environment to {env_file}")
    else:
        print("No changes made to .env")

    if _is_python_package_installed("playwright"):
        choice = (
            input(
                "\nInstall Playwright Chromium browser now? (recommended for web search) (y/N): "
            )
            .strip()
            .lower()
        )
        if choice == "y":
            cmd = [sys.executable, "-m", "playwright", "install", "chromium"]
            print(f"Running: {' '.join(cmd)}\n")
            subprocess.call(cmd)

    _install_optional_packages()
    _check_external_tools()

    print("\nSetup finished.")


if __name__ == "__main__":
    main()
