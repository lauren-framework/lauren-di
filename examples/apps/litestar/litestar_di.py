"""Litestar's close point: the ASGI seam, in ``ASGIMiddleware`` (PRD 11 §5.2).

Litestar is the one host in this page that hands the adapter a raw ASGI triple
*inside* the application object, so the adapter is the closest of the four to the
PRD 09 reference adapter: ``handle(scope, receive, send, next_app)`` opens a unit,
delegates, and closes in ``finally`` — after the last ``http.response.body``,
which is the finished point the contract asks for (IC-7, LIT-2).

Two details are load-bearing, and both were measured on litestar 2.25.0 while
building this example:

* **Do not override ``scopes``.** The base class defaults to HTTP, WebSocket and
  ASGI. A subclass that sets ``scopes = ()`` is silently built as a bypass and
  ``handle`` never runs.
* **The instance is built once and shared by every request.** Litestar takes the
  middleware object itself, so all per-request state lives in ``handle``'s locals.
  The mutable lists on this class are diagnostics, not request state.

The second documented owner is ``after_response`` (:func:`open_in_before_request`
plus :func:`close_in_after_response`), which is correct because Litestar runs it
*after* the streamed body and after the response's background task (LIT-3, T11-11).
It is the fallback for an app that cannot add ASGI middleware.

One deviation from the page, measured rather than assumed: PRD 11 §5.2 writes
``after_response=[close_scope]``. In litestar 2.25.0 those two app-level hooks take
a *single callable*, not a list — the list form fails every request with
``TypeError: 'list' object is not callable``. :func:`hooks_only` therefore passes
the callable directly, and ``tests/test_litestar.py`` pins the behaviour so the
page can be corrected against a running interpreter.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from _host import HostUnit, report_to
from lauren_di import Token
from litestar.middleware import ASGIMiddleware

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from lauren_di import DestructError, DIContainer, ResolutionScope
    from litestar import Request
    from litestar.types import ASGIApp, Receive, Scope, Send

LOGGER = logging.getLogger("examples.litestar")

LITESTAR_SCOPE_TOKEN = Token("litestar.asgi.scope")
"""The example's one value token: the ASGI scope the adapter was handed (IC-6)."""

SCOPE_KEY = "di"
"""Where the scope lands in ``scope["state"]``, i.e. ``request.state[SCOPE_KEY]``."""

UNIT_KEY = "lauren_di_unit"
"""Where the hooks-only install parks its *unit* (the middleware one parks nothing)."""


def current_scope(request: Request) -> ResolutionScope:
    """Return the resolution scope the adapter published on this request.

    Args:
        request: The Litestar request, whose ``state`` is the ASGI ``scope["state"]``
            dictionary the middleware wrote into (LIT-4).

    Returns:
        The live :class:`lauren_di.ResolutionScope` for this request.

    Raises:
        RuntimeError: If no scope is published, which means the app was built
            without :func:`install` — a wiring bug, reported as one.
    """
    scope = request.scope.get("state", {}).get(SCOPE_KEY)
    if scope is None:
        raise RuntimeError(
            "no lauren_di scope on this request: build the app through "
            "`litestar_di.install`, which is what adds the ASGI middleware"
        )
    return scope


@dataclass
class Close:
    """One close the adapter performed, so a test can read the order back."""

    path: str
    body_messages: int
    """How many ``http.response.body`` messages had crossed the seam at close time."""

    errors: tuple[DestructError, ...] = field(default_factory=tuple)


class DIMiddleware(ASGIMiddleware):
    """Own one unit per HTTP request in Litestar's ASGI seam (LIT-2).

    ``ASGIMiddleware`` is the only Litestar hook that runs *after* the body, so it
    is the adapter's primary install. The class deliberately keeps its state in
    ``handle``'s locals: Litestar builds one instance and reuses it for every
    request, so an attribute written per request would be shared state (D-58).
    """

    def __init__(
        self,
        container: DIContainer,
        *,
        report: Callable[[tuple[DestructError, ...]], None] | None = None,
    ) -> None:
        """Bind the app's container to the middleware instance.

        Args:
            container: The started, process-wide container (IC-1).
            report: Where teardown failures go instead of the response (IC-8).
        """
        self.container = container
        self.report = report
        self.closes: list[Close] = []
        """Every close, in order: the evidence the tests read back."""

    async def handle(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
        next_app: ASGIApp,
    ) -> None:
        """Open a unit, delegate, and close after the last body message.

        Args:
            scope: The raw ASGI scope.
            receive: The ASGI receive channel.
            send: The ASGI send channel.
            next_app: The Litestar application, already built.
        """
        if scope["type"] != "http":
            # WebSocket and ASGI-lifespan scopes are not units of work here; the
            # example's routes are all HTTP, and owning a lifespan scope would
            # open a unit before the container has started.
            await next_app(scope, receive, send)
            return

        state: dict[str, Any] = scope.setdefault("state", {})
        if SCOPE_KEY in state:
            raise RuntimeError(
                f"{SCOPE_KEY!r} is already bound for this connection: "
                "only one middleware may own the scope (IC-3)"
            )

        container = self.container
        unit = HostUnit(
            container,
            values={LITESTAR_SCOPE_TOKEN: scope},
            report=self.report,
        )
        body_messages = 0

        async def counting_send(message: Mapping[str, Any]) -> None:
            """Count body messages, then hand the message to the transport."""
            nonlocal body_messages
            if message["type"] == "http.response.body":
                body_messages += 1
            await send(message)

        state[SCOPE_KEY] = await unit.open()
        try:
            await next_app(scope, receive, counting_send)
        finally:
            # The close point: after the last body message, after the response's
            # background task, after Litestar unwound its own hooks (LIT-2).
            errors = await unit.close()
            self.closes.append(
                Close(path=scope.get("path", ""), body_messages=body_messages, errors=errors)
            )


def lifespan_hooks(
    container: DIContainer,
    *,
    report: Callable[[tuple[DestructError, ...]], None] | None = None,
) -> dict[str, list[Any]]:
    """Build ``on_startup``/``on_shutdown`` for ``Litestar(**hooks)`` (LIT-1).

    ``on_startup`` runs once, before the socket accepts work, which is where
    ``container.startup()`` belongs (IC-1, D-60). ``on_shutdown`` runs after the
    last request and reports the tuple ``shutdown()`` returns (IC-9).

    Args:
        container: The container this app owns.
        report: Where ``shutdown()``'s teardown errors go.

    Returns:
        A keyword-argument dict for the ``Litestar`` constructor.
    """

    async def _start() -> None:
        """Start the container before the host accepts work."""
        await container.startup()

    async def _stop() -> None:
        """Stop the container once, reporting what teardown raised."""
        errors = await container.shutdown()
        if errors:
            (report or report_to)(errors)

    return {"on_startup": [_start], "on_shutdown": [_stop]}


def open_in_before_request(
    container: DIContainer,
    *,
    report: Callable[[tuple[DestructError, ...]], None] | None = None,
) -> Callable[[Request], Any]:
    """Build the *opening* half of the hooks-only install (LIT-3).

    Args:
        container: The started container (IC-1).
        report: Where teardown failures go (IC-8).

    Returns:
        An async hook with Litestar's ``before_request`` signature.
    """

    async def open_scope(request: Request) -> None:
        """Open this request's unit and publish it where the handler can read it."""
        unit = HostUnit(
            container,
            values={LITESTAR_SCOPE_TOKEN: request.scope},
            report=report,
        )
        request.scope.setdefault("state", {})[SCOPE_KEY] = await unit.open()
        request.scope["state"][UNIT_KEY] = unit

    return open_scope


def close_in_after_response(
    *,
    report: Callable[[tuple[DestructError, ...]], None] | None = None,
) -> Callable[[Request], Any]:
    """Build the *closing* half of the hooks-only install (LIT-3, T11-11).

    ``after_response`` is a valid second owner because Litestar runs it after the
    streamed body and after the response's ``BackgroundTask`` — measured order on
    2.25.0: ``chunk0``, ``chunk1``, ``chunk2``, ``background``, ``after_response``.

    Measured correction to PRD 11 §5.2: in litestar 2.25.0 the app-level
    ``after_response`` hook is called with the **request**, not the response (the
    page's snippet annotates it as a response and then reads ``response.request``,
    which fails with ``AttributeError: 'Request' object has no attribute
    'request'``). This hook therefore takes the request and reads the state map
    the wrapper wrote.

    Args:
        report: Where teardown failures go (IC-8).

    Returns:
        An async hook with Litestar's app-level ``after_response`` signature.
    """

    async def close_scope(request: Request) -> None:
        """Close the unit the ``before_request`` hook opened for this request."""
        state = request.scope.get("state", {})
        unit = state.pop(UNIT_KEY, None)
        state.pop(SCOPE_KEY, None)
        if unit is None:
            raise RuntimeError(
                "no open unit for this response: the hooks-only install needs "
                "`litestar_di.open_in_before_request` to have run first"
            )
        errors = await unit.close()
        if errors:
            (report or report_to)(errors)

    return close_scope


def install(
    container: DIContainer,
    *,
    report: Callable[[tuple[DestructError, ...]], None] | None = None,
) -> tuple[dict[str, list[Any]], DIMiddleware]:
    """Return the ``Litestar(**kwargs)`` pieces for the middleware install (LIT-2).

    Args:
        container: The container this app owns (IC-1).
        report: Where teardown failures go (IC-8).

    Returns:
        The lifecycle hooks and the middleware instance to pass as
        ``middleware=[instance]``. The instance is returned too because it holds
        the close log the tests assert on.
    """
    return lifespan_hooks(container, report=report), DIMiddleware(container, report=report)


def hooks_only(container: DIContainer, *, report: Any = None) -> dict[str, Any]:
    """Return the ``Litestar(**kwargs)`` pieces for the hooks-only install (LIT-3).

    The values are single callables, not lists: litestar 2.25.0 rejects a list
    with ``TypeError: 'list' object is not callable`` on every request, which is
    the one place this example deliberately differs from PRD 11 §5.2's snippet.

    Args:
        container: The container this app owns (IC-1).
        report: Where teardown failures go (IC-8).

    Returns:
        Keyword arguments carrying the lifecycle hooks and both request hooks.
    """
    return {
        **lifespan_hooks(container, report=report),
        "before_request": open_in_before_request(container, report=report),
        "after_response": close_in_after_response(report=report),
    }
