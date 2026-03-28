from __future__ import annotations

import sys


def _supports_color() -> bool:
    return hasattr(sys.stdout, "isatty") and sys.stdout.isatty()


_COLOR = _supports_color()


def _c(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m" if _COLOR else text


def dim(t: str) -> str:
    return _c("2", t)


def bold(t: str) -> str:
    return _c("1", t)


def cyan(t: str) -> str:
    return _c("36", t)


def green(t: str) -> str:
    return _c("32", t)


def yellow(t: str) -> str:
    return _c("33", t)


def red(t: str) -> str:
    return _c("31", t)


def blue(t: str) -> str:
    return _c("34", t)


def magenta(t: str) -> str:
    return _c("35", t)


def white(t: str) -> str:
    return _c("97", t)


__all__ = ["dim", "bold", "cyan", "green", "yellow", "red", "blue", "magenta", "white"]
