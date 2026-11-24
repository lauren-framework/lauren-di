"""The Starlette adapter: one lifespan, one pure-ASGI middleware, one scope per request.

PRD 10 §5.1 lists the only three hooks Starlette needs, and this module is nothing
but those three:

* :func:`di_lifespan` — maps Starlette's lifespan onto ``container.startup()`` and
  ``container.shutdown()``, so eager construction happens before the first
  connection and teardown happens once, at the end (IC-9);
* :class:`DIScopeMiddleware` — owns one :class:`_host.HostUnit` per connection and
  closes it in a ``finally`` (IC-3, IC-7, IC-8);
* :func:`scope_of` — the single call a handler makes to reach its scope (IC-4,
  IC-5).

The middleware is pure ASGI on purpose. ``BaseHTTPMiddleware.dispatch``'s
``finally`` runs as soon as the response *object* exists, which for a streaming
body is before the first chunk is sent: PRD 10 §5.4 measures a ``closed=True``
scope on chunks 1-3. ``tests/test_starlette.py`` contains both that negative
control (T10-07) and the positive measurement (T10-06).
"""

from __future__ import annotations

import contextlib
import uuid
from collections.abc import Callable
from typing import Any

import lauren_di as di
from _host import SCOPE_KEY, HostUnit, report_to
from starlette.types import ASGIApp, Receive, Scope, Send

#: The host-owned object this app declares in ``value_tokens=`` (IC-6).
#:
#: A request id is created by the transport, not by the container, so it is a
#: *value*: supplying it through ``values=`` keeps the graph honest and lets any
#: request-scoped provider depend on the id of the unit it was built for.
REQUEST_ID = di.Token("starlette-request-id")

#: ``report=`` callable shape: the teardown failures, in container order (IC-8).
Report = Callable[[tuple[di.DestructError, ...]], None]


def request_id_for(scope: Scope) -> str:
    """Return the id the host already knows for this connection.

    An inbound ``x-request-id`` is honoured so the app joins the caller's trace;
    otherwise the transport's own id is minted here. Either way the value is
    host-owned: no provider may construct it.

    Args:
        scope: The raw ASGI scope the middleware received.

    Returns:
        A non-empty request id.
    """
    for name, value in scope.get("headers") or ():
        if name.lower() == b"x-request-id":
            return value.decode("latin-1")
    return uuid.uuid4().hex


class DIScopeMiddleware:
    """Open one unit per connection, put its scope in ``scope["state"]``, close once.

    Starlette binds ``scope["state"]`` before middleware runs, so writing the scope
    there is exactly IC-4: the handler reads it from the host's per-unit state and
    never from a module global (D-58).

    HTTP and WebSocket scopes are owned here; ``lifespan`` and anything else pass
    straight through, because those are not units of work.
    """

    def __init__(
        self,
        app: ASGIApp,
        *,
        container: di.DIContainer,
        report: Report | None = None,
    ) -> None:
        """Wrap ``app`` with one unit per connection.

        Args:
            app: The Starlette application (or any ASGI app) to wrap.
            container: The started, process-wide container (IC-1).
            report: Where teardown failures go instead of the response (IC-8).
                Defaults to :func:`_host.report_to`, one log line per failure.
        """
        self.app = app
        self.container = container
        self.report = report

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Serve one connection inside one scope.

        Args:
            scope: The ASGI scope; may be reused by the server for a later
                connection, hence the per-call unit.
            receive: The ASGI receive channel.
            send: The ASGI send channel.
        """
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        state: dict[str, Any] = scope.setdefault("state", {})
        if SCOPE_KEY in state:
            # IC-3: one unit, one scope. A second owner means two middlewares both
            # think they own the request; failing loudly beats a silent double close.
            raise RuntimeError(
                f"{SCOPE_KEY!r} is already bound for this connection: "
                "only one middleware may own the scope (IC-3)"
            )

        unit = HostUnit(
            self.container,
            values={REQUEST_ID: request_id_for(scope)},
            report=self.report,
        )
        try:
            state[SCOPE_KEY] = await unit.open()
            await self.app(scope, receive, send)
        finally:
            # The close point: after the last body chunk, after background tasks,
            # after the handler's own exceptions were handled (IC-7, IC-8).
            await unit.close()


def di_lifespan(container: di.DIContainer, *, report: Report | None = None) -> Any:
    """Build the ``lifespan=`` context manager Starlette runs once per process.

    Args:
        container: The container this app owns (IC-1). It is *not* started yet.
        report: Where the ``shutdown()`` tuple is reported (IC-9). Defaults to
            :func:`_host.report_to`.

    Returns:
        An async context manager factory with Starlette's ``lifespan`` signature.
    """

    @contextlib.asynccontextmanager
    async def lifespan(app: Any) -> Any:
        """Start the container before the first request, shut it down once."""
        del app  # The container is the app's, not the lifespan's, to know.
        await container.startup()
        try:
            yield {}
        finally:
            errors = await container.shutdown()
            if errors:
                (report or report_to)(errors)

    return lifespan


def wire(
    app: Any,
    container: di.DIContainer,
    *,
    report: Report | None = None,
    middleware: Any = DIScopeMiddleware,
) -> None:
    """Attach the container to ``app.state`` and install the middleware.

    The lifespan cannot be installed here \u2014 Starlette resolves it at construction
    time \u2014 so :func:`di_lifespan` is passed to ``Starlette(lifespan=...)`` and this
    function adds the other two hooks.

    Args:
        app: The Starlette application under construction.
        container: The container built for this app (IC-1).
        report: Where teardown failures go (IC-8).
        middleware: The scope owner to install. It must accept ``container=`` and
            ``report=``; the tests substitute a ``BaseHTTPMiddleware`` owner to
            measure the difference between the two close points (T10-07).
    """
    app.state.di = container
    app.add_middleware(middleware, container=container, report=report)


def scope_of(connection: Any) -> di.ResolutionScope:
    """Return the current unit's scope from a ``Request`` or ``WebSocket``.

    Handlers resolve through this, never through ``container.resolve``, so the
    scope cache is the only cache (IC-5).

    Args:
        connection: A Starlette ``Request`` or ``WebSocket``.

    Returns:
        The :class:`lauren_di.ResolutionScope` the middleware opened.

    Raises:
        RuntimeError: If the connection has no scope, which means the middleware
            was not installed and every downstream resolve would be wrong.
    """
    try:
        return getattr(connection.state, SCOPE_KEY)
    except AttributeError as exc:  # pragma: no cover - a wiring bug, not a request
        raise RuntimeError(
            f"no {SCOPE_KEY!r} on this connection: install DIScopeMiddleware first"
        ) from exc
