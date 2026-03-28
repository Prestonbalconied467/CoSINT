"""
tools/social/__init__.py

Re-exports the top-level router so callers can simply do:
    from social import route
"""

from ._router import route

__all__ = ["route"]
