"""The Pyramid adapter: one WSGI middleware, one close point.

Pyramid hands a view its request and then forgets about it. The one hook it does
offer fires *before* the body runs (PYR-2), so it cannot own a scope whose
teardown has to follow the last chunk. The close point is therefore our own WSGI
wrapper: the middleware opens the scope once ``environ`` exists, writes it to
``environ[SCOPE_KEY]``, and closes it from the response iterable's ``close()``
(PYR-3, PYR-4) — the same shape Falcon and Flask use (PRD 12 §5.3).

Two further facts from PRD 12 §5.6 decide the body of this module:

* PYR-1 — an in-process WSGI client runs the view on ``MainThread``, so the
  container's asynchronous work has to leave the caller's thread. That is what
  ``_host.SyncBridge`` is for (IC-10): one long-lived loop, never
  ``asyncio.run`` per request.
* PYR-5 — ``Configurator`` has no lifecycle hook, so ``container.startup()``
  belongs to the entry point that builds the app, not to a Pyramid callback.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator, Mapping
from functools import partial
from typing import Any

import _host
from lauren_di import DestructError, DIContainer, ResolutionScope

#: Builds the host values for one unit of work, keyed by their tokens (IC-6).
ValuesFactory = Callable[[Mapping[str, Any]], Mapping[Any, Any]]

#: Observes the end of one unit: the unit itself, and the teardown errors (IC-8).
CloseHook = Callable[["_host.HostUnit", tuple[DestructError, ...]], None]


def no_values(_environ: Mapping[str, Any]) -> Mapping[Any, Any]:
    """Supply no host values — the default for an app with no host-owned token."""
    return {}


def resolve(
    bridge: _host.SyncBridge,
    scope: ResolutionScope,
    token: Any,
) -> Any:
    """Resolve ``token`` in ``scope``, on the bridge loop rather than the caller.

    ``ResolutionScope.resolve`` is a coroutine, and a Pyramid view is a plain
    function, so the view cannot await it. The hop is not a workaround: it is what
    keeps an async provider — and any loop-bound singleton it builds — on the one
    loop the process keeps open (IC-10, T12-13).

    Args:
        bridge: The unit's bridge, the only thread allowed to touch the container.
        scope: This request's scope, taken from ``environ[_host.SCOPE_KEY]``.
        token: A class or ``Token`` the scope knows how to resolve.

    Returns:
        The resolved instance.
    """
    return bridge.run(scope.resolve(token))


class _ClosingIterable:
    """Wrap a response iterable so ``close()`` runs after the last chunk.

    A WSGI server calls ``close()`` when it is done with the response: after the
    final chunk, or earlier if the client disconnects. That is the only point in
    a WSGI host where "the body is over" is guaranteed (PYR-3), and it is why the
    unit is closed here rather than in a view or a Pyramid event.
    """

    __slots__ = ("_close", "_closed", "_iterable", "_iterator")

    def __init__(
        self,
        iterable: Iterable[bytes],
        *,
        close: Callable[[], None],
    ) -> None:
        """Keep the response body and the callback that ends the unit.

        Args:
            iterable: Whatever the wrapped app returned as its WSGI body.
            close: Called once, after the body's own ``close()`` has run.
        """
        self._iterable = iterable
        self._iterator: Iterator[bytes] | None = None
        self._close = close
        self._closed = False

    def __iter__(self) -> Iterator[bytes]:
        """Iterate the wrapped body lazily, one chunk at a time."""
        if self._iterator is None:
            self._iterator = iter(self._iterable)
        return self._iterator

    def __next__(self) -> bytes:
        """Return the next chunk, so ``next(response)`` works like ``for``."""
        return next(iter(self))

    def close(self) -> None:
        """Close the body first and the unit second, exactly once (IC-7)."""
        if self._closed:
            return
        self._closed = True
        try:
            inner = getattr(self._iterable, "close", None)
            if inner is not None:
                inner()
        finally:
            self._close()


class DIMiddleware:
    """WSGI middleware: one scope per request, closed on the response iterable.

    It wraps the Pyramid application, so it sees every request — including the
    ones Pyramid answers with an exception view (T12-11) — and every response,
    including a lazily produced one (T12-05).
    """

    __slots__ = ("_app", "_bridge", "_container", "_on_close", "_values")

    def __init__(
        self,
        app: Callable[[dict[str, Any], Callable[..., Any]], Iterable[bytes]],
        *,
        container: DIContainer,
        bridge: _host.SyncBridge,
        values: ValuesFactory = no_values,
        on_close: CloseHook | None = None,
    ) -> None:
        """Bind the wrapper to a started container and its bridge (IC-1, IC-10).

        Args:
            app: The WSGI application to wrap, e.g. ``config.make_wsgi_app()``.
            container: The started, process-wide container.
            bridge: The one loop every container call runs on.
            values: Builds this unit's host values from ``environ`` (IC-6).
            on_close: Called once after the unit closed, so a test — or a metric
                or a span — can observe the close point without reaching into
                the unit's internals.
        """
        self._app = app
        self._container = container
        self._bridge = bridge
        self._values = values
        self._on_close = on_close

    def __call__(
        self,
        environ: dict[str, Any],
        start_response: Callable[..., Any],
    ) -> Iterable[bytes]:
        """Serve one request: open the scope, delegate, wrap the body.

        Args:
            environ: The WSGI environment, which is also this host's request store.
            start_response: The WSGI callback, passed straight through.

        Returns:
            The response body, whose ``close()`` ends the unit.
        """
        unit = _host.HostUnit(self._container, values=self._values(environ))
        environ[_host.SCOPE_KEY] = self._bridge.run(unit.open())
        try:
            iterable = self._app(environ, start_response)
        except BaseException:
            # The app failed before it produced a body, so nothing will ever close
            # the iterable: the close point has to be this except branch (IC-7).
            self._finish(unit)
            raise
        return _ClosingIterable(iterable, close=partial(self._finish, unit))

    def _finish(self, unit: _host.HostUnit) -> None:
        """Close ``unit`` and report the teardown errors to ``on_close``.

        ``HostUnit.close`` is idempotent, so a server that closes twice (or a
        test that closes an already-closed body) still tears down once (IC-7).
        """
        errors = self._bridge.run(unit.close())
        if self._on_close is not None:
            self._on_close(unit, errors)
