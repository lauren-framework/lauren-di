"""A runnable Falcon app whose per-request objects all come from ``lauren_di``.

Run it with ``python examples/apps/falcon/falcon_app.py`` and ask for
``/hello``, ``/stream`` or ``/oops``. The interesting route is ``/stream``: its
body is produced *after* the responder has returned, which is exactly the window
a naive adapter closes the scope in.

The three steps every WSGI example shows:

1. ``startup()`` once, on the process-wide bridge, before the first request;
2. one scope per request, opened by the middleware once ``environ`` exists;
3. closed by the response iterable's ``close()`` — never at responder return.
"""

from __future__ import annotations

import asyncio
import threading
import uuid
from collections.abc import Iterator
from typing import Annotated, Any

import falcon
import falcon_di

# ``falcon_di`` puts the shared examples directory on ``sys.path`` when it is
# imported (a script only gets its own directory), so the reference adapter
# can be imported here, after it.
from _host import build_container
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
        """Hold the value the resources print, so a leak would be visible."""
        self.greeting = "hello"

    @post_construct
    def announce(self) -> None:
        """Record that startup, not a request, built this object."""
        EVENTS.append("settings:started")


@injectable(scope=Scope.REQUEST)
class RequestId:
    """One identity per request: the cheapest proof that scopes are not shared."""

    def __init__(self, environ: Annotated[dict[str, Any], Inject(falcon_di.ENVIRON)]) -> None:
        """Receive this request's environ through the scope's ``values``."""
        self.value = uuid.uuid4().hex[:12]
        self.path = environ.get("PATH_INFO", "")
        # Constructed on the bridge's loop thread, not on the worker that is
        # serving the request — the two are deliberately kept apart (IC-10).
        self.built_on = threading.current_thread().name


@injectable(scope=Scope.REQUEST)
class UnitOfWork:
    """A transaction that commits when the *client* is done, not when the resource is."""

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
    every request. A bridge that opened a loop per request would leave the second
    request with a different loop — or a closed one — which is the failure D-59
    exists to prevent.
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


class ResponseWatcher:
    """Falcon middleware: record when *Falcon* thinks the response is over.

    ``process_response`` runs inside ``App.__call__``, so it fires before the WSGI
    server reads the first chunk of a streamed body. That is what makes it
    measurably too early to be the scope's close point.
    """

    def process_response(
        self,
        req: falcon.Request,
        resp: falcon.Response,
        resource: Any,
        req_succeeded: bool,
    ) -> None:
        """Append the marker; the ordering is the assertion, not the value."""
        EVENTS.append("falcon:process-response")


class HelloResource:
    """``GET /hello``: resolve request-scoped objects and return them as JSON."""

    def __init__(self, adapter: falcon_di.WSGIAdapter) -> None:
        """Take the process-wide adapter, not a request-scoped object."""
        self._adapter = adapter

    def on_get(self, req: falcon.Request, resp: falcon.Response) -> None:
        """Resolve the singleton and this request's unit of work, and return both."""
        settings = self._adapter.resolve(req.env, Settings)
        unit = self._adapter.resolve(req.env, UnitOfWork)
        resp.media = {
            "greeting": settings.greeting,
            "id": unit.request_id.value,
            "path": unit.request_id.path,
            "thread": threading.current_thread().name,
            "built_on": unit.request_id.built_on,
        }


class StreamResource:
    """``GET /stream``: stream three chunks produced after the responder returned."""

    def __init__(self, adapter: falcon_di.WSGIAdapter) -> None:
        """Take the process-wide adapter so the responder can resolve lazily."""
        self._adapter = adapter

    def on_get(self, req: falcon.Request, resp: falcon.Response) -> None:
        """Set ``resp.stream`` to a generator and record the return point (T12-05)."""
        scope = req.env[falcon_di.SCOPE_KEY]
        unit = self._adapter.resolve(req.env, UnitOfWork)

        def body() -> Iterator[bytes]:
            """Emit one chunk per line, recording each as it is produced."""
            for index in range(3):
                EVENTS.append(f"chunk:{index}")
                yield f"{unit.request_id.value}:{index}:closed={scope.closed}\n".encode()

        EVENTS.append("handler:returned")
        resp.content_type = "text/plain"
        resp.stream = body()


class LoopResource:
    """``GET /loop``: report the loop the singleton was built on (T12-13)."""

    def __init__(self, adapter: falcon_di.WSGIAdapter) -> None:
        """Take the process-wide adapter so the responder can resolve lazily."""
        self._adapter = adapter

    def on_get(self, req: falcon.Request, resp: falcon.Response) -> None:
        """Return the loop the singleton holds, and whether it is the bridge's."""
        bound = self._adapter.resolve(req.env, BridgeBound)
        loop = bound.loop
        resp.media = {
            "loop": repr(loop),
            "running": bool(loop is not None and loop.is_running()),
            "is_bridge_loop": loop is self._adapter.bridge.loop,
        }


class BoomResource:
    """``GET /boom``: resolve, then raise, so the error path is measurable (T12-11)."""

    def __init__(self, adapter: falcon_di.WSGIAdapter) -> None:
        """Take the process-wide adapter so the responder can resolve lazily."""
        self._adapter = adapter

    def on_get(self, req: falcon.Request, resp: falcon.Response) -> None:
        """Resolve a request-scoped object, then fail the way a view does."""
        self._adapter.resolve(req.env, UnitOfWork)
        raise RuntimeError("the view failed on purpose")


class OopsResource:
    """``GET /oops``: a request-scoped object whose teardown raises, answered 200."""

    def __init__(self, adapter: falcon_di.WSGIAdapter) -> None:
        """Take the process-wide adapter so the responder can resolve lazily."""
        self._adapter = adapter

    def on_get(self, req: falcon.Request, resp: falcon.Response) -> None:
        """Resolve the doomed object; the failure belongs to teardown, not here."""
        self._adapter.resolve(req.env, FailingTransaction)
        resp.text = "the teardown failure is not this response's problem\n"


def create_app(adapter: falcon_di.WSGIAdapter) -> falcon.App:
    """Build the Falcon app; every resource resolves through ``adapter``'s scope.

    Args:
        adapter: the container, bridge and middleware factory for this process.

    Returns:
        A ``falcon.App``; its own hooks are recorded, but it does not own the
        scope. :func:`build` wraps it in :class:`falcon_di.DIMiddleware`.
    """
    app = falcon.App(middleware=[ResponseWatcher()])
    app.add_route("/hello", HelloResource(adapter))
    app.add_route("/stream", StreamResource(adapter))
    app.add_route("/loop", LoopResource(adapter))
    app.add_route("/boom", BoomResource(adapter))
    app.add_route("/oops", OopsResource(adapter))

    def record_error(
        req: falcon.Request,
        resp: falcon.Response,
        error: RuntimeError,
        params: Any,
    ) -> None:
        """Record what the error handler could still see, then answer 500 (T12-11)."""
        del error, params
        scope = req.env.get(falcon_di.SCOPE_KEY)
        EVENTS.append(f"error-handler:closed={None if scope is None else scope.closed}")
        resp.content_type = "text/plain"
        resp.status = falcon.HTTP_500
        resp.text = "the view failed on purpose\n"

    app.add_error_handler(RuntimeError, record_error)
    return app


def build() -> tuple[falcon_di.WSGIApp, falcon.App, falcon_di.WSGIAdapter, falcon_di.SyncBridge]:
    """Build the container, the adapter and the app, in the one order that works.

    Returns:
        ``(application, app, adapter, bridge)``: the WSGI callable to serve, the
        Falcon app it wraps, and the adapter whose loop shutdown must run on.
    """
    container = build_container(value_tokens=(falcon_di.ENVIRON,))
    bridge = falcon_di.SyncBridge()
    adapter = falcon_di.WSGIAdapter(container, bridge)
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
    app = create_app(adapter)
    return adapter.wrap(app, report=record_errors), app, adapter, bridge


def main() -> None:
    """Serve the application on a single-threaded WSGI server, then shut it down."""
    from wsgiref.simple_server import make_server

    server = make_server("127.0.0.1", 8001, APPLICATION)
    print("falcon app serving on http://127.0.0.1:8001")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        # Ctrl-C is how the demo stops: fall through to the teardown below rather
        # than letting the traceback replace the shutdown tuple.
        pass
    finally:
        BRIDGE.run(CONTAINER.shutdown())
        BRIDGE.close()


APPLICATION, APP, ADAPTER, BRIDGE = build()
CONTAINER = ADAPTER.container

if __name__ == "__main__":
    main()
