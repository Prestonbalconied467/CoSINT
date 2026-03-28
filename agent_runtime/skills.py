from __future__ import annotations

from pathlib import Path

SKILLS_DIR = Path(__file__).resolve().parents[1] / "skills"

# Ordered search paths for load_skill — most specific first.
_SEARCH_DIRS = [
    SKILLS_DIR / "core",
    SKILLS_DIR / "depth",
    SKILLS_DIR / "subagents",
    SKILLS_DIR,  # fallback: root (for README etc.)
]


def load_skill(name: str) -> str | None:
    """Search all skill subdirectories for <name>.md and return its content."""
    for directory in _SEARCH_DIRS:
        candidate = directory / f"{name}.md"
        if candidate.exists():
            return candidate.read_text(encoding="utf-8")
    return None


__all__ = ["load_skill"]
