"""A runnable Flask app whose per-request objects all come from ``lauren_di``.

Run it with ``python examples/apps/flask/flask_app.py`` and ask for
``/hello``, ``/stream`` or ``/oops``. The interesting route is ``/stream``: its
body is produced *after* the view function has returned, which is exactly the
window a naive adapter closes the scope in.

The three steps every WSGI example shows:

1. ``startup()`` once, on the process-wide bridge, before the first request;
2. one scope per request, opened by the middleware once ``environ`` exists;
3. closed by the response iterable's ``close()`` — never at view return.
"""

from __future__ import annotations

import asyncio
import threading
import uuid
from collections.abc import Iterator
from typing import Annotated, Any

import flask_di

# ``flask_di`` puts the shared examples directory on ``sys.path`` when it is
# imported (a script only gets its own directory), so the reference adapter
# can be imported here, after it.
from _host import build_container
from flask import Flask, Response, jsonify, request
from lauren_di import DestructError, Inject, Scope, injectable, post_construct, pre_destruct

#: The observable trace. The app appends to it, the tests only read it, so the
#: assertions are about the order of real events rather than about mocks.
EVENTS: list[str] = []


def record_errors(errors: tuple[DestructError, ...]) -> None:
    """Send teardown failures to the trace instead of to the response (IC-8)."""
    EVENTS.extend(f"teardown-error:{type(error).__name__}" for error in errors)


@injectable()
class Settings:
    """A singleton: built once at ``startup()``, shared by every request."""

    def __init__(self) -> None:
        """Hold the value the routes print, so a leak would be visible."""
        self.greeting = "hello"

    @post_construct
    def announce(self) -> None:
        """Record that startup, not a request, built this object."""
        EVENTS.append("settings:started")


@injectable(scope=Scope.REQUEST)
class RequestId:
    """One identity per request: the cheapest proof that scopes are not shared."""

    def __init__(self, environ: Annotated[dict[str, Any], Inject(flask_di.ENVIRON)]) -> None:
        """Receive this request's environ through the scope's ``values``."""
        self.value = uuid.uuid4().hex[:12]
        self.path = environ.get("PATH_INFO", "")
        # Constructed on the bridge's loop thread, not on the worker that is
        # serving the request — the two are deliberately kept apart (IC-10).
        self.built_on = threading.current_thread().name


@injectable(scope=Scope.REQUEST)
class UnitOfWork:
    """A transaction that commits when the *client* is done, not when the view is."""

    def __init__(self, request_id: RequestId) -> None:
        """Depend on the request id, so one scope tears both of them down."""
        self.request_id = request_id

    @post_construct
    def begin(self) -> None:
        """Record the moment the transaction was really opened."""
        EVENTS.append(f"uow:begin:{self.request_id.value}")

    @pre_destruct
    def commit(self) -> None:
        """Record the commit: the marker the close-point test looks for."""
        EVENTS.append(f"uow:commit:{self.request_id.value}")


@injectable()
class BridgeBound:
    """A singleton that remembers the loop it was built on (T12-13, D-59).

    Built once by ``startup()`` on the process-wide bridge loop, then reused by
    every request. If the adapter opened a loop per request, the second request
    would find either a different loop or a closed one — which is the failure
    D-59 exists to prevent.
    """

    def __init__(self) -> None:
        """Start with no loop: ``@post_construct`` records the real one."""
        self.loop: asyncio.AbstractEventLoop | None = None

    @post_construct
    async def _capture_loop(self) -> None:
        """Record the loop this singleton was constructed on."""
        self.loop = asyncio.get_running_loop()


@injectable(scope=Scope.REQUEST)
class FailingTransaction:
    """A teardown that always fails: IC-8 says it must be reported, never raised."""

    @pre_destruct
    def rollback(self) -> None:
        """Fail on purpose — the response has already been chosen by now."""
        raise RuntimeError("rollback failed")


def create_app(adapter: flask_di.WSGIAdapter) -> Flask:
    """Build the Flask app; every view resolves through ``adapter``'s request scope.

    Args:
        adapter: the container, bridge and middleware factory for this process.

    Returns:
        The app, with ``wsgi_app`` already wrapped by :class:`flask_di.DIMiddleware`.
    """
    app = Flask(__name__)
    # Wrapping ``wsgi_app`` rather than the app object keeps ``app`` a Flask app
    # (``url_for``, ``test_client``, the CLI) while putting the middleware in
    # the path of exactly what a real server runs.
    app.wsgi_app = adapter.wrap(app.wsgi_app, report=record_errors)

    @app.teardown_request
    def _record_flask_teardown(exc: BaseException | None) -> None:
        """Record when *Flask* thinks the request ended — measured, not assumed."""
        EVENTS.append("flask:teardown-request")

    @app.get("/hello")
    def hello() -> Response:
        """Resolve the singleton and the request's unit of work, and return both."""
        settings = adapter.resolve(request.environ, Settings)
        unit = adapter.resolve(request.environ, UnitOfWork)
        return jsonify(
            greeting=settings.greeting,
            id=unit.request_id.value,
            path=unit.request_id.path,
            thread=threading.current_thread().name,
            built_on=unit.request_id.built_on,
        )

    @app.get("/stream")
    def stream() -> Response:
        """Stream three chunks produced after this view has returned (T12-05)."""
        scope = request.environ[flask_di.SCOPE_KEY]
        unit = adapter.resolve(request.environ, UnitOfWork)

        def body() -> Iterator[bytes]:
            """Emit one chunk per line, recording each as it is produced."""
            for index in range(3):
                EVENTS.append(f"chunk:{index}")
                yield f"{unit.request_id.value}:{index}:closed={scope.closed}\n".encode()

        EVENTS.append("handler:returned")
        return Response(body(), mimetype="text/plain")

    @app.get("/loop")
    def loop_report() -> Response:
        """Report the loop the singleton was built on, so affinity is checkable."""
        bound = adapter.resolve(request.environ, BridgeBound)
        loop = bound.loop
        return jsonify(
            loop=repr(loop),
            running=bool(loop is not None and loop.is_running()),
            is_bridge_loop=loop is adapter.bridge.loop,
        )

    @app.get("/boom")
    def boom() -> Response:
        """Resolve, then raise, so the error path's close count is measurable (T12-11)."""
        adapter.resolve(request.environ, UnitOfWork)
        raise RuntimeError("the view failed on purpose")

    @app.errorhandler(RuntimeError)
    def _record_error(error: RuntimeError) -> tuple[Response, int]:
        """Record what the error handler could still see, then answer 500 (T12-11)."""
        scope = request.environ.get(flask_di.SCOPE_KEY)
        EVENTS.append(f"error-handler:closed={None if scope is None else scope.closed}")
        return Response("the view failed on purpose\n", mimetype="text/plain"), 500

    @app.get("/oops")
    def oops() -> Response:
        """Resolve a request-scoped object whose teardown raises; still answer 200."""
        adapter.resolve(request.environ, FailingTransaction)
        return Response("the teardown failure is not this response's problem\n")

    return app


def build() -> tuple[Flask, flask_di.WSGIAdapter, flask_di.SyncBridge]:
    """Build the container, the adapter and the app, in the one order that works.

    Returns:
        ``(app, adapter, bridge)``; the bridge is returned so the entry point can
        shut the container down on the same loop before stopping that loop.
    """
    container = build_container(value_tokens=(flask_di.ENVIRON,))
    bridge = flask_di.SyncBridge()
    adapter = flask_di.WSGIAdapter(container, bridge)
    for provider in (
        Settings,
        RequestId,
        UnitOfWork,
        BridgeBound,
        FailingTransaction,
    ):
        container.register(provider)
    # Startup happens at import time, so no request can ever observe a
    # half-started container (T12-02).
    bridge.run(container.startup())
    return create_app(adapter), adapter, bridge


def main() -> None:
    """Serve the app on a single-threaded WSGI server, then shut the container down."""
    from wsgiref.simple_server import make_server

    server = make_server("127.0.0.1", 8000, APP)
    print("flask app serving on http://127.0.0.1:8000")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        # Ctrl-C is how the demo stops: fall through to the teardown below rather
        # than letting the traceback replace the shutdown tuple.
        pass
    finally:
        BRIDGE.run(CONTAINER.shutdown())
        BRIDGE.close()


APP, ADAPTER, BRIDGE = build()
CONTAINER = ADAPTER.container

if __name__ == "__main__":
    main()
