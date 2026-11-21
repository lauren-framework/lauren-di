"""Shared helpers for the ``lauren_di`` test suite.

The suite deliberately runs coroutines through :func:`run` rather than relying on
``pytest-asyncio``: one fewer plugin, one fewer version matrix, and every test
still exercises the real event-loop behaviour of ``resolve`` / ``scope`` /
``shutdown``.
"""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from typing import Any


def run(coro: Coroutine[Any, Any, Any]) -> Any:
    """Drive one coroutine to completion on a private event loop."""
    return asyncio.run(coro)
