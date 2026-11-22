"""Example 5 — a plugin registry assembled from many providers of one token.

Scenario
--------
Three plugins implement the same Protocol and each registers with
``multi=True``. ``Registry`` asks for ``list[Plugin]`` and receives every
multi-bound provider for that token, in registration order. The composition
root never maintains a hand-written list, so adding a plugin is one
registration and no other edit.

What to look for
----------------
``Registry.plugins`` holds all three, in the order they were registered. The
``list[Plugin]`` spelling is what makes it a collection request; asking for
the bare ``Plugin`` token would be an ambiguity error instead.

Run
---
    PYTHONPATH=. python examples/05_multi_binding_plugin_registry.py
"""

from __future__ import annotations

import asyncio
from typing import Protocol, runtime_checkable

from lauren_di import DIContainer, injectable


@runtime_checkable
class Plugin(Protocol):
    """Every plugin contributes a name and a transform."""

    name: str

    def apply(self, text: str) -> str: ...


@injectable(multi=True, provides=[Plugin])
class UpperCase:
    name = "upper"

    def apply(self, text: str) -> str:
        return text.upper()


@injectable(multi=True, provides=[Plugin])
class Reverse:
    name = "reverse"

    def apply(self, text: str) -> str:
        return text[::-1]


@injectable(multi=True, provides=[Plugin])
class Exclaim:
    name = "exclaim"

    def apply(self, text: str) -> str:
        return text + "!"


@injectable()
class Registry:
    """``list[Plugin]`` collects every ``multi=True`` provider for the token."""

    def __init__(self, plugins: list[Plugin]) -> None:
        self.plugins = plugins

    def run(self, text: str) -> str:
        for plugin in self.plugins:
            text = plugin.apply(text)
        return text


async def main() -> None:
    container = DIContainer()
    container.register(UpperCase)
    container.register(Reverse)
    container.register(Exclaim)
    container.register(Registry)

    registry = await container.resolve(Registry)
    print("plugins:", [plugin.name for plugin in registry.plugins])
    print("pipeline:", registry.run("lauren"))


if __name__ == "__main__":
    asyncio.run(main())
