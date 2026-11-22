"""Example 6 — the three injection styles, and when to use each.

Scenario
--------
One ``Clock`` dependency, injected three ways into three consumers:

1. **constructor** — ``def __init__(self, clock: Clock)``: explicit, the
default choice, and the only style a static type checker sees.
2. **class-body field** — ``clock: Clock``: less boilerplate for optional
collaborators. The annotation is the *type*, which is also the token, so the
field is collected without naming anything twice.
3. **field with an explicit token** — ``clock: Annotated[Clock, Inject("CLOCK")]``:
needed when the provider is registered under a non-class token.

``Depends[T]`` is a *runtime spelling* for ``T`` — it evaluates to ``T`` — so
``container.resolve(Depends[Clock])`` is ``container.resolve(Clock)``. It is not
a type form, so it belongs where it is evaluated, never in annotation position
(PRD 01 §5.5): mypy and pyright both reject ``x: Depends[T]``, and on a function
token the field gate would skip the annotation silently.

What to look for
----------------
All three consumers receive the *same* ``Clock``, because style is a
readability choice, not a lifetime one. The alias registered for ``"CLOCK"``
points at that same singleton rather than creating a second clock.

Run
---
    PYTHONPATH=. python examples/06_injection_styles.py
"""

from __future__ import annotations

import asyncio
from typing import Annotated

from lauren_di import Depends, DIContainer, Inject, injectable, use_existing


@injectable()
class Clock:
    """The one dependency every consumer below needs."""

    def label(self) -> str:
        return "system clock"


@injectable()
class ByConstructor:
    """Style 1: explicit parameter, visible to every type checker."""

    def __init__(self, clock: Clock) -> None:
        self.clock = clock


@injectable()
class ByField:
    """Style 2: class-body field; the annotation *is* the token."""

    clock: Clock


@injectable()
class ByExplicitToken:
    """Style 3: the annotation is the type, ``Inject`` is the lookup key."""

    clock: Annotated[Clock, Inject("CLOCK")]


async def main() -> None:
    container = DIContainer()
    container.register(Clock)
    # The string token resolves to the *same* singleton, not a second clock.
    container.register_custom(use_existing(provide="CLOCK", existing=Clock))
    container.register(ByConstructor)
    container.register(ByField)
    container.register(ByExplicitToken)

    by_constructor = await container.resolve(ByConstructor)
    by_field = await container.resolve(ByField)
    by_token = await container.resolve(ByExplicitToken)

    # `Depends[T] is T`, so the spelling is only ever an *expression* (§5.5).
    assert await container.resolve(Depends[Clock]) is await container.resolve(Clock)

    print("constructor:", by_constructor.clock.label())
    print("field:      ", by_field.clock.label())
    print("token:      ", by_token.clock.label())
    print("all one clock:", by_constructor.clock is by_field.clock is by_token.clock)


if __name__ == "__main__":
    asyncio.run(main())
