"""The reference host adapter: a unit of work with no framework anywhere.

Scenario
--------
``lauren_di`` is a container, not a runtime: something has to start it, hand out
one ``ResolutionScope`` per request/job/command, and close that scope at the
transport's finished point. PRD 09 §5.1 calls that something a *host adapter*
and states twelve obligations for it (IC-1 … IC-12); PRD 09 §5.11 prints the
whole adapter in twenty lines.

This file is that adapter, executable. It builds one container, starts it,
serves three units of work — one of them a streaming body — and stops it. There
is no web framework, no CLI library and no third-party import: the *host* is the
``main()`` coroutine below, and the adapter is :class:`HostAdapter` plus
:class:`Unit`.

What to look for
----------------
* ``started`` is printed once and *before* the first unit: ``startup()`` ran
  before anything resolved (IC-1).
* Inside one unit, two resolves of the same request-scoped ``Repo`` return the
  same object; a second unit gets a different one, because the scope owns the
  cache (IC-3, IC-5).
* The unit supplies the host's own request object as a *value*; the container
  never constructs it and never closes it (IC-6).
* ``connection closed`` is printed *after* the last body chunk of the streaming
  unit, not when its ``yield`` returns: the scope closes at the close point
  (IC-7).
* ``shutdown()`` is called once and its tuple is read — teardown failures are
  reported, never raised (IC-8, IC-9).

Run
---
    PYTHONPATH=. python examples/host_unit_of_work.py
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Annotated, Any

from lauren_di import (
    DIContainer,
    Inject,
    ResolutionScope,
    Scope,
    Token,
    injectable,
    use_factory,
)

#: The host's own object for this unit — a request, a job, a command.
REQUEST = Token("host.request")

#: What the adapter observed, so ``main()`` can print it: order matters here.
EVENTS: list[str] = []


class Connection:
    """A host-owned resource whose lifetime *is* the unit of work."""

    def __init__(self, name: str) -> None:
        """Remember which unit opened it."""
        self.name = name
        self.closed = False

    async def aclose(self) -> None:
        """Close the connection and say so, in order."""
        self.closed = True
        EVENTS.append(f"connection.closed:{self.name}")


async def open_connection() -> AsyncIterator[Connection]:
    """A generator provider: ``aclose()`` runs when the *scope* closes."""
    connection = Connection(name="unit")
    try:
        yield connection
    finally:
        await connection.aclose()


@injectable(scope=Scope.REQUEST)
class Repo:
    """Request-scoped, so one instance per unit and one only."""

    def __init__(self, request: Annotated[Any, Inject(REQUEST)]) -> None:
        """Capture the host object this unit was opened with."""
        self.request = request
        EVENTS.append("repo.new")


class HostAdapter:
    """One container per process; one scope per unit of work.

    The two methods a host must call are :meth:`start` before it accepts work
    and :meth:`stop` when it stops accepting it. Everything else — the scope,
    its close point, where its teardown failures go — belongs to :class:`Unit`,
    so no ambient state is involved (IC-3, IC-4).
    """

    def __init__(self, container: DIContainer) -> None:
        """Bind the adapter to a built, not-yet-started container."""
        self.container = container
        self.errors: list[Any] = []

    async def start(self) -> None:
        """Start the container: singletons and their ``@post_construct`` hooks."""
        EVENTS.append("container.started")
        await self.container.startup()

    async def stop(self) -> tuple[Any, ...]:
        """Stop it once and hand the teardown failures back to the host."""
        errors = await self.container.shutdown()
        EVENTS.append("container.stopped")
        return errors

    def unit(self, values: dict[Any, Any] | None = None) -> Unit:
        """Open a unit; ``values`` are the host's own objects, keyed by token."""
        return Unit(self, values or {})

    def report(self, errors: tuple[Any, ...]) -> None:
        """Send teardown failures to the host's channel instead of the body."""
        self.errors.extend(errors)
        for error in errors:
            EVENTS.append(f"teardown.reported:{error.message}")


class Unit:
    """One request, job, command or connection. Single use."""

    def __init__(self, adapter: HostAdapter, values: dict[Any, Any]) -> None:
        """Hold the adapter and this unit's host values; nothing is open yet."""
        self._adapter = adapter
        self._values = values
        self.scope: ResolutionScope | None = None

    async def __aenter__(self) -> ResolutionScope:
        """Open exactly one scope, after the host bound its own state."""
        self.scope = self._adapter.container.scope(values=self._values)
        return await self.scope.__aenter__()

    async def __aexit__(self, *exc_info: Any) -> bool:
        """Close the scope once and report what teardown raised.

        Returns ``False`` on purpose: a teardown failure must never replace the
        handler's own exception (IC-8).
        """
        assert self.scope is not None, "unit entered without a scope"
        self._adapter.report(await self.scope.close())
        return False


def build(container: DIContainer | None = None) -> HostAdapter:
    """Build the adapter: one container, one singleton, one request-scoped pair.

    Args:
        container: An already-built container, for a host that has its own
            registration step; ``None`` builds and registers here.

    Returns:
        The adapter, not yet started — the host calls :meth:`HostAdapter.start`
        before it accepts units (IC-1).
    """
    if container is None:
        container = DIContainer(value_tokens=(REQUEST,), strict_request_scope=True)
        container.register(Repo)
        container.register_custom(
            use_factory(provide=Connection, factory=open_connection, scope=Scope.REQUEST)
        )
    return HostAdapter(container)


async def main() -> None:
    """Serve three units of work and print what the adapter observed."""
    adapter = build()
    await adapter.start()
    print("after start:   ", list(EVENTS))

    first_request = {"path": "/first"}
    async with adapter.unit({REQUEST: first_request}) as scope:
        repo = await scope.resolve(Repo)
        again = await scope.resolve(Repo)
        connection = await scope.resolve(Connection)
        print("one scope, one instance:", repo is again)
        print("the host's own object:  ", repo.request is first_request)
    print("after unit 1:  ", list(EVENTS))
    print("teardown ran last:      ", connection.closed)

    second_request = {"path": "/second"}
    async with adapter.unit({REQUEST: second_request}) as scope:
        other = await scope.resolve(Repo)
        streaming = await scope.resolve(Connection)
        print("a second unit is fresh: ", other is not repo)
        for chunk in (b"one ", b"two ", b"three"):
            print("chunk:                  ", chunk.decode().strip())
        print("still open while bodying:", not streaming.closed)
    print("closed after the body:  ", streaming.closed)

    print("shutdown failures:      ", await adapter.stop())
    print("reported teardown:      ", adapter.errors)


if __name__ == "__main__":
    asyncio.run(main())
