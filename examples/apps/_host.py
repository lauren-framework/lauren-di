"""The reference host adapter: the twelve obligations, in code.

PRD 09 §5.1 states the contract every framework page maps onto its own hooks.
This module is the executable form of it — the "reference adapter" of §5.11 —
and it is deliberately framework-free: it imports ``lauren_di`` and the
standard library and nothing else (IC-12).

Every example app under ``examples/apps/<framework>/`` uses these three pieces:

* :func:`build_container` — IC-1 (one container per process, started before the
  host accepts work) and IC-2 (request-scope strictness is a decision, stated).
* :class:`HostUnit` — IC-3, IC-4, IC-7, IC-8: exactly one scope per unit of
  work, stored in the host's own state, closed once in a ``finally`` after the
  transport finished, with teardown errors reported rather than raised.
* :class:`SyncBridge` — IC-10: a sync host (WSGI, argparse, click) runs every
  container call on one long-lived loop instead of ``asyncio.run`` per request,
  so an ``asyncio.Lock`` created during ``startup()`` is still usable later.

What the adapter deliberately does not do
-----------------------------------------
It does not store the scope in a module global (D-58: the next unit would
overwrite it) and it does not resolve for you. A unit hands out the
:class:`~lauren_di.ResolutionScope`; the handler calls ``scope.resolve`` on it
(IC-5) and never ``container.resolve``, which would bypass the scope cache and
build a second instance.

Run
---
This module is imported, not executed. The contract it encodes is exercised by
``tests/integration/hosts/`` and by each app's own tests::

    nox -s examples                      # every app that can be installed here
    python -m pytest examples/apps/flask # one app
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import threading
from collections.abc import Coroutine, Mapping
from typing import Any, TypeVar

from lauren_di import DestructError, DIContainer, ResolutionScope

T = TypeVar("T")

LOGGER = logging.getLogger("lauren_di.examples")

#: The one key an adapter writes into the host's per-unit state (IC-4).
#:
#: A WSGI host puts it in ``environ``, an ASGI host in ``scope["state"]`` or
#: ``request.state``, a CLI host on its invocation object. One key, written
#: once per unit: a second write is either a nested scope (IC-3 violation) or a
#: bug, and a module-level global would be overwritten by the next request.
SCOPE_KEY = "lauren_di_scope"


def build_container(
    *,
    value_tokens: tuple[Any, ...] = (),
    overrides: Any = None,
    strict_request_scope: bool = True,
) -> DIContainer:
    """Build the process-wide container: IC-1, IC-2 and IC-6, in one place.

    ``strict_request_scope=True`` is the default because every web example has a
    ``REQUEST``-scoped provider, and the lax fallback would resolve one outside
    any scope, cache it, and never tear it down (M-11). A host with no
    ``REQUEST`` provider passes ``strict_request_scope=False`` and says so in its
    README; the flag is never silently flipped.

    ``value_tokens`` names the host-owned objects the host will supply through
    ``values=`` at unit entry (IC-6). Declaring them here is what lets
    ``compile()`` skip them; a ``SINGLETON`` that depends on one would capture
    the first request's object, so the examples never do that.

    ``overrides`` is applied while rows are registered (IC-11): a plan is a
    build-time decision, never a mutation of a live container.

    Args:
        value_tokens: Tokens the host supplies per unit, e.g. ``(REQUEST,)``.
        overrides: An ``OverridePlan`` for tests, or ``None``.
        strict_request_scope: Whether resolving a ``REQUEST`` token outside a
            scope is an error. Defaults to ``True``.

    Returns:
        An unstared ``DIContainer``. Callers register their providers and then
        ``await container.startup()`` before the host accepts work (IC-1).
    """
    return DIContainer(
        value_tokens=value_tokens,
        overrides=overrides,
        strict_request_scope=strict_request_scope,
    )


class HostUnit:
    """One unit of work: one scope, one close point, no ambient state.

    The host creates a unit after it has bound its own per-unit state, passes
    ``values=`` for every declared value token, and closes the unit at the
    transport's "finished" point:

    * ASGI pure middleware — after ``await app(scope, receive, send)`` returns,
      which is after the last body chunk (M-18);
    * WSGI — after the response iterable's ``close()``;
    * CLI — after the command body, before process exit.

    ``BaseHTTPMiddleware.dispatch``'s ``finally`` is *not* a close point: it runs
    after the first chunk, so a streaming body would be produced by services
    whose ``aclose()`` already ran. PRD 10 §5.4 measures that; the ASGI examples
    therefore install a pure middleware.

    Closing is idempotent (IC-7) and never raises: teardown failures land in
    :attr:`errors` and are logged through :meth:`report` (IC-8), because by then
    the response may already be committed.
    """

    __slots__ = ("_closed", "_container", "_report", "_scope", "_values")

    def __init__(
        self,
        container: DIContainer,
        *,
        values: Mapping[Any, Any] | None = None,
        report: Any = None,
    ) -> None:
        """Bind a unit to ``container``; nothing is opened until :meth:`open`.

        Args:
            container: The started, process-wide container (IC-1).
            values: Host-owned objects for this unit, keyed by their tokens
                (IC-6). Supplying one for an undeclared token raises
                ``InvalidProviderError`` — the host's bug, early and loud.
            report: Optional callable ``(errors) -> None`` used instead of the
                default log line, for hosts with a metrics channel.
        """
        self._container = container
        self._values = dict(values or {})
        self._report = report
        self._scope: ResolutionScope | None = None
        self._closed = False

    @property
    def scope(self) -> ResolutionScope:
        """The open scope. Raises before :meth:`open` and after :meth:`close`."""
        if self._scope is None:
            raise RuntimeError("the unit is not open: call `await unit.open()` first")
        if self._closed:
            raise RuntimeError("the unit is closed: resolving after the close point is a bug")
        return self._scope

    @property
    def closed(self) -> bool:
        """Whether :meth:`close` has run, so a host can assert one close (IC-7)."""
        return self._closed

    @property
    def errors(self) -> tuple[DestructError, ...]:
        """Teardown failures collected by :meth:`close` (IC-8). Empty until then."""
        return self._scope.errors if self._scope is not None else ()

    async def open(self) -> ResolutionScope:
        """Open the unit's scope and return it (IC-3).

        The scope is opened *after* the host bound its own state, so the
        ``values=`` this unit carries are visible to the providers that were
        awaiting construction. Opening one more scope inside the unit is a bug:
        :meth:`open` refuses a second call rather than silently nesting.
        """
        if self._scope is not None:
            raise RuntimeError(
                "this unit already opened a scope (IC-3): one unit of work, one scope"
            )
        self._scope = self._container.scope(values=self._values)
        return await self._scope.__aenter__()

    async def close(self) -> tuple[DestructError, ...]:
        """Close the scope exactly once and report what teardown raised (IC-7, IC-8).

        Returns:
            The teardown errors, in the order the container produced them. The
            same tuple on every call after the first, so a ``finally`` that runs
            twice is harmless.
        """
        if self._closed or self._scope is None:
            return self.errors
        self._closed = True
        errors = await self._scope.close()
        self.report(errors)
        return errors

    def report(self, errors: tuple[DestructError, ...] | None = None) -> None:
        """Send teardown failures to the host's channel instead of the response.

        IC-8: a ``@pre_destruct`` that raises cannot be reported as a 500 — the
        response may already be streaming. Dropping it is the measured bug (M-9);
        logging it is the minimum, and ``report=`` swaps in a metric or span.
        """
        found = self.errors if errors is None else errors
        if not found:
            return
        if self._report is not None:
            self._report(found)
            return
        for error in found:
            LOGGER.error(
                "teardown failed for %s (%s, %s): %s",
                getattr(error, "name", "<unknown>"),
                getattr(error, "origin", "<unknown>"),
                getattr(error, "stage", "<unknown>"),
                error,
            )

    async def __aenter__(self) -> ResolutionScope:
        """Open the unit, so ``async with`` is the whole adapter body."""
        return await self.open()

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
        """Close the unit in a ``finally`` — the host's exception still propagates."""
        await self.close()
        return False


class SyncBridge:
    """One long-lived loop for a sync host: IC-10, instead of ``asyncio.run`` per unit.

    A WSGI worker or a CLI command is synchronous, but ``lauren_di``'s lifecycle is
    ``async``. ``asyncio.run`` per request is tempting and wrong for anything
    loop-affine: a provider that stores an ``asyncio.Lock``, a pooled
    ``aiohttp`` session, or a task created in ``@post_construct`` would be bound
    to a loop that closed before the next unit began (M-4/M-5/M-6).

    This bridge runs one loop on a daemon thread for the life of the process and
    submits every coroutine to it. ``close()`` stops the loop after the last
    submitted work, which is the host's "process is stopping" moment.

    The bridge is not a scheduler: ``run`` blocks its caller, so concurrency is
    the host's business, not the adapter's.
    """

    __slots__ = ("_loop", "_ready", "_thread")

    def __init__(self) -> None:
        """Start the loop thread and wait until it is serving work."""
        self._loop = asyncio.new_event_loop()
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._serve, name="lauren-di-loop", daemon=True)
        self._thread.start()
        self._ready.wait()

    def _serve(self) -> None:
        """Run the loop forever on this thread until :meth:`close` stops it."""
        asyncio.set_event_loop(self._loop)
        self._loop.call_soon(self._ready.set)
        try:
            self._loop.run_forever()
        finally:
            self._loop.close()

    @property
    def loop(self) -> asyncio.AbstractEventLoop:
        """The loop every container call runs on."""
        return self._loop

    def run(self, awaitable: Coroutine[Any, Any, T]) -> T:
        """Run ``awaitable`` on the shared loop and return its result.

        Exceptions are re-raised in the calling thread, so a host sees the
        container's own exception type rather than a wrapper.
        """
        future = asyncio.run_coroutine_threadsafe(awaitable, self._loop)
        return future.result()

    def close(self) -> None:
        """Stop the loop and join the thread; safe to call more than once."""
        if self._loop.is_closed():
            return
        with contextlib.suppress(RuntimeError):
            self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=5)


def report_to(errors: tuple[DestructError, ...]) -> None:
    """A ``report=`` callback that writes one log line per teardown failure.

    Useful for hosts whose ``finally`` cannot log (a CLI that has already flushed
    stdout, a test that asserts on stderr); the default :meth:`HostUnit.report`
    does the same thing, this is the explicit spelling.
    """
    for error in errors:
        LOGGER.error("teardown failed: %s", error)
