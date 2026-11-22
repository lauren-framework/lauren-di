"""Example 4 — bind a Protocol to one of several implementations.

Scenario
--------
``Notifier`` is a ``runtime_checkable`` Protocol with two implementations,
``EmailNotifier`` and ``SmsNotifier``. Consumers depend on the Protocol, never
on a concrete class, and the composition root chooses the implementation with
``use_class``.

What to look for
----------------
The same ``Alert`` class is resolved against two containers and reports a
different transport in each. Swapping the notifier is one line at the
composition root, not an edit in every consumer — which is the reason to
depend on the Protocol at all.

Run
---
    PYTHONPATH=. python examples/04_protocol_binding.py
"""

from __future__ import annotations

import asyncio
from typing import Protocol, runtime_checkable

from lauren_di import DIContainer, injectable, use_class


@runtime_checkable
class Notifier(Protocol):
    """The consumer-facing contract; no implementation detail leaks through."""

    def send(self, message: str) -> str: ...


class EmailNotifier:
    """A concrete implementation. It needs no decorator of its own."""

    def send(self, message: str) -> str:
        return f"email: {message}"


class SmsNotifier:
    """The alternative implementation, chosen by the composition root."""

    def send(self, message: str) -> str:
        return f"sms: {message}"


@injectable()
class Alert:
    """Depends on the Protocol, so it cannot tell which transport it got."""

    def __init__(self, notifier: Notifier) -> None:
        self.notifier = notifier

    def raise_(self, text: str) -> str:
        return self.notifier.send(text)


def build(implementation: type) -> DIContainer:
    """The composition root: the only place that names a concrete class."""
    container = DIContainer()
    container.register_custom(use_class(provide=Notifier, use=implementation))
    container.register(Alert)
    return container


async def main() -> None:
    for implementation in (EmailNotifier, SmsNotifier):
        alert = await build(implementation).resolve(Alert)
        print(f"{implementation.__name__:14} -> {alert.raise_('disk full')}")


if __name__ == "__main__":
    asyncio.run(main())
