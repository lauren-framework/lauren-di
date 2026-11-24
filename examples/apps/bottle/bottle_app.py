"""A Bottle app whose request scope is owned by one WSGI middleware.

The three steps every example in this directory shows:

1. build one container per process and ``startup()`` it before the first request
   (IC-1) — Bottle has no lifecycle hook, so the entry point does it (BTL-5);
2. open one scope per request, in the middleware, once ``environ`` exists (IC-3);
3. close it once, from the response iterable's ``close()`` (IC-7, BTL-3).

Run it for the measured transcript::

    python examples/apps/bottle/bottle_app.py

The demo serves one streaming request through a threaded ``wsgiref`` server and
prints the observation log, so the close point is visible rather than asserted:
``chunk:2`` is logged before ``torn-down``.
"""

from __future__ import annotations

import asyncio
import contextlib
import socket
import socketserver
import sys
import threading
import urllib.request
import wsgiref.simple_server
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Annotated, Any

import bottle

# Run as a script — ``python examples/apps/bottle/bottle_app.py`` — this file is
# not inside a package, so neither the shared helpers one directory up nor a
# source-checkout ``lauren_di`` would be importable. Every import below is a
# deliberate late import for that reason; an installed deployment already has
# both paths and the loop is a no-op.
_HERE = Path(__file__).resolve()
for _path in (_HERE.parent.parent, _HERE.parents[3]):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

import _host  # noqa: E402
import bottle_di  # noqa: E402
from lauren_di import (  # noqa: E402
    DestructError,
    DIError,
    Inject,
    Scope,
    Token,
    injectable,
    post_construct,
    pre_destruct,
)

#: The host value: every unit is handed the WSGI environ, keyed by this token.
#: Declaring it keeps ``strict_request_scope`` on, because the app really does
#: have a request-scoped provider to supply it to (IC-2, IC-6).
REQUEST = Token("wsgi.environ")

#: Per-request observation log, in the order the events happen. A real app would
#: emit metrics here; the example records them so a test can compare the order.
EVENTS: list[str] = []

#: One entry per unit close, holding that unit's teardown errors (IC-7, IC-8).
CLOSES: list[tuple[DestructError, ...]] = []

#: One entry per singleton build, so the test can see ``startup()`` precede work.
BOOTS: list[str] = []


def reset() -> None:
    """Clear the observation logs: they are the only state the example keeps."""
    EVENTS.clear()
    CLOSES.clear()
    BOOTS.clear()


@injectable(scope=Scope.SINGLETON)
class Registry:
    """Process-wide state, built once during ``startup()`` (IC-1, T12-02).

    It records the loop it was built on: a host that opened a fresh loop per
    request would build it again, on a different loop, or fail with
    ``RuntimeError: Event loop is closed`` (T12-13).
    """

    def __init__(self) -> None:
        """Leave the loop and thread unset until ``@post_construct`` runs."""
        self.loop: asyncio.AbstractEventLoop | None = None
        self.thread = ""

    @post_construct
    async def warm(self) -> None:
        """Bind the instance to the loop that built it."""
        await asyncio.sleep(0)
        self.loop = asyncio.get_running_loop()
        self.thread = threading.current_thread().name
        BOOTS.append(self.thread)


@injectable(scope=Scope.REQUEST)
class Trail:
    """One audit trail per unit of work: built by the scope, torn down by it.

    ``REQUEST`` scope is what makes it a witness for the close point: it is built
    while the request runs and destroyed when the scope closes, so its
    ``@pre_destruct`` marker can only appear after the last body chunk (T12-12).
    """

    def __init__(self, environ: Annotated[dict[str, Any], Inject(REQUEST)]) -> None:
        """Take the host's own request object, not a copy of it (IC-6, BTL-4).

        Args:
            environ: The WSGI environ the middleware stored the scope in, proving
                the value channel reaches a provider untouched.
        """
        self.environ = environ
        self.thread = threading.current_thread().name

    @post_construct
    async def opened(self) -> None:
        """Record that the unit was built, on the bridge loop."""
        await asyncio.sleep(0)
        EVENTS.append("built")

    @pre_destruct
    async def closing(self) -> None:
        """Record that the unit is being torn down — the close point, in order."""
        await asyncio.sleep(0)
        EVENTS.append("torn-down")


def unit_values(environ: Mapping[str, Any]) -> Mapping[Any, Any]:
    """Hand the WSGI environ to the unit as the ``REQUEST`` value (IC-6)."""
    return {REQUEST: environ}


def record_close(unit: _host.HostUnit, errors: tuple[DestructError, ...]) -> None:
    """Store every close so the tests can count them and check the errors (IC-7).

    IC-8 forbids reporting a teardown failure through the response — the body may
    already be streaming — so the errors are handed to a hook instead.
    """
    CLOSES.append(errors)


class Views:
    """The endpoints, closed over the bridge they must resolve through (BTL-1)."""

    def __init__(self, bridge: _host.SyncBridge) -> None:
        """Keep the bridge: a route is synchronous, the container is not.

        Args:
            bridge: The process-wide bridge every container call runs on.
        """
        self._bridge = bridge

    def resolve(self, token: Any) -> Any:
        """Resolve ``token`` in this request's scope, off the caller's thread.

        ``bottle.request`` is a thread-local proxy over the environ the middleware
        wrote, so the scope is one dict lookup away (BTL-4).
        """
        return bottle_di.resolve(self._bridge, bottle.request.environ[_host.SCOPE_KEY], token)

    def report(self) -> str:
        """Report what this request measured, in a body the test can read back.

        Nothing here is asserted in prose: the scope's own identity, the environ
        the scope was handed, and the thread each piece of work ran on all come
        from the objects themselves (T12-01, T12-03, T12-04, T12-13).
        """
        trail = self.resolve(Trail)
        registry = self.resolve(Registry)
        lines = (
            f"scope={type(bottle.request.environ[_host.SCOPE_KEY]).__name__}",
            f"scope-id={id(bottle.request.environ[_host.SCOPE_KEY])}",
            f"path={trail.environ.get('PATH_INFO')}",
            f"view-thread={threading.current_thread().name}",
            f"built-on={trail.thread}",
            f"registry-thread={registry.thread}",
            f"registry-loop={id(registry.loop)}",
        )
        bottle.response.content_type = "text/plain"
        return "\n".join(lines) + "\n"

    def stream(self) -> Iterator[str]:
        """Return three chunks produced lazily, after the route has returned.

        Each chunk carries ``closed=`` read at the moment it is produced, so the
        body itself shows that the scope was still open while the bytes were
        served (T12-05). Bottle pulls the first chunk while casting the return
        value, which is why ``chunk:0`` exists before the WSGI call returns.
        """
        scope = bottle.request.environ[_host.SCOPE_KEY]
        self.resolve(Trail)

        def body() -> Iterator[str]:
            for index in range(3):
                EVENTS.append(f"chunk:{index}")
                yield f"chunk:{index}:closed={scope.closed}\n"

        bottle.response.content_type = "text/plain"
        return body()

    def fail(self) -> str:
        """Raise before the body exists, to exercise the 500 path (T12-11)."""
        self.resolve(Trail)
        raise DIError("the view failed on purpose")


def build_bottle_app(bridge: _host.SyncBridge) -> bottle.Bottle:
    """Build the Bottle app: three routes, one hook, one error handler.

    Args:
        bridge: The process-wide bridge the routes resolve through.

    Returns:
        A ``Bottle`` application, ready to wrap in :class:`bottle_di.DIMiddleware`.
    """
    views = Views(bridge)
    app = bottle.Bottle()
    app.route("/report", callback=views.report)
    app.route("/stream", callback=views.stream)
    app.route("/fail", callback=views.fail)
    app.add_hook("after_request", on_after_request)
    app.error(500, on_error)
    return app


def on_after_request() -> None:
    """Record Bottle's own end-of-route hook, to show it is not the close point.

    ``after_request`` runs when the callback returns, i.e. before ``chunk:1`` and
    ``chunk:2`` exist (BTL-2), so it could not have owned this scope (T12-09).
    """
    scope = bottle.request.environ[_host.SCOPE_KEY]
    EVENTS.append(f"hook:after_request:closed={scope.closed}")


def on_error(error: bottle.HTTPError) -> str:
    """Answer a failing route with a 500 while the scope is still open (T12-11).

    Bottle's error handler runs inside the request, before the body iterates, so it
    can still read the scope — and the middleware closes it afterwards, once.
    """
    scope = bottle.request.environ[_host.SCOPE_KEY]
    EVENTS.append(f"error-view:closed={scope.closed}")
    bottle.response.content_type = "text/plain"
    return f"{error.body}: the route failed on purpose\n"


class Demo:
    """The example's root: one container, one bridge, one wrapped WSGI app."""

    def __init__(self, *, overrides: Any = None) -> None:
        """Start the container, then wrap the Bottle app around it.

        Args:
            overrides: An ``OverridePlan`` for tests, or ``None`` (IC-11).
        """
        self.bridge = _host.SyncBridge()
        self.container = _host.build_container(value_tokens=(REQUEST,), overrides=overrides)
        self.container.register(Registry)
        self.container.register(Trail)
        # IC-1: startup() finishes before the host accepts any work.
        self.bridge.run(self.container.startup())
        self.bottle_app = build_bottle_app(self.bridge)
        self.wsgi_app = bottle_di.DIMiddleware(
            self.bottle_app,
            container=self.container,
            bridge=self.bridge,
            values=unit_values,
            on_close=record_close,
        )

    def __enter__(self) -> Demo:
        """Let a test (or ``main``) use ``with`` instead of ``try/finally``."""
        return self

    def __exit__(self, *exc_info: Any) -> None:
        """Close the container and the loop on the way out."""
        self.close()

    def close(self) -> None:
        """Shut the container down, then stop the bridge loop (IC-9).

        The order matters: ``shutdown()`` runs on the loop, so the loop has to
        outlive it.
        """
        try:
            self.bridge.run(self.container.shutdown())
        finally:
            self.bridge.close()


class _ThreadedServer(socketserver.ThreadingMixIn, wsgiref.simple_server.WSGIServer):
    """A thread per request, like a real deployment.

    ``wsgiref``'s default single-threaded server would run the route on whichever
    thread serves requests; a thread pool is what the PRD's threaded-server cases
    assume (§10.1), so the demo uses one.
    """

    daemon_threads = True


class _QuietHandler(wsgiref.simple_server.WSGIRequestHandler):
    """Keep the demo's transcript about the container, not about access logs."""

    def log_message(self, format: str, *args: Any) -> None:
        """Drop the server's access log."""


@contextlib.contextmanager
def serve(wsgi_app: Any) -> Iterator[str]:
    """Serve ``wsgi_app`` on a free localhost port for the duration of the block.

    Args:
        wsgi_app: The WSGI callable to serve.

    Yields:
        The base URL, so the caller can drive one real HTTP request through it.
    """
    # Bind :0 and read the port back: a fixed port would collide on a busy CI box.
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    server = wsgiref.simple_server.make_server(
        "127.0.0.1",
        port,
        wsgi_app,
        server_class=_ThreadedServer,
        handler_class=_QuietHandler,
    )
    thread = threading.Thread(target=server.serve_forever, name="bottle-server", daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def main() -> None:
    """Serve one streaming request and print what happened, in order.

    The transcript is the point of the example: the last chunk is produced before
    the unit is torn down, and the teardown happens exactly once.
    """
    with (
        Demo() as demo,
        serve(demo.wsgi_app) as base_url,
        urllib.request.urlopen(f"{base_url}/stream") as response,
    ):
        body = response.read().decode()
        status = response.status
    print(f"GET /stream -> {status}")
    print(body, end="")
    print("events:", EVENTS)
    print("closes:", len(CLOSES), "errors:", CLOSES[0] if CLOSES else None)


if __name__ == "__main__":
    main()
