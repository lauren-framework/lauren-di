"""The FastAPI app against PRD 10 §10.1: the close point, measured.

Same enumerated rows as the Starlette module (`T10-nn`), plus the two rows that
are FastAPI's own: ``Depends`` beside ``Inject`` (T10-13) and
``dependency_overrides`` staying container-free (T10-14). Sync test functions,
``asyncio.run`` for async, and a direct ASGI call wherever the transport
boundary is the thing being measured.
"""

from __future__ import annotations

import asyncio
import multiprocessing
from typing import Any

import lauren_di as di
import pytest
from _testing import load, require

require("fastapi")

import _host  # noqa: E402
import httpx  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from starlette.middleware.base import BaseHTTPMiddleware  # noqa: E402
from starlette.requests import Request  # noqa: E402
from starlette.responses import JSONResponse  # noqa: E402
from starlette.routing import Route  # noqa: E402

fastapi_app = load("fastapi", "fastapi_app")
fastapi_di = load("fastapi", "fastapi_di")


class DispatchOwnedMiddleware(BaseHTTPMiddleware):
    """A scope owner built on ``BaseHTTPMiddleware``: the negative control (T10-07)."""

    def __init__(self, app: Any, *, container: Any, report: Any = None) -> None:
        """Wrap ``app`` with a unit opened in ``dispatch`` instead of in ASGI.

        Args:
            app: The downstream ASGI app.
            container: The app's container.
            report: Where teardown failures go.
        """
        super().__init__(app)
        self.container = container
        self.report = report

    async def dispatch(self, request: Request, call_next: Any) -> Any:
        """Open before the call, close as soon as the response object exists.

        Args:
            request: The request the middleware was handed.
            call_next: Starlette's downstream callable.

        Returns:
            The downstream response, unread.
        """
        unit = _host.HostUnit(
            self.container,
            values={fastapi_di.REQUEST_ID: fastapi_di.request_id_for(request.scope)},
            report=self.report,
        )
        try:
            request.state.lauren_di_scope = await unit.open()
            return await call_next(request)
        finally:
            await unit.close()


def _drive(app: Any, recorder: Any, path: str, *, request_id: str = "drive-1") -> list[dict]:
    """Run one request through the full ASGI stack, without a test client.

    ``TestClient`` reads the body for you, which erases the ordering T10-06 is
    about; calling the app directly keeps every outgoing message and records it
    in the same event log the app writes to.

    Args:
        app: The FastAPI app to drive.
        recorder: The app's event log, appended to from the ``send`` wrapper.
        path: The request path.
        request_id: Sent as ``x-request-id``, so the unit has a known id.

    Returns:
        Every ASGI message the app sent, in order.
    """
    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "root_path": "",
        "headers": [(b"x-request-id", request_id.encode())],
        "client": ("testclient", 50000),
        "server": ("testserver", 80),
        "state": {},
    }
    sent: list[dict] = []
    delivered = False

    async def receive() -> dict:
        """Deliver one empty body, then park until the app cancels this task.

        Parking matters: ``StreamingResponse`` listens for ``http.disconnect``
        while it streams, and a ``receive`` that returns instantly would spin the
        event loop instead of yielding it.
        """
        nonlocal delivered
        if not delivered:
            delivered = True
            return {"type": "http.request", "body": b"", "more_body": False}
        await asyncio.Event().wait()
        raise AssertionError("unreachable: the disconnect listener is cancelled")

    async def send(message: dict) -> None:
        """Record the message before it leaves the app."""
        recorder.add("sent", message["type"])
        sent.append(dict(message))

    async def run() -> None:
        """Start the container the way the lifespan does, drive, then stop it."""
        container = app.state.di
        await container.startup()
        try:
            await app(scope, receive, send)
        finally:
            await container.shutdown()

    asyncio.run(run())
    return sent


def _bodies(sent: list[dict]) -> list[bytes]:
    """Return the response body chunks, in order.

    Args:
        sent: The ASGI messages returned by :func:`_drive`.

    Returns:
        The chunk payloads, including the empty terminator.
    """
    return [m.get("body", b"") for m in sent if m["type"] == "http.response.body"]


def test_scope_owner_is_pure_asgi() -> None:
    """The installed owner is a pure ASGI middleware, never ``BaseHTTPMiddleware``."""
    app = fastapi_app.create_app()
    assert [item.cls for item in app.user_middleware] == [fastapi_di.DIScopeMiddleware]
    assert not issubclass(fastapi_di.DIScopeMiddleware, BaseHTTPMiddleware)


def test_app_state_holds_the_container() -> None:
    """§5.8: the process-wide container lives on ``app.state``, not in a global."""
    app = fastapi_app.create_app()
    assert isinstance(app.state.di, di.DIContainer)
    with TestClient(app) as client:
        body = client.get("/whoami").json()
    assert body["container_on_app_state"] is True


def test_lifespan_starts_the_container_before_the_first_request() -> None:
    """T10-01: the singleton is constructed by the lifespan, not by a request."""
    recorder = fastapi_app.Recorder()
    app = fastapi_app.create_app(recorder)
    with TestClient(app) as client:
        assert recorder.kinds() == ["startup"], recorder.events
        assert client.get("/health").status_code == 200
    assert recorder.of("construct") == [], "no request-scoped work before a request"


def test_lifespan_shutdown_reports_its_tuple() -> None:
    """T10-02 / IC-9: ``shutdown()``'s teardown errors are reported, not raised."""
    recorder = fastapi_app.Recorder()
    app = fastapi_app.create_app(recorder, leaky_shutdown=True)
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
    kinds = recorder.kinds()
    assert "shutdown" in kinds, recorder.events
    assert "teardown-error" in kinds, recorder.events
    assert kinds.index("shutdown") < kinds.index("teardown-error")


def test_failed_startup_aborts_the_app() -> None:
    """T10-03: a ``@post_construct`` that raises stops the app before it serves."""

    @di.injectable()
    class Exploding:
        """A singleton that cannot be constructed."""

        @di.post_construct
        def boom(self) -> None:
            """Fail eagerly, as a bad connection string would."""
            raise RuntimeError("startup failed")

    container = _host.build_container(strict_request_scope=True)
    container.register(Exploding)
    app = FastAPI(
        routes=[Route("/", lambda request: JSONResponse({"ok": True}))],
        lifespan=fastapi_di.di_lifespan(container),
    )
    fastapi_di.wire(app, container)
    with (
        pytest.raises(RuntimeError, match="startup failed"),
        TestClient(app) as client,
    ):
        client.get("/")


def test_one_scope_per_request() -> None:
    """T10-04: two connections get two scopes, so two request ids."""
    recorder = fastapi_app.Recorder()
    app = fastapi_app.create_app(recorder)
    with TestClient(app) as client:
        first = client.get("/whoami").json()
        second = client.get("/whoami").json()
    assert first["request_id"] != second["request_id"]
    assert first["scope_id"] != second["scope_id"]
    assert len(recorder.of("teardown")) == 2


def test_scope_is_in_the_request_state() -> None:
    """T10-05 / IC-4: the host state slot holds the live ``ResolutionScope``."""
    recorder = fastapi_app.Recorder()
    app = fastapi_app.create_app(recorder)
    with TestClient(app) as client:
        body = client.get("/whoami", headers={"x-request-id": "abc"}).json()
    assert body["request_id"] == "abc", "the host-owned id is supplied through values="
    assert body["scope_key"] == _host.SCOPE_KEY
    assert _host.SCOPE_KEY in body["state_keys"]
    assert body["is_resolution_scope"] is True
    assert body["closed"] is False, "the handler runs while the scope is open"


def test_teardown_is_the_last_event_of_a_request() -> None:
    """The other end of T10-01: construct ... teardown, once, in order."""
    recorder = fastapi_app.Recorder()
    app = fastapi_app.create_app(recorder)
    with TestClient(app) as client:
        assert client.get("/greet/ada").json()["greeting"].startswith("hello ada from v1.0")
    kinds = recorder.kinds()
    assert kinds.count("construct") == 1
    assert kinds.count("teardown") == 1
    assert kinds.index("construct") < kinds.index("teardown") < kinds.index("shutdown")


def test_streaming_teardown_runs_after_the_last_chunk() -> None:
    """T10-06 / A-2: the pure-ASGI close point is after the last body chunk.

    Measured at the transport boundary: every ``http.response.body`` the app sent
    precedes the ``teardown`` event, and every chunk reports ``closed=False``.
    """
    recorder = fastapi_app.Recorder()
    app = fastapi_app.create_app(recorder)
    sent = _drive(app, recorder, "/stream", request_id="stream-1")

    bodies = [body for body in _bodies(sent) if body]
    assert len(bodies) == 3, bodies
    assert all(b"closed=False" in body for body in bodies), bodies

    last_body = max(
        index
        for index, (kind, detail) in enumerate(recorder.events)
        if kind == "sent" and detail == "http.response.body"
    )
    teardown = recorder.events.index(("teardown", "stream-1"))
    assert last_body < teardown, recorder.events
    assert recorder.of("chunk") == [f"{n}:closed=False" for n in (1, 2, 3)]


def test_base_http_middleware_closes_before_the_last_chunk() -> None:
    """T10-07: the negative control — ``dispatch``'s ``finally`` is too early."""
    recorder = fastapi_app.Recorder()
    app = fastapi_app.create_app(recorder, middleware=DispatchOwnedMiddleware)
    with TestClient(app) as client:
        body = client.get("/stream").text
    assert "chunk 1 closed=False" in body
    assert "chunk 2 closed=True" in body, body
    assert "chunk 3 closed=True" in body
    assert recorder.of("chunk") == ["1:closed=False", "2:closed=True", "3:closed=True"]


def test_background_task_sees_an_open_scope() -> None:
    """T10-08 / A-6: background tasks run before the close point, not after it."""
    recorder = fastapi_app.Recorder()
    app = fastapi_app.create_app(recorder)
    with TestClient(app) as client:
        assert client.get("/audit").status_code == 200
    assert recorder.of("background") == ["closed=False"], recorder.events
    assert recorder.kinds().index("background") < recorder.kinds().index("teardown")


def test_handled_exception_closes_once() -> None:
    """T10-11 / A-8: a handled error still closes exactly one scope."""
    recorder = fastapi_app.Recorder()
    app = fastapi_app.create_app(recorder)
    with TestClient(app) as client:
        response = client.get("/guarded", headers={"x-request-id": "handled"})
    assert response.status_code == 418
    assert recorder.of("teardown") == ["handled"]
    assert "teardown-error" not in recorder.kinds()


def test_unhandled_exception_closes_once() -> None:
    """T10-12 / A-8: the 500 path closes once too — an exception is not a leak."""
    recorder = fastapi_app.Recorder()
    app = fastapi_app.create_app(recorder)
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/unhandled", headers={"x-request-id": "unhandled"})
    assert response.status_code == 500
    assert recorder.of("teardown") == ["unhandled"]


def test_teardown_failure_is_reported_not_raised() -> None:
    """IC-8: a raising ``@pre_destruct`` lands in ``scope.errors`` and is reported.

    The response is complete before teardown runs, so the only legal channel is
    the report callback; the client must still see its 200.
    """
    recorder = fastapi_app.Recorder()
    app = fastapi_app.create_app(recorder)
    with TestClient(app) as client:
        response = client.get("/teardown-fails", headers={"x-request-id": "fragile"})
    assert response.status_code == 200
    assert response.json()["status"] == "sent before teardown"
    assert recorder.of("teardown-raised") == ["Fragile"]
    reported = recorder.of("teardown-error")
    assert len(reported) == 1, recorder.events
    assert "Fragile" in reported[0]


def test_depends_and_inject_coexist() -> None:
    """T10-13 / A-5: ``Depends`` injects; a handler-level ``Inject`` is a marker.

    The route-side seam is ``Annotated[Greeter, Depends(greeter_dep)]`` and the
    container-side seam is ``Annotated[str, Inject(REQUEST_ID)]`` inside
    ``Greeter.__init__``. A handler that writes ``= Inject(...)`` gets the marker
    itself, which is the measured reason FastAPI needs ``Depends``.
    """
    recorder = fastapi_app.Recorder()
    app = fastapi_app.create_app(recorder)
    with TestClient(app) as client:
        via_depends = client.get("/greet/ada", headers={"x-request-id": "dep"}).json()
        marker = client.get("/inject-marker").json()
    assert via_depends["request_id"] == "dep"
    assert "hello ada from v1.0" in via_depends["greeting"]
    assert marker == {"type": "_InjectMarker", "is_marker": True}


def test_dependency_overrides_is_container_free() -> None:
    """T10-14 / A-4: overriding a dependency cannot reach the container.

    ``app.dependency_overrides`` swaps the fake in for the *route*; a route that
    resolves from the container still sees the real service.
    """

    class FakeGreeter:
        """A test double with no container involvement at all."""

        request_id = "fake"

        def greeting(self, name: str) -> str:
            """Answer in a way the real service never would.

            Args:
                name: The path parameter.

            Returns:
                A recognisable fake greeting.
            """
            return f"fake greeting for {name}"

    recorder = fastapi_app.Recorder()
    app = fastapi_app.create_app(recorder)
    app.dependency_overrides[fastapi_app.greeter_dep] = FakeGreeter
    with TestClient(app) as client:
        overridden = client.get("/greet/ada").json()["greeting"]
        real = client.get("/container-greeting/ada").json()["greeting"]
    assert overridden == "fake greeting for ada"
    assert real.startswith("hello ada from v1.0"), real


def test_asgi_transport_does_not_run_the_lifespan() -> None:
    """T10-15 / A-9: ``httpx.ASGITransport`` skips the lifespan, and the app lies.

    Without ``startup()`` nothing is eager, yet the request still answers 200
    because the scope resolves lazily — the singleton's ``@post_construct`` runs
    *inside* the request. The log shows it: ``startup`` (the construction event)
    comes after the request began rather than before it. Call
    ``container.startup()`` yourself, or use ``TestClient``, when the eager
    guarantee matters.
    """
    recorder = fastapi_app.Recorder()
    app = fastapi_app.create_app(recorder)
    assert recorder.kinds() == [], "nothing runs at import time"

    async def call() -> Any:
        """Send one request through ``ASGITransport`` and stop the container."""
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/greet/ada")
        await app.state.di.shutdown()
        return response

    response = asyncio.run(call())
    assert response.status_code == 200
    assert recorder.kinds() == ["startup", "construct", "teardown", "shutdown"], recorder.events


def test_websocket_keeps_one_scope_per_connection() -> None:
    """T10-09 / A-7: three frames on one connection share one scope and one service."""
    recorder = fastapi_app.Recorder()
    app = fastapi_app.create_app(recorder)
    seen: list[dict] = []
    with (
        TestClient(app) as client,
        client.websocket_connect("/channel", headers={"x-request-id": "ws-1"}) as socket,
    ):
        for index in range(3):
            socket.send_text(f"frame-{index}")
            seen.append(socket.receive_json())

    assert len({message["scope_id"] for message in seen}) == 1, seen
    assert len({message["request_id"] for message in seen}) == 1, seen
    # `closed=False` on the last frame is the measurement: a per-message close
    # would have torn the service down before frame 3 arrived.
    assert [message["closed"] for message in seen] == [False, False, False]
    assert recorder.of("construct") == ["ws-1"]
    assert recorder.of("teardown") == ["ws-1"]


def test_a_second_connection_gets_a_new_scope() -> None:
    """T10-10: one connection, one scope — so a reconnect builds a second service."""
    recorder = fastapi_app.Recorder()
    app = fastapi_app.create_app(recorder)
    scopes: list[str] = []
    with TestClient(app) as client:
        for request_id in ("ws-a", "ws-b"):
            with client.websocket_connect(
                "/channel", headers={"x-request-id": request_id}
            ) as socket:
                socket.send_text("hello")
                scopes.append(socket.receive_json()["scope_id"])

    assert scopes[0] != scopes[1], "a reconnect reused the previous connection's scope"
    assert recorder.of("construct") == ["ws-a", "ws-b"]
    assert recorder.of("teardown") == ["ws-a", "ws-b"]


def _worker_facts(app_module: Any, count: int = 2) -> list[dict]:
    """Run ``count`` worker processes, each with its own container (T10-16).

    ``spawn`` rather than ``fork``: a forked child would inherit the parent's
    already-imported modules and could accidentally look like a shared container,
    while a spawned one has to build its own the way ``uvicorn --workers`` does.
    The assertion that matters is the pid, which only a real process boundary can
    produce.

    Args:
        app_module: The example app module whose ``run_worker`` is the target.
        count: How many workers to start.

    Returns:
        One fact dictionary per worker, in completion order.
    """
    context = multiprocessing.get_context("spawn")
    queue = context.Queue()
    workers = [
        context.Process(target=app_module.run_worker, args=(queue, index)) for index in range(count)
    ]
    for worker in workers:
        worker.start()
    facts = [queue.get(timeout=120) for _ in workers]
    for worker in workers:
        worker.join(timeout=120)
        assert worker.exitcode == 0, f"a worker process failed: {worker.exitcode}"
    return facts


def test_one_container_per_worker() -> None:
    """T10-16 / A-10: ``--workers 2`` is two pids, two containers, two startups.

    Each worker's log holds exactly one ``startup`` (its own ``@post_construct``)
    and no ``construct`` (no request-scoped work before traffic), and the pids
    differ \u2014 so nothing was shared but the code.
    """
    facts = _worker_facts(fastapi_app)
    assert len({fact["pid"] for fact in facts}) == 2, facts
    assert [fact["startup"] for fact in facts] == [["Settings"], ["Settings"]], facts
    assert all(fact["construct"] == [] for fact in facts), facts
    assert all(fact["shutdown"] == ["Settings"] for fact in facts), facts
