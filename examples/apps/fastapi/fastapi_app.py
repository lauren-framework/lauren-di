"""A runnable FastAPI app hosted on ``lauren_di``: the PRD 10 page, in one file.

Same three steps as every host example, plus the two FastAPI-specific seams
PRD 10 §5.8 makes explicit:

* ``app.state.di`` holds the one container this app owns (IC-1);
* services reach a route through ``Annotated[T, Depends(fastapi_di.dependency(T))]``,
  while the container's own graph uses ``Annotated[str, Inject(REQUEST_ID)]`` —
  two systems, one direction of flow (A-5);
* the container is ``strict_request_scope=True``, so a request-scoped service that
  escapes its unit is a loud error rather than a cached global (IC-2).

Run it with ``python examples/apps/fastapi/fastapi_app.py``; it drives itself
in-process and prints the measured close point.
"""

from __future__ import annotations

import asyncio
import os
import pathlib
import sys
from typing import Annotated, Any

# ``examples/apps`` holds the shared ``_host`` adapter and ``examples/apps/fastapi``
# holds this app; both are importable without installing anything, which is what
# ``python examples/apps/fastapi/fastapi_app.py`` needs (the repo root is added as
# well, because ``lauren_di`` is not installed in every checkout).
_APPS = pathlib.Path(__file__).resolve().parents[1]
_ROOT = _APPS.parents[1]
for _path in (_ROOT, _APPS):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

import lauren_di as di  # noqa: E402
from _host import SCOPE_KEY, build_container  # noqa: E402
from fastapi import (  # noqa: E402
    BackgroundTasks,
    Depends,
    FastAPI,
    Request,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import JSONResponse, PlainTextResponse, StreamingResponse  # noqa: E402
from fastapi_di import (  # noqa: E402
    REQUEST_ID,
    DIScopeMiddleware,
    dependency,
    di_lifespan,
    scope_of,
    wire,
)


class Recorder:
    """An append-only event log, so ordering can be asserted instead of assumed.

    Every hook writes here, which turns "the scope was closed after the last
    chunk" into a comparison of two positions in one list.
    """

    def __init__(self) -> None:
        """Start with an empty log."""
        self.events: list[tuple[str, str]] = []

    def add(self, kind: str, detail: str = "") -> None:
        """Append one event.

        Args:
            kind: The event verb, e.g. ``"teardown"``.
            detail: The subject, e.g. the request id.
        """
        self.events.append((kind, detail))

    def kinds(self) -> list[str]:
        """Return the event verbs in order — what the close-point checks compare."""
        return [kind for kind, _ in self.events]

    def of(self, kind: str) -> list[str]:
        """Return the details of every event of ``kind``, in order.

        Args:
            kind: The event verb to filter on.

        Returns:
            The matching details.
        """
        return [detail for verb, detail in self.events if verb == kind]

    def report(self, errors: tuple[di.DestructError, ...]) -> None:
        """Record teardown failures instead of raising them (IC-8).

        Args:
            errors: The tuple the container produced at the close point.
        """
        for error in errors:
            self.add("teardown-error", getattr(error, "name", repr(error)))


@di.injectable()
class Settings:
    """Process-wide configuration: the singleton every request reads."""

    version = "1.0"

    def __init__(self, recorder: Recorder) -> None:
        """Bind the recorder this app was built with.

        Args:
            recorder: The app's event log.
        """
        self.recorder = recorder

    @di.post_construct
    def ready(self) -> None:
        """Record that eager construction ran before the first request."""
        self.recorder.add("startup", type(self).__name__)

    @di.pre_destruct
    def stop(self) -> None:
        """Record that the container tore the singleton down last."""
        self.recorder.add("shutdown", type(self).__name__)


@di.injectable()
class Leaky:
    """A singleton whose ``@pre_destruct`` always raises, to exercise IC-9.

    Registering it proves that ``container.shutdown()`` *returns* teardown errors
    and the lifespan reports them (T10-02) rather than failing the process exit.
    """

    @di.pre_destruct
    def break_down(self) -> None:
        """Fail, as a broken resource would at shutdown."""
        raise RuntimeError("Leaky.teardown always fails")


@di.injectable(scope=di.Scope.REQUEST)
class Greeter:
    """One greeting per request, torn down once the response has been sent.

    The request id comes from ``Annotated[str, Inject(REQUEST_ID)]``: the
    container's injection, supplied by the host through ``values=`` (IC-6).
    """

    def __init__(
        self,
        settings: Settings,
        recorder: Recorder,
        request_id: Annotated[str, di.Inject(REQUEST_ID)],
    ) -> None:
        """Build the request-scoped service.

        Args:
            settings: The singleton configuration.
            recorder: The app's event log.
            request_id: The host-owned id of the unit this was built for.
        """
        self.settings = settings
        self.recorder = recorder
        self.request_id = request_id
        self.closed = False
        self.recorder.add("construct", request_id)

    def greeting(self, name: str) -> str:
        """Compose the greeting.

        Args:
            name: The path parameter.

        Returns:
            The greeting text, including this request's id.
        """
        return f"hello {name} from v{self.settings.version} (request {self.request_id})"

    @di.pre_destruct
    def teardown(self) -> None:
        """Mark the service closed and record the close point."""
        self.closed = True
        self.recorder.add("teardown", self.request_id)


@di.injectable(scope=di.Scope.REQUEST)
class Fragile:
    """A request-scoped service whose teardown always fails (IC-8)."""

    def __init__(self, recorder: Recorder) -> None:
        """Bind the recorder this app was built with.

        Args:
            recorder: The app's event log.
        """
        self.recorder = recorder

    @di.pre_destruct
    def break_down(self) -> None:
        """Record the attempt, then fail."""
        self.recorder.add("teardown-raised", type(self).__name__)
        raise RuntimeError("Fragile.teardown always fails")


#: The dependency FastAPI calls; the module-level reference is what
#: ``app.dependency_overrides`` is keyed by (T10-14).
settings_dep = dependency(Settings)
greeter_dep = dependency(Greeter)


def build_di_container(recorder: Recorder, *, leaky: bool = False) -> di.DIContainer:
    """Build the one container this app owns.

    Args:
        recorder: The app's event log, registered as an instance.
        leaky: Register :class:`Leaky`, so ``shutdown()`` returns an error for the
            lifespan to report (T10-02).

    Returns:
        A container the lifespan starts.
    """
    container = build_container(
        value_tokens=(REQUEST_ID,),
        strict_request_scope=True,
    )
    container.register_instance(Recorder, recorder)
    container.register(Settings)
    container.register(Greeter)
    container.register(Fragile)
    if leaky:
        container.register(Leaky)
    return container


def _install_routes(app: FastAPI) -> None:
    """Register this app's routes on ``app``.

    Args:
        app: The FastAPI application to populate.
    """

    @app.get("/health")
    async def health(settings: Annotated[Settings, Depends(settings_dep)]) -> JSONResponse:
        """Resolve the singleton through the scope and answer."""
        return JSONResponse({"status": "ok", "version": settings.version})

    @app.get("/whoami")
    async def whoami(request: Request) -> JSONResponse:
        """Expose the per-request identities and the host state slot (IC-4)."""
        scope = scope_of(request)
        greeter = await scope.resolve(Greeter)
        return JSONResponse(
            {
                "request_id": greeter.request_id,
                "scope_id": hex(id(scope)),
                "closed": scope.closed,
                "scope_key": SCOPE_KEY,
                "state_keys": sorted(request.scope["state"]),
                "is_resolution_scope": isinstance(scope, di.ResolutionScope),
                "container_on_app_state": isinstance(request.app.state.di, di.DIContainer),
            }
        )

    @app.get("/greet/{name}")
    async def greet(
        name: str,
        greeter: Annotated[Greeter, Depends(greeter_dep)],
    ) -> JSONResponse:
        """Serve the greeting through ``Depends`` — the route-side seam."""
        return JSONResponse({"greeting": greeter.greeting(name), "request_id": greeter.request_id})

    @app.get("/container-greeting/{name}")
    async def container_greeting(request: Request, name: str) -> JSONResponse:
        """Resolve straight from the container, bypassing ``Depends``.

        This route exists to show that ``app.dependency_overrides`` cannot reach
        the container: overriding ``greeter_dep`` changes ``/greet`` and leaves
        this route on the real service (T10-14).
        """
        greeter = await scope_of(request).resolve(Greeter)
        return JSONResponse({"greeting": greeter.greeting(name)})

    @app.get("/inject-marker")
    async def inject_marker(
        service: Any = di.Inject(Greeter),  # noqa: B008 - the marker *is* the point
    ) -> JSONResponse:
        """Document A-5: FastAPI passes an ``Inject`` default straight through.

        FastAPI does not read lauren_di's ``Inject``; the handler receives the
        ``_InjectMarker`` object itself. Services reach a FastAPI route through
        ``Depends``, and ``Inject`` stays inside the container's graph.
        """
        return JSONResponse(
            {
                "type": type(service).__name__,
                "is_marker": type(service).__name__ == "_InjectMarker",
            }
        )

    @app.get("/stream")
    async def stream(request: Request) -> StreamingResponse:
        """Stream three chunks, each reporting whether the service is still open."""
        greeter = await scope_of(request).resolve(Greeter)

        async def body() -> Any:
            """Yield the chunks; ``greeter.closed`` is the live close-point reading."""
            for index in range(1, 4):
                greeter.recorder.add("chunk", f"{index}:closed={greeter.closed}")
                yield f"chunk {index} closed={greeter.closed}\n".encode()

        return StreamingResponse(body(), media_type="text/plain")

    @app.get("/audit")
    async def audit(request: Request, background: BackgroundTasks) -> PlainTextResponse:
        """Schedule a background task and record whether the scope is open there."""
        greeter = await scope_of(request).resolve(Greeter)
        background.add_task(greeter.recorder.add, "background", f"closed={greeter.closed}")
        return PlainTextResponse("audit scheduled\n")

    @app.get("/guarded")
    async def guarded(request: Request) -> PlainTextResponse:
        """Raise a handled error: the exception handler's response closes once."""
        await scope_of(request).resolve(Greeter)
        raise LookupError("guarded failure")

    @app.get("/unhandled")
    async def unhandled(request: Request) -> PlainTextResponse:
        """Raise an unhandled error: the 500 path must still close once."""
        await scope_of(request).resolve(Greeter)
        raise RuntimeError("unhandled failure")

    @app.get("/teardown-fails")
    async def teardown_fails(request: Request) -> JSONResponse:
        """Resolve a service whose teardown raises, and answer before it runs."""
        await scope_of(request).resolve(Fragile)
        return JSONResponse({"status": "sent before teardown"})

    @app.websocket("/channel")
    async def channel(websocket: WebSocket) -> None:
        """Serve one scope per connection, not one per message (T10-09, T10-10).

        FastAPI routes WebSockets through the same ASGI stack, so the pure
        middleware owns the unit here too: three frames share one ``Greeter`` and
        a reconnect builds a second one. Resolving through ``scope_of`` rather
        than ``Depends`` keeps the message loop explicit about *which* unit it is
        answering for.
        """
        scope = scope_of(websocket)
        greeter = await scope.resolve(Greeter)
        await websocket.accept()
        try:
            while True:
                message = await websocket.receive_text()
                await websocket.send_json(
                    {
                        "echo": message,
                        "scope_id": hex(id(scope)),
                        "request_id": greeter.request_id,
                        "closed": greeter.closed,
                    }
                )
        except WebSocketDisconnect:
            # The client hung up; the middleware's `finally` closes the scope.
            return


def _guarded_handler(request: Request, exc: Exception) -> PlainTextResponse:
    """Turn a ``LookupError`` into a 418, as PRD 10 §10.1 T10-11 expects."""
    del request
    return PlainTextResponse(f"guarded: {exc}\n", status_code=418)


def create_app(
    recorder: Recorder | None = None,
    *,
    report: Any = None,
    middleware: Any = DIScopeMiddleware,
    leaky_shutdown: bool = False,
) -> FastAPI:
    """Build the FastAPI app and wire the container into it.

    Args:
        recorder: The event log to use; a fresh one is made when omitted.
        report: The ``report=`` callback for teardown failures. Defaults to the
            recorder's, because IC-8 says failures are reported and the log is how
            this example reports.
        middleware: The scope owner to install. The default is the pure-ASGI
            :class:`fastapi_di.DIScopeMiddleware`; the tests pass a
            ``BaseHTTPMiddleware`` owner to measure the difference (T10-07).
        leaky_shutdown: Register a singleton whose ``@pre_destruct`` raises, so the
            lifespan has a ``shutdown()`` tuple to report (T10-02).

    Returns:
        The app, with ``app.state.di`` holding the container.
    """
    log = recorder if recorder is not None else Recorder()
    container = build_di_container(log, leaky=leaky_shutdown)
    callback = report or log.report
    app = FastAPI(
        title="lauren_di + FastAPI",
        lifespan=di_lifespan(container, report=callback),
    )
    _install_routes(app)
    app.add_exception_handler(LookupError, _guarded_handler)
    wire(app, container, report=callback, middleware=middleware)
    app.state.recorder = log
    return app


def run_worker(queue: Any, index: int = 0) -> None:
    """Serve as one worker process: build *this* worker's container and run it.

    ``uvicorn --workers 2`` forks two interpreters, and each one runs the lifespan
    for the container it built itself \u2014 the only thing shared is the code (A-10).
    This function is that worker in miniature, and it is the
    ``multiprocessing`` target ``test_one_container_per_worker`` (T10-16) measures
    over a real process boundary, so the example can prove the row without
    binding a port.

    Args:
        queue: The queue the parent reads the facts from. It must be created by
            the same multiprocessing context that spawned this process.
        index: The worker's ordinal, so the parent can tell two workers apart
            even before it has compared their pids.
    """

    async def lifespan() -> None:
        """Start the container, then stop it, exactly as the lifespan does."""
        await container.startup()
        await container.shutdown()

    recorder = Recorder()
    container = build_di_container(recorder)
    asyncio.run(lifespan())
    queue.put(
        {
            "pid": os.getpid(),
            "index": index,
            "startup": recorder.of("startup"),
            "construct": recorder.of("construct"),
            "shutdown": recorder.of("shutdown"),
        }
    )


def main() -> int:
    """Drive the app in-process, print what happened, and exit 0.

    Returns:
        The process exit code: 0 when every step behaved.
    """
    # Imported here so the module stays importable without the test client's
    # transitive dependency on httpx.
    from fastapi.testclient import TestClient

    recorder = Recorder()
    app = create_app(recorder)
    print("lauren_di + fastapi — one container, one scope per request\n")
    with TestClient(app) as client:
        print("after lifespan startup:", recorder.kinds())
        for path in (
            "/health",
            "/greet/ada",
            "/whoami",
            "/inject-marker",
            "/audit",
            "/guarded",
            "/teardown-fails",
        ):
            response = client.get(path)
            print(f"GET {path:20} -> {response.status_code} {response.text.strip()}")
        print("\nGET /stream (the close point is measured inside the body):")
        print("  " + client.get("/stream").text.strip().replace("\n", "\n  "))
    print("\nevent order:")
    for kind, detail in recorder.events:
        print(f"  {kind:16} {detail}")
    print("\nstreaming teardown runs after the last chunk and before the log ends.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
