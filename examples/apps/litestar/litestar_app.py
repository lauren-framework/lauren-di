"""``lauren_di`` hosted in Litestar: one container, one unit per request (PRD 11 §5.2).

Run it::

    python examples/apps/litestar/litestar_app.py       # in-process self-check
    litestar --app litestar_app:app run                 # the real thing

Both go through :class:`litestar_di.DIMiddleware`, an ``ASGIMiddleware``, because
that is the only Litestar hook that runs after the body (LIT-2). Handlers take
their scope from ``request.state`` and resolve through it; nothing reaches for the
container, and no provider is stored in a module global (D-58).
"""

from __future__ import annotations

import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any
from uuid import uuid4

if __name__ == "__main__":  # a direct run must still find the repo and the shared files
    for _parent in (Path(__file__).resolve().parents[1], Path(__file__).resolve().parents[3]):
        if str(_parent) not in sys.path:
            sys.path.insert(0, str(_parent))

import litestar_di
from _host import build_container
from lauren_di import (
    DIContainer,
    Inject,
    ResolutionScope,
    Scope,
    injectable,
    post_construct,
    pre_destruct,
)
from litestar import Litestar, Request, get
from litestar.background_tasks import BackgroundTask
from litestar.response import Stream
from litestar_di import LITESTAR_SCOPE_TOKEN, current_scope


@injectable()
class Recorder:
    """The example's event log: providers and routes append to it.

    A container-scoped singleton, so the log belongs to the object graph the
    example demonstrates rather than to a module global.
    """

    def __init__(self) -> None:
        """Start with an empty log."""
        self.events: list[str] = []

    def record(self, tag: str) -> None:
        """Append one event.

        Args:
            tag: The event, e.g. ``"chunk:0"``.
        """
        self.events.append(tag)

    def report(self, errors: tuple[Any, ...]) -> None:
        """Record teardown failures instead of raising them (IC-8).

        Args:
            errors: The tuple the unit produced at its close point.
        """
        for error in errors:
            self.record(f"teardown-error:{type(error).__name__}")


class Greeting:
    """The abstraction the app resolves; the container picks the implementation."""

    def greet(self, name: str) -> str:
        """Say hello.

        Args:
            name: Who to greet.

        Returns:
            The greeting.
        """
        raise NotImplementedError


@injectable(provides=[Greeting])
class DefaultGreeting(Greeting):
    """A container-scoped provider: built once, reused by every request (IC-1)."""

    def __init__(self, recorder: Recorder) -> None:
        """Bind the recorder this app was built with.

        Args:
            recorder: The app's event log.
        """
        self.recorder = recorder

    def greet(self, name: str) -> str:
        """Say hello.

        Args:
            name: Who to greet.

        Returns:
            The greeting text.
        """
        return f"hello, {name}"

    @post_construct
    async def _started(self) -> None:
        """Record eager construction, which happens before the first request."""
        self.recorder.record("greeting:start")

    @pre_destruct
    async def _stopped(self) -> None:
        """Record container teardown, which happens after the last request."""
        self.recorder.record("greeting:stop")


@injectable(scope=Scope.REQUEST)
class RequestUnit:
    """The request-scoped service: one per unit, disposed at the close point."""

    def __init__(
        self,
        recorder: Recorder,
        scope: Annotated[Any, Inject(LITESTAR_SCOPE_TOKEN)],
    ) -> None:
        """Build the request-scoped service.

        Args:
            recorder: The app's event log.
            scope: The ASGI scope, supplied by the adapter through ``values=`` (IC-6).
        """
        self.recorder = recorder
        self.scope = scope
        self.request_id = uuid4().hex[:8]

    @post_construct
    async def _opened(self) -> None:
        """Record the unit opening."""
        self.recorder.record(f"unit:open:{self.request_id}")

    @pre_destruct
    async def _closed(self) -> None:
        """Record the unit closing: this is the close point, in order."""
        self.recorder.record(f"unit:close:{self.request_id}")


@injectable(scope=Scope.REQUEST)
class DoomedTeardown:
    """A service whose teardown raises: the adapter reports it and the reply lives."""

    def __init__(self, recorder: Recorder) -> None:
        """Bind the recorder this app was built with.

        Args:
            recorder: The app's event log.
        """
        self.recorder = recorder

    @pre_destruct
    async def _closed(self) -> None:
        """Fail on purpose, after the response has been produced."""
        self.recorder.record("doomed:teardown")
        raise RuntimeError("teardown exploded on purpose")


@dataclass
class Example:
    """Everything one app owns: the recorder, the container, the app and the adapter."""

    recorder: Recorder
    container: DIContainer
    app: Litestar
    adapter: litestar_di.DIMiddleware


def create_container(recorder: Recorder) -> DIContainer:
    """Build the one container this app owns.

    Args:
        recorder: The app's event log, registered as an instance so providers can
            record ordering facts.

    Returns:
        A container the app's ``on_startup`` hook starts (IC-1).
    """

    @injectable(provides=[Recorder])
    def _recorder() -> Recorder:
        """Hand the container the recorder this app was built with."""
        return recorder

    container = build_container(value_tokens=(LITESTAR_SCOPE_TOKEN,))
    container.register(_recorder)
    container.register(DefaultGreeting)
    container.register(RequestUnit)
    container.register(DoomedTeardown)
    return container


def _handlers(recorder: Recorder) -> list[Any]:
    """Return this app's route handlers.

    Args:
        recorder: Unused as a value; kept so the routes stay next to the providers
            they are about and the signature mirrors the other host examples.

    Returns:
        The route handlers, in registration order.
    """
    del recorder  # The handlers reach it through the container, never directly.

    @get("/health")
    async def health(request: Request) -> dict[str, str]:
        """Answer without touching the request scope, so startup is observable."""
        del request
        return {"status": "ok"}

    @get("/hello")
    async def hello(request: Request) -> dict[str, str]:
        """The ordinary case: a singleton and a request-scoped service."""
        scope = current_scope(request)
        greeting = await scope.resolve(Greeting)
        unit = await scope.resolve(RequestUnit)
        return {"message": greeting.greet("world"), "request_id": unit.request_id}

    @get("/stream")
    async def stream(request: Request) -> Stream:
        """The case that makes the close point measurable: three chunks, then a task."""
        scope = current_scope(request)
        unit = await scope.resolve(RequestUnit)
        recorder = await scope.resolve(Recorder)

        async def chunks() -> Any:
            """Yield the chunks, recording each one so the order is legible."""
            for index in range(3):
                recorder.record(f"chunk:{index}")
                yield f"{index}:{unit.request_id}\n"

        async def audit() -> None:
            """Record that a background task still sees the unit open (LIT-2)."""
            recorder.record("background")

        return Stream(chunks(), background=BackgroundTask(audit))

    @get("/echo")
    async def echo(request: Request) -> dict[str, Any]:
        """The value token in action: the service holds the ASGI scope it was built in."""
        unit = await current_scope(request).resolve(RequestUnit)
        return {"path": unit.scope.get("path"), "request_id": unit.request_id}

    @get("/scope")
    async def scope_report(request: Request) -> dict[str, Any]:
        """T11-04: the host store holds a live ``ResolutionScope``, not ``None``."""
        scope = current_scope(request)
        return {
            "is_resolution_scope": isinstance(scope, ResolutionScope),
            "closed": scope.closed,
            "state_keys": sorted(request.scope.get("state", {})),
        }

    @get("/sync", sync_to_thread=True)
    def sync_handler(request: Request) -> dict[str, str]:
        """A ``def`` handler: measured on Litestar's portal thread (LIT-5, T11-12)."""
        del request
        return {"thread": threading.current_thread().name}

    @get("/teardown-error")
    async def teardown_error(request: Request) -> dict[str, bool]:
        """A failing ``@pre_destruct`` is reported, never raised (IC-8)."""
        await current_scope(request).resolve(DoomedTeardown)
        return {"ok": True}

    @get("/boom")
    async def boom(request: Request) -> dict[str, bool]:
        """Raise after resolving, so the error path's close count is observable."""
        await current_scope(request).resolve(RequestUnit)
        raise RuntimeError("handler exploded on purpose")

    return [health, hello, stream, echo, scope_report, sync_handler, teardown_error, boom]


def _middleware(container: DIContainer, report: Any) -> litestar_di.DIMiddleware:
    """Build the one middleware instance this app owns.

    Args:
        container: The container this app owns.
        report: Where teardown failures go (IC-8).

    Returns:
        The middleware, which also carries the close log the tests read.
    """
    return litestar_di.DIMiddleware(container, report=report)


def create_app(
    *,
    report: Any = None,
    hooks_only: bool = False,
) -> Example:
    """Build one complete example: recorder, container, app and adapter.

    Args:
        report: Where teardown failures go; defaults to the recorder's log, which
            is how this example reports (IC-8).
        hooks_only: Build the hooks-only variant (LIT-3).

    Returns:
        The example, including the middleware instance so its close log is
        reachable from a test.
    """
    recorder = Recorder()
    container = create_container(recorder)
    adapter = _middleware(container, report or recorder.report)

    if hooks_only:
        app = Litestar(
            route_handlers=_handlers(recorder),
            middleware=[],
            **litestar_di.hooks_only(container, report=report or recorder.report),
        )
    else:
        app = Litestar(
            route_handlers=_handlers(recorder),
            middleware=[adapter],
            **litestar_di.lifespan_hooks(container, report=report or recorder.report),
        )
    return Example(recorder=recorder, container=container, app=app, adapter=adapter)


example = create_app()
"""The module-level app, for ``litestar --app litestar_app:app run``."""

app = example.app
"""The Litestar application the server runs."""

container = example.container
"""The one container of the module-level app; started by ``on_startup``."""


def demo() -> int:
    """Exercise the app in-process and print the measured close point.

    Returns:
        The process exit code: 0 when every step behaved.
    """
    # Imported here so the module stays importable without the test client's
    # transitive dependency on httpx.
    from litestar.testing import TestClient

    built = create_app()
    print("lauren_di + litestar — one container, one unit per request\n")
    with TestClient(built.app) as client:
        print("after on_startup:", built.recorder.events)
        for path in ("/health", "/hello", "/echo", "/sync", "/teardown-error"):
            response = client.get(path)
            print(f"GET {path:16} -> {response.status_code} {response.text.strip()[:64]}")
        print("\nGET /stream (the close point is recorded by the middleware):")
        print("  " + client.get("/stream").text.strip().replace("\n", "\n  "))
    for close in built.adapter.closes:
        print(f"  closed {close.path:18} after {close.body_messages} body message(s)")
    print("\nevent order:")
    for event in built.recorder.events:
        print(f"  {event}")
    print("\nthe streamed body finished before its unit closed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(demo())
