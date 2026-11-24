"""A runnable Starlette app hosted on ``lauren_di``: the PRD 10 page, in one file.

The shape every host example shares:

* one container per app, built in :func:`create_app` with
  ``strict_request_scope=True`` (IC-1, IC-2);
* the container is started by the lifespan and stopped by it (IC-9);
* one scope per request, opened by ``starlette_di.DIScopeMiddleware`` and closed
  in its ``finally`` (IC-3);
* handlers resolve through ``scope.resolve`` only (IC-5);
* the request id is host-owned: declared in ``value_tokens=`` and supplied
  through ``values=`` (IC-6);
* a raising ``@pre_destruct`` lands in ``scope.errors`` and is *reported*, never
  turned into a 500 (IC-8).

Run it with ``python examples/apps/starlette/starlette_app.py``; it drives itself
in-process and prints what the close point actually did, so the example is
verifiable without a server.
"""

from __future__ import annotations

import asyncio
import os
import pathlib
import sys
from typing import Annotated, Any

# ``examples/apps`` holds the shared ``_host`` adapter and ``examples/apps/starlette``
# holds this app; both are importable without installing anything, which is what
# ``python examples/apps/starlette/starlette_app.py`` needs (the repo root is added
# as well, because ``lauren_di`` is not installed in every checkout).
_APPS = pathlib.Path(__file__).resolve().parents[1]
_ROOT = _APPS.parents[1]
for _path in (_ROOT, _APPS):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

import lauren_di as di  # noqa: E402
from _host import SCOPE_KEY, build_container  # noqa: E402
from starlette.applications import Starlette  # noqa: E402
from starlette.background import BackgroundTask  # noqa: E402
from starlette.requests import Request  # noqa: E402
from starlette.responses import (  # noqa: E402
    JSONResponse,
    PlainTextResponse,
    Response,
    StreamingResponse,
)
from starlette.routing import Route, WebSocketRoute  # noqa: E402
from starlette.websockets import WebSocket, WebSocketDisconnect  # noqa: E402
from starlette_di import (  # noqa: E402
    REQUEST_ID,
    DIScopeMiddleware,
    di_lifespan,
    scope_of,
    wire,
)


class Recorder:
    """An append-only event log, so ordering can be asserted instead of assumed.

    The whole example is about *when* things happen relative to the response, so
    every hook writes here and the assertions compare positions in
    :attr:`events`. Passing one recorder per app keeps the tests independent.
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
    """Process-wide configuration: the singleton every request reads.

    ``@post_construct`` is the proof that the lifespan, not the first request,
    constructed it (T10-01).
    """

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
        """Record that the container tore the singleton down after the last one."""
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

    The ``closed`` flag is read *from inside* the streaming body: if teardown ran
    before the last chunk, the chunk would say ``closed=True``. That is the
    measurement T10-06 asserts on.
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
            request_id: The host-owned id of the unit this was built for (IC-6).
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
    """A request-scoped service whose teardown always fails (IC-8).

    The response is already sent when it runs, so the failure can only be
    reported — never raised into the transport.
    """

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


def build_di_container(recorder: Recorder, *, leaky: bool = False) -> di.DIContainer:
    """Build the one container this app owns.

    Args:
        recorder: The app's event log, registered as an instance so providers can
            record ordering facts.
        leaky: Register :class:`Leaky` too, so ``shutdown()`` returns an error and
            the lifespan has something to report (T10-02).

    Returns:
        A started-later container (``startup()`` belongs to the lifespan).
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


def _routes() -> list[Route]:
    """Return the app's routes.

    Returns:
        The routes, all resolving through the request scope.
    """

    async def health(request: Request) -> JSONResponse:
        """Resolve the singleton through the scope and report the scope's state."""
        scope = scope_of(request)
        settings = await scope.resolve(Settings)
        return JSONResponse({"status": "ok", "version": settings.version})

    async def whoami(request: Request) -> JSONResponse:
        """Expose the two per-request identities plus the host state slot (IC-4)."""
        scope = scope_of(request)
        greeter = await scope.resolve(Greeter)
        state = request.scope["state"]
        return JSONResponse(
            {
                "request_id": greeter.request_id,
                "scope_id": hex(id(scope)),
                "closed": scope.closed,
                "scope_key": SCOPE_KEY,
                "state_keys": sorted(state),
                "is_resolution_scope": isinstance(scope, di.ResolutionScope),
            }
        )

    async def greet(request: Request) -> JSONResponse:
        """Resolve the request-scoped service and use it."""
        scope = scope_of(request)
        greeter = await scope.resolve(Greeter)
        return JSONResponse(
            {
                "greeting": greeter.greeting(request.path_params["name"]),
                "request_id": greeter.request_id,
            }
        )

    async def stream(request: Request) -> StreamingResponse:
        """Stream three chunks, each reporting whether the service is still open."""
        scope = scope_of(request)
        greeter = await scope.resolve(Greeter)

        async def body() -> Any:
            """Yield the chunks; ``greeter.closed`` is the live close-point reading."""
            for index in range(1, 4):
                greeter.recorder.add("chunk", f"{index}:closed={greeter.closed}")
                yield f"chunk {index} closed={greeter.closed}\n".encode()

        return StreamingResponse(body(), media_type="text/plain")

    async def audit(request: Request) -> Response:
        """Run a background task and record whether the scope is still open there."""
        scope = scope_of(request)
        greeter = await scope.resolve(Greeter)
        response = PlainTextResponse("audit scheduled\n")
        response.background = BackgroundTask(
            greeter.recorder.add,
            "background",
            f"closed={greeter.closed}",
        )
        return response

    async def guarded(request: Request) -> Response:
        """Raise a handled error: the exception handler's response closes once."""
        scope = scope_of(request)
        await scope.resolve(Greeter)
        raise LookupError("guarded failure")

    async def unhandled(request: Request) -> Response:
        """Raise an unhandled error: Starlette's 500 path must still close once."""
        scope = scope_of(request)
        await scope.resolve(Greeter)
        raise RuntimeError("unhandled failure")

    async def teardown_fails(request: Request) -> Response:
        """Resolve a service whose teardown raises, and answer before it runs."""
        scope = scope_of(request)
        await scope.resolve(Fragile)
        return JSONResponse({"status": "sent before teardown"})

    async def channel(websocket: WebSocket) -> None:
        """Serve one scope per *connection*, not one per message (T10-09, T10-10).

        The middleware opens the unit when the connection arrives and closes it
        when the connection ends, so three frames share one ``Greeter`` and a
        reconnect builds a second one. Closing per message would tear down a
        service the next frame still needs, which is the WebSocket row of PRD 09's
        close-point table.
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
            # The client hung up: the middleware's `finally` is the close point, so
            # this handler has nothing to tear down itself.
            return

    return [
        Route("/health", health),
        Route("/whoami", whoami),
        Route("/greet/{name}", greet),
        Route("/stream", stream),
        Route("/audit", audit),
        Route("/guarded", guarded),
        Route("/unhandled", unhandled),
        Route("/teardown-fails", teardown_fails, methods=["GET"]),
        WebSocketRoute("/channel", channel),
    ]


def create_app(
    recorder: Recorder | None = None,
    *,
    report: Any = None,
    middleware: Any = DIScopeMiddleware,
    leaky_shutdown: bool = False,
) -> Starlette:
    """Build the Starlette app and wire the container into it.

    Args:
        recorder: The event log to use; a fresh one is made when omitted, so tests
            never share state.
        report: The ``report=`` callback for teardown failures. Defaults to the
            recorder's, because IC-8 says failures are reported and the log is how
            this example reports.
        middleware: The scope owner to install. The default is the pure-ASGI
            :class:`starlette_di.DIScopeMiddleware`; the tests pass a
            ``BaseHTTPMiddleware`` owner to measure the difference (T10-07).
        leaky_shutdown: Register a singleton whose ``@pre_destruct`` raises, so the
            lifespan has a ``shutdown()`` tuple to report (T10-02).

    Returns:
        The app, with ``app.state.di`` holding the container.
    """
    log = recorder if recorder is not None else Recorder()
    container = build_di_container(log, leaky=leaky_shutdown)
    callback = report or log.report
    app = Starlette(
        routes=_routes(),
        lifespan=di_lifespan(container, report=callback),
        exception_handlers={LookupError: _guarded_handler},
    )
    wire(app, container, report=callback, middleware=middleware)
    app.state.recorder = log
    return app


def _guarded_handler(request: Request, exc: Exception) -> Response:
    """Turn a ``LookupError`` into a 418 so the exception path is observable."""
    del request
    return PlainTextResponse(f"guarded: {exc}\n", status_code=418)


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
    from starlette.testclient import TestClient

    recorder = Recorder()
    app = create_app(recorder)
    print("lauren_di + starlette — one container, one scope per request\n")
    with TestClient(app) as client:
        print("after lifespan startup:", recorder.kinds())
        for path in ("/health", "/greet/ada", "/whoami", "/audit", "/guarded", "/teardown-fails"):
            response = client.get(path)
            print(f"GET {path:18} -> {response.status_code} {response.text.strip()}")
        print("\nGET /stream (the close point is measured inside the body):")
        print("  " + client.get("/stream").text.strip().replace("\n", "\n  "))
    print("\nevent order:")
    for kind, detail in recorder.events:
        print(f"  {kind:16} {detail}")
    print("\nstreaming teardown runs after the last chunk and before the log ends.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
