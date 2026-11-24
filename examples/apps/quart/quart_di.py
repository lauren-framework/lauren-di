"""Quart's close point: wrap ``app.asgi_app``, never ``teardown_request`` (PRD 11 §5.3).

Quart's own request teardown is the trap this page exists to name. Measured on
quart 0.23.1 while building this example, a three-chunk streamed body produces::

    g.di -> teardown_request -> http.response.start -> chunk 0 -> ... -> chunk 2 -> close

``teardown_request`` runs *before the first chunk*, so a unit closed there is
already disposed while the body is still being produced (QRT-2). The framework
does not offer a "finished" hook after the body, so the adapter takes the ASGI
seam instead: ``app.asgi_app`` is a plain ASGI callable, and wrapping it puts the
close exactly where PRD 09's contract wants it — after the last
``http.response.body`` (QRT-3).

Quart stores request state in ``g``, a ``ContextVar``, and the ASGI layer cannot
write a ``ContextVar`` that the handler will see: doing so would leak the scope
into every task spawned during the request, which is the ambient-scope design
D-58 rejects. So the adapter has two moving parts, and both are explicit:

* :class:`DIWrapper` **owns** the lifetime (open before, close after);
* one ``before_request`` hook **publishes** the live scope into ``g`` (QRT-4).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from _host import HostUnit, report_to
from lauren_di import Token

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from lauren_di import DestructError, DIContainer, ResolutionScope
    from quart import Quart

LOGGER = logging.getLogger("examples.quart")

QUART_REQUEST_TOKEN = Token("quart.request")
"""The example's one value token: the request's ASGI scope (IC-6)."""

SCOPE_KEY = "di"
"""Where the wrapper parks the scope in ``scope["state"]``; ``g.di`` mirrors it."""

UNIT_KEY = "lauren_di_unit"
"""Where the wrapper parks its *unit*, which ``g`` deliberately does not carry."""


def current_scope() -> ResolutionScope:
    """Return the scope the adapter published on ``g`` for this request.

    Returns:
        The live :class:`lauren_di.ResolutionScope`.

    Raises:
        RuntimeError: If nothing was published, which means the app was built
            without :func:`install` — a wiring bug, reported as one.
    """
    from quart import g

    try:
        scope = getattr(g, SCOPE_KEY, None)
    except RuntimeError:  # no app context at all, e.g. a call outside a request
        scope = None
    if scope is None:
        raise RuntimeError(
            "no lauren_di scope on `g`: build the app through `quart_di.install`, "
            "which wraps `app.asgi_app` and publishes the scope per request"
        )
    return scope


@dataclass
class Close:
    """One close the wrapper performed, so a test can read the order back."""

    path: str
    body_messages: int
    """How many ``http.response.body`` messages had crossed the seam at close time."""

    errors: tuple[DestructError, ...] = field(default_factory=tuple)


class DIWrapper:
    """The ASGI callable :func:`install` puts in front of ``app.asgi_app`` (QRT-3)."""

    def __init__(
        self,
        app: Any,
        container: DIContainer,
        *,
        report: Callable[[tuple[DestructError, ...]], None] | None = None,
    ) -> None:
        """Wrap ``app`` so every HTTP request becomes one unit of work.

        Args:
            app: The ASGI callable to delegate to — ``app.asgi_app`` at install
                time.
            container: The started, process-wide container (IC-1).
            report: Where teardown failures go instead of the response (IC-8).
        """
        self.app = app
        self.container = container
        self.report = report
        self.closes: list[Close] = []
        """Every close, in order: the evidence the tests read back."""

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        """Open a unit, delegate, and close after the last body message.

        Args:
            scope: The raw ASGI scope.
            receive: The ASGI receive channel.
            send: The ASGI send channel.
        """
        if scope["type"] != "http":
            # Lifespan and WebSocket scopes are not units of work: a lifespan unit
            # would open before the container has started.
            await self.app(scope, receive, send)
            return

        state: dict[str, Any] = scope.setdefault("state", {})
        if SCOPE_KEY in state:
            raise RuntimeError(
                f"{SCOPE_KEY!r} is already bound for this connection: "
                "only one wrapper may own the scope (IC-3)"
            )

        unit = HostUnit(
            self.container,
            values={QUART_REQUEST_TOKEN: scope},
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
        state[UNIT_KEY] = unit
        try:
            await self.app(scope, receive, counting_send)
        finally:
            # The close point: after the last body chunk, which is also after
            # Quart's own teardown hooks — they run *first*, not last (QRT-2).
            errors = await unit.close()
            state.pop(SCOPE_KEY, None)
            state.pop(UNIT_KEY, None)
            self.closes.append(
                Close(path=scope.get("path", ""), body_messages=body_messages, errors=errors)
            )


def install(
    app: Quart,
    container: DIContainer,
    *,
    report: Callable[[tuple[DestructError, ...]], None] | None = None,
) -> DIWrapper:
    """Attach the unit-of-work wrapper and the container lifecycle to ``app``.

    Three registrations, each doing one thing:

    * ``before_serving`` / ``after_serving`` start and stop the container (IC-1,
      IC-9, D-60). They run for a server, and for ``await app.startup()`` — but
      **not** for ``app.test_client()``, which is QRT-5 and why the tests call
      ``startup()`` themselves;
    * the ``app.asgi_app`` wrapper owns the scope lifetime (QRT-3);
    * one ``before_request`` hook copies the scope into ``g`` (QRT-4), because
      ``g`` is a ``ContextVar`` the ASGI layer cannot write.

    Args:
        app: The Quart application under construction.
        container: The container this app owns.
        report: Where teardown failures go (IC-8).

    Returns:
        The installed wrapper, which carries the close log the tests read.
    """

    @app.before_serving
    async def _start() -> None:
        """Start the container before the host accepts work."""
        await container.startup()

    @app.after_serving
    async def _stop() -> None:
        """Stop the container once, reporting what teardown raised."""
        errors = await container.shutdown()
        if errors:
            (report or report_to)(errors)

    @app.before_request
    async def _publish() -> None:
        """Publish the wrapper's scope on ``g`` for this request (QRT-4)."""
        from quart import g, request

        g.di = request.scope.get("state", {}).get(SCOPE_KEY)

    wrapper = DIWrapper(app.asgi_app, container, report=report)
    app.asgi_app = wrapper
    return wrapper


def install_teardown_owner(
    app: Quart,
    container: DIContainer,
    *,
    report: Callable[[tuple[DestructError, ...]], None] | None = None,
) -> None:
    """Install the *wrong* owner, so its cost can be measured (QRT-2, T11-06).

    This is the trap in a form a reader can run: the scope is opened in
    ``before_request`` and closed in ``teardown_request``, which Quart calls
    before the response body is produced. Every streamed chunk then observes an
    already-closed scope.

    Args:
        app: The Quart application under construction.
        container: The container this app owns.
        report: Where teardown failures go (IC-8).
    """

    @app.before_request
    async def _open() -> None:
        """Open this request's unit and publish it on ``g``."""
        from quart import g, request

        unit = HostUnit(
            container,
            values={QUART_REQUEST_TOKEN: request.scope},
            report=report,
        )
        g.di = await unit.open()
        g.di_unit = unit

    @app.teardown_request
    async def _close(_error: BaseException | None) -> None:
        """Close at the framework's echo of the response object — too early."""
        from quart import g

        unit = getattr(g, "di_unit", None)
        if unit is not None:
            await unit.close()


async def conversation(
    asgi: Any,
    path: str,
    *,
    method: str = "GET",
    snapshot: Callable[[], Any] | None = None,
) -> Any:
    """Drive one HTTP exchange through an ASGI callable and record what it sent.

    ``TestClient`` reads the body for you, which erases the ordering this example
    is about; this drives the callable the way a server does and calls
    ``snapshot`` the instant the final body message is handed to the transport.

    Args:
        asgi: The ASGI callable to drive (usually ``app.asgi_app``).
        path: The request path.
        method: The HTTP method.
        snapshot: Called once, when the last body message crosses the seam.

    Returns:
        A :class:`Conversation` with the status, the body and the snapshot.
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
        "state": {},
    }
    delivered = False

    async def receive() -> dict[str, Any]:
        """Deliver one empty body, then report the client as gone."""
        nonlocal delivered
        if delivered:
            return {"type": "http.disconnect"}
        delivered = True
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict[str, Any]) -> None:
        """Record the message, snapping the world at the finished point."""
        nonlocal at_body_end
        messages.append(message)
        final = message["type"] == "http.response.body" and not message.get("more_body", False)
        if final and snapshot is not None and at_body_end is None:
            at_body_end = snapshot()

    await asgi(scope, receive, send)
    status = next((int(m["status"]) for m in messages if m["type"] == "http.response.start"), 0)
    body = b"".join(m.get("body", b"") for m in messages if m["type"] == "http.response.body")
    return Conversation(status=status, body=body, messages=messages, at_body_end=at_body_end)


@dataclass
class Conversation:
    """What one ASGI conversation produced, in order."""

    status: int
    body: bytes
    messages: list[dict[str, Any]] = field(default_factory=list)
    at_body_end: Any = None
    """Snapshot taken the instant the final body message crossed the seam."""


async def startup(app: Quart) -> None:
    """Run Quart's serving hooks, since ``test_client()`` never does (QRT-5).

    Args:
        app: The application to start; its ``before_serving`` hooks — where the
            container starts — run here.
    """
    await app.startup()


async def shutdown(app: Quart) -> None:
    """Run Quart's shutdown hooks (QRT-5).

    Args:
        app: The application to stop; its ``after_serving`` hooks run here.
    """
    await app.shutdown()
