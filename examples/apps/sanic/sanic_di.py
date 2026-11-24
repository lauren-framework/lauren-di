"""Sanic's close point is the server seam, not the app (PRD 11 §5.4).

Sanic builds the ``Request`` and writes the response inside its HTTP protocol, so
every hook the *application* offers runs before the body bytes go out::

    request middleware -> handler -> response middleware -> http.lifecycle.response
    -> body chunks -> eof

Measured on sanic 25.12.1 while building this example (the numbers are in
``README.md``): a ``http.lifecycle.response`` handler that sleeps 0.4s delays the
client's first body byte to 0.476s, so even the signal fires before the body. Closing
the unit in the response middleware would therefore dispose request-scoped services
while the response is still being written (SNC-2). The one hook that runs *after* the
last body message is the ASGI callable the server invokes, so that callable is where
this adapter puts the close.

This module imports nothing from sanic (D-57/D-62): the adapter is a plain ASGI
wrapper plus one middleware registration, and ``sanic`` stays a host dependency.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from _host import HostUnit
from lauren_di import Token

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable

    from lauren_di import DestructError, DIContainer, ResolutionScope
    from sanic import Sanic

LOGGER = logging.getLogger("examples.sanic")

SANIC_SCOPE_TOKEN = Token("sanic.asgi.scope")
"""The example's one value token: the ASGI scope the server handed to the app."""

_UNIT: ContextVar[HostUnit | None] = ContextVar("lauren_di_sanic_unit", default=None)
"""How the wrapper's unit reaches the request without a module global.

The wrapper sets this *before* it awaits the Sanic app, so the middleware and every
handler read it from the same task. ``request.ctx.di`` stays the authoritative host
store (IC-4); this variable is only the wrapper's short walk to the request object,
which the wrapper cannot see because Sanic builds it further down.
"""


def current_scope(request: Any) -> ResolutionScope:
    """Return the resolution scope published on ``request`` by :func:`install`."""
    scope = getattr(request.ctx, "di", None)
    if scope is None:
        raise RuntimeError(
            "no lauren_di scope on this request: serve the app through "
            "`sanic_di.DIASGIApp`, which is the only seam that both opens and closes "
            "a unit at the right points (sanic's own `app.run()` never calls it)"
        )
    return scope


def install(app: Sanic, container: DIContainer) -> None:
    """Publish the wrapper's unit on ``request.ctx`` and own the container lifecycle.

    The listeners are the Sanic form of "started before the host serves" (IC-1):
    ``before_server_start`` runs after the port is bound and before the first request,
    and ``after_server_stop`` runs once, after the last one (IC-2).

    Deliberately absent: a response middleware. It looks like the natural close point
    and is not one — it runs before the body is written (SNC-2).
    """

    @app.before_server_start
    async def _start_container(_app: Sanic) -> None:
        await container.startup()

    @app.after_server_stop
    async def _stop_container(_app: Sanic) -> None:
        await container.shutdown()

    @app.on_request
    async def _publish_scope(request: Any) -> None:
        unit = _UNIT.get()
        if unit is not None:
            request.ctx.di = unit.scope


@dataclass
class Conversation:
    """What one ASGI conversation produced, in order."""

    status: int
    body: bytes
    messages: list[dict[str, Any]] = field(default_factory=list)
    at_body_end: Any = None
    """Snapshot taken the instant the final body message crossed the seam."""


class DIASGIApp:
    """The ASGI callable a server runs *instead of* the bare Sanic app.

    One unit per HTTP request: opened before the app sees the request, closed exactly
    once after the last body message, whoever produced that body.
    """

    def __init__(
        self,
        app: Sanic,
        container: DIContainer,
        *,
        report: Callable[[tuple[DestructError, ...]], None] | None = None,
    ) -> None:
        """Wrap ``app`` so every request becomes one unit of work."""
        self.app = app
        self.container = container
        self.report = report
        self.events: list[tuple[str, str]] = []
        """``("open" | "close", path)`` in the order the seam saw them."""
        self.reported: list[DestructError] = []
        """Every teardown failure, in arrival order: reported, never raised (IC-8)."""

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        """One ASGI conversation: open, delegate, close, report."""
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path = scope.get("path", "")
        unit = HostUnit(self.container, values={SANIC_SCOPE_TOKEN: scope}, report=self._collect)
        await unit.open()
        self.events.append(("open", path))
        token = _UNIT.set(unit)
        try:
            await self.app(scope, receive, send)
        finally:
            _UNIT.reset(token)
            await unit.close()
            self.events.append(("close", path))

    def _collect(self, errors: tuple[DestructError, ...] | None = None) -> None:
        """Send teardown failures to the host's channel instead of the response."""
        self.reported.extend(errors or ())
        if self.report is not None and errors:
            self.report(errors)
        for error in errors or ():
            LOGGER.warning("request teardown failed: %r", error)


async def asgi_conversation(
    asgi: Any,
    path: str,
    *,
    method: str = "GET",
    snapshot: Callable[[], Any] | None = None,
) -> Conversation:
    """Drive one HTTP exchange through an ASGI callable and record what it sent.

    This is what an ASGI server does and nothing more, which makes it a measurement
    tool: ``snapshot`` is called exactly when the final body message is handed to the
    transport, so a test can ask "was the unit still open at the finished point?".
    """
    messages: list[dict[str, Any]] = []
    at_body_end: Any = None
    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "root_path": "",
        "headers": [(b"host", b"testserver")],
        "client": ("127.0.0.1", 54321),
        "server": ("testserver", 80),
    }
    seen = False

    async def receive() -> dict[str, Any]:
        nonlocal seen
        if seen:
            return {"type": "http.disconnect"}
        seen = True
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict[str, Any]) -> None:
        nonlocal at_body_end
        messages.append(message)
        final = message["type"] == "http.response.body" and not message.get("more_body", False)
        if final and snapshot is not None and at_body_end is None:
            at_body_end = snapshot()

    await asgi(scope, receive, send)
    status = next((int(m["status"]) for m in messages if m["type"] == "http.response.start"), 0)
    body = b"".join(m.get("body", b"") for m in messages if m["type"] == "http.response.body")
    return Conversation(status=status, body=body, messages=messages, at_body_end=at_body_end)


@asynccontextmanager
async def asgi_lifespan(asgi: Any) -> AsyncIterator[None]:
    """Run the server's lifespan handshake around the ``with`` block.

    Sanic refuses to serve before it has seen a lifespan startup (its loop is set up
    there), and its ``before_server_start`` listeners — where the container starts —
    only run inside this handshake. Anything that drives the ASGI callable by hand has
    to speak lifespan first, exactly like a server does.
    """
    inbox: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
    started = asyncio.Event()
    scope = {"type": "lifespan", "asgi": {"version": "3.0", "spec_version": "2.3"}, "state": {}}

    async def receive() -> dict[str, Any]:
        return await inbox.get()

    async def send(message: dict[str, Any]) -> None:
        if message["type"].startswith("lifespan.startup."):
            started.set()

    task = asyncio.ensure_future(asgi(scope, receive, send))
    await inbox.put({"type": "lifespan.startup"})
    await started.wait()
    try:
        yield
    finally:
        await inbox.put({"type": "lifespan.shutdown"})
        await task
