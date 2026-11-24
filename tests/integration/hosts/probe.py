"""The one probe every conformance adapter serves (PRD 09 §5.10).

The twelve invariants are observations about *a host adapter*, not about
``lauren_di``: "was the container started before the first unit", "did two units
share a request-scoped instance", "did the scope close after the last chunk".
So the suite needs a unit of work whose behaviour is identical everywhere and
whose evidence is a list of events — this module is that unit.

``EVENTS`` is a module-level list on purpose: providers are constructed by the
container, which cannot be handed a per-unit recorder, and a singleton's
``@post_construct`` runs at ``startup()`` before any unit exists. Drivers reset
it before they start, so one adapter's observations never leak into the next.
"""

from __future__ import annotations

import asyncio
from typing import Any

from lauren_di import (
    DIContainer,
    OverridePlan,
    ProviderOverride,
    Scope,
    injectable,
    post_construct,
    pre_destruct,
)

#: Everything a probe observed, in order: the evidence the invariants read.
EVENTS: list[str] = []


def reset() -> None:
    """Forget every observation: one adapter per reset, never shared."""
    EVENTS.clear()


def record(event: str) -> None:
    """Append one observation to the trace."""
    EVENTS.append(event)


def _running_loop() -> Any:
    """The loop this provider was built on — what C-10 compares across units."""
    try:
        return asyncio.get_running_loop()
    except RuntimeError:  # pragma: no cover - a host that builds off-loop
        return None


@injectable()
class Config:
    """A singleton: C-1 reads its startup hook, C-9 its teardown hook."""

    def __init__(self) -> None:
        """Capture the loop the container was started on."""
        self.loop = _running_loop()

    @post_construct
    def started(self) -> None:
        """Record that ``startup()`` constructed this singleton."""
        record("config.started")

    @pre_destruct
    def stopped(self) -> None:
        """Record that ``shutdown()`` tore it down — once, if C-9 holds."""
        record("config.stopped")


@injectable(scope=Scope.REQUEST)
class Scoped:
    """Request-scoped: C-2 (no scope, no instance), C-3, C-5."""

    def __init__(self) -> None:
        """Record one construction, with the loop it happened on."""
        self.loop = _running_loop()
        record("scoped.new")


@injectable(scope=Scope.REQUEST)
class Closing:
    """Request-scoped, and its destructor is the close-point marker (C-7)."""

    @pre_destruct
    def closed(self) -> None:
        """Run when the *scope* closes, not when the handler returns."""
        record("scoped.closed")


@injectable(scope=Scope.REQUEST)
class Fragile:
    """Request-scoped with a destructor that fails (C-8)."""

    @pre_destruct
    def boom(self) -> None:
        """Record the attempt, then fail: the host must report this, not raise it."""
        record("fragile.closed")
        raise RuntimeError("conformance teardown exploded")


class Frozen:
    """The double an ``OverridePlan`` installs instead of :class:`Config` (C-11)."""

    marker = "frozen"


def override_plan() -> OverridePlan:
    """The plan C-11 builds a second container with."""
    return OverridePlan({Config: ProviderOverride(Config, "value", Frozen())})


def build(*, with_override: bool = False) -> DIContainer:
    """Build the probe container: strict request scope, four providers.

    Args:
        with_override: Whether to build with :func:`override_plan` applied, which
            is how C-11 observes that overrides are a *build-time* decision.

    Returns:
        An unstarted container. The driver starts it (IC-1).
    """
    container = DIContainer(
        overrides=override_plan() if with_override else None,
        strict_request_scope=True,
    )
    for provider in (Config, Scoped, Closing, Fragile):
        container.register(provider)
    return container


def serve(resolve: Any, *, fragile: bool = False) -> dict[str, Any]:
    """Serve one unit, synchronously: two resolves, a body, maybe a failure.

    Args:
        resolve: ``token -> instance``, already bound to this unit's scope. A sync
            host binds its bridge; an async host binds an awaitable version.
        fragile: Whether to also resolve :class:`Fragile`, so teardown fails.

    Returns:
        The two ``Scoped`` instances, which C-5 requires to be one object.
    """
    record("unit")
    first = resolve(Scoped)
    second = resolve(Scoped)
    record("body")
    resolve(Closing)
    if fragile:
        resolve(Fragile)
    return {"first": first, "second": second}


async def aserve(resolve: Any, *, fragile: bool = False) -> dict[str, Any]:
    """The awaitable twin of :func:`serve`, for an ASGI-shaped host."""
    record("unit")
    first = await resolve(Scoped)
    second = await resolve(Scoped)
    record("body")
    await resolve(Closing)
    if fragile:
        await resolve(Fragile)
    return {"first": first, "second": second}
