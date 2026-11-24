"""The FastAPI adapter: the same three hooks, on a FastAPI app.

FastAPI is a Starlette app with a router, so the ASGI contract is unchanged and
this module is deliberately the same shape as ``starlette_di``: one lifespan, one
pure-ASGI middleware, one helper for handlers. It is duplicated rather than
shared because the only framework-neutral code lives in ``_host.py``; an example
that imported another example's adapter would stop showing *this* host's page.

The FastAPI-specific seam is documented where it bites: ``Depends`` and
``Inject`` belong to different containers of meaning (PRD 10 §5.8). A route
receives services through ``Annotated[T, Depends(...)]``, and the container's own
graph uses ``Annotated[T, Inject(TOKEN)]`` inside the provider's constructor. A
handler-level ``Inject`` default reaches the handler as an ``_InjectMarker``,
never as a service — measured in ``test_fastapi.py`` as T10-13.
"""

from __future__ import annotations

import contextlib
import uuid
from collections.abc import Callable
from typing import Any

import lauren_di as di
from _host import SCOPE_KEY, HostUnit, report_to
from starlette.requests import Request
from starlette.types import ASGIApp, Receive, Scope, Send

#: The host-owned object this app declares in ``value_tokens=`` (IC-6).
REQUEST_ID = di.Token("fastapi-request-id")

#: ``report=`` callable shape: the teardown failures, in container order (IC-8).
Report = Callable[[tuple[di.DestructError, ...]], None]


def request_id_for(scope: Scope) -> str:
    """Return the id the host already knows for this connection.

    Args:
        scope: The raw ASGI scope the middleware received.

    Returns:
        The inbound ``x-request-id`` when the caller sent one, else a fresh id.
    """
    for name, value in scope.get("headers") or ():
        if name.lower() == b"x-request-id":
            return value.decode("latin-1")
    return uuid.uuid4().hex


class DIScopeMiddleware:
    """Open one unit per connection, put its scope in ``scope["state"]``, close once.

    Pure ASGI, exactly as in the Starlette example, because the reason is the
    framework's, not the style's: ``BaseHTTPMiddleware.dispatch``'s ``finally``
    runs when the response object exists, which is before a streaming body is
    finished (PRD 10 §5.4). FastAPI builds on Starlette, so the same rule and the
    same measurement apply (T10-06, T10-07).
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
            app: The FastAPI application (or any ASGI app) to wrap.
            container: The started, process-wide container (IC-1).
            report: Where teardown failures go instead of the response (IC-8).
        """
        self.app = app
        self.container = container
        self.report = report

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Serve one connection inside one scope.

        Args:
            scope: The ASGI scope.
            receive: The ASGI receive channel.
            send: The ASGI send channel.
        """
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        state: dict[str, Any] = scope.setdefault("state", {})
        if SCOPE_KEY in state:
            # IC-3: one unit, one scope. Two owners would mean two closes.
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
            # The close point: after the last body chunk and after the handler
            # returned its response object (IC-7, IC-8).
            await unit.close()


def di_lifespan(container: di.DIContainer, *, report: Report | None = None) -> Any:
    """Build the ``lifespan=`` context manager FastAPI runs once per process.

    Args:
        container: The container this app owns (IC-1).
        report: Where the ``shutdown()`` tuple is reported (IC-9).

    Returns:
        An async context manager factory with FastAPI's ``lifespan`` signature.
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

    ``app.state.di`` is where a FastAPI app keeps the process-wide container
    (PRD 10 §5.8): dependencies receive it through ``request.app.state.di``,
    never through a module global.

    Args:
        app: The FastAPI application under construction.
        container: The container built for this app (IC-1).
        report: Where teardown failures go (IC-8).
        middleware: The scope owner to install; tests substitute a
            ``BaseHTTPMiddleware`` owner to measure the difference (T10-07).
    """
    app.state.di = container
    app.add_middleware(middleware, container=container, report=report)


def scope_of(connection: Any) -> di.ResolutionScope:
    """Return the current unit's scope from a ``Request`` or ``WebSocket``.

    Handlers and ``Depends`` providers resolve through this, never through
    ``container.resolve``, so the scope cache is the only cache (IC-5).

    Args:
        connection: A Starlette ``Request`` or ``WebSocket``.

    Returns:
        The :class:`lauren_di.ResolutionScope` the middleware opened.

    Raises:
        RuntimeError: If the connection has no scope, which means the middleware
            was not installed.
    """
    try:
        return getattr(connection.state, SCOPE_KEY)
    except AttributeError as exc:  # pragma: no cover - a wiring bug, not a request
        raise RuntimeError(
            f"no {SCOPE_KEY!r} on this connection: install DIScopeMiddleware first"
        ) from exc


def dependency(token: Any) -> Any:
    """Build a FastAPI dependency that resolves ``token`` from the request scope.

    This is the one bridge between the two injection systems: FastAPI calls the
    provider, the provider awaits ``scope.resolve``, and the container sees
    exactly one resolution per request (IC-5). Keep a module-level reference to
    the result, because ``app.dependency_overrides`` is keyed by this callable.

    Args:
        token: The class or ``Token`` to resolve.

    Returns:
        An ``async`` dependency callable with FastAPI's ``Depends`` signature.
    """

    async def resolve(request: Request) -> Any:
        """Resolve one token from this request's scope.

        FastAPI only injects the request when the annotation *is* ``Request``; an
        ``Any`` here would silently become a required query parameter.

        Args:
            request: The request FastAPI passes in.

        Returns:
            The resolved instance.
        """
        return await scope_of(request).resolve(token)

    return resolve
