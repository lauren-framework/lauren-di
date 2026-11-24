"""``lauren_di`` hosted in Quart: one container, one unit per request (PRD 11 §5.3).

Run it::

    python examples/apps/quart/quart_app.py      # in-process self-check
    hypercorn quart_app:app --bind 127.0.0.1:8000

The app object is served through :func:`quart_di.install`, which wraps
``app.asgi_app``. Handlers read their scope from ``g.di`` and resolve through it;
the framework's own ``teardown_request`` is deliberately not used, because it runs
before the body (QRT-2).
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
from quart import Quart, jsonify, request
from quart_di import (
    QUART_REQUEST_TOKEN,
    DIWrapper,
    conversation,
    current_scope,
    install,
    install_teardown_owner,
    shutdown,
    startup,
)


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
        scope: Annotated[Any, Inject(QUART_REQUEST_TOKEN)],
    ) -> None:
        """Build the request-scoped service.

        Args:
            recorder: The app's event log.
            scope: The ASGI scope, supplied by the wrapper through ``values=`` (IC-6).
        """
        self.recorder = recorder
        self.scope = scope
        self.request_id = uuid4().hex[:8]
        self.closed = False

    @post_construct
    async def _opened(self) -> None:
        """Record the unit opening."""
        self.recorder.record(f"unit:open:{self.request_id}")

    @pre_destruct
    async def _closed(self) -> None:
        """Record the unit closing, and mark it closed for the streamed chunk."""
        self.closed = True
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
    """Everything one app owns: the recorder, the container, the app and the wrapper."""

    recorder: Recorder
    container: DIContainer
    app: Quart
    wrapper: DIWrapper | None
    """The installed wrapper, or ``None`` for the negative control that has none."""


def create_container(recorder: Recorder) -> DIContainer:
    """Build the one container this app owns.

    Args:
        recorder: The app's event log, registered as an instance so providers can
            record ordering facts.

    Returns:
        A container the app's ``before_serving`` hook starts (IC-1).
    """

    @injectable(provides=[Recorder])
    def _recorder() -> Recorder:
        """Hand the container the recorder this app was built with."""
        return recorder

    container = build_container(value_tokens=(QUART_REQUEST_TOKEN,))
    container.register(_recorder)
    container.register(DefaultGreeting)
    container.register(RequestUnit)
    container.register(DoomedTeardown)
    return container


def _routes(app: Quart) -> None:
    """Register this app's routes on ``app``.

    Args:
        app: The Quart application to populate.
    """

    @app.get("/health")
    async def health() -> Any:
        """Answer without touching the scope, so startup is observable."""
        return jsonify({"status": "ok"})

    @app.get("/hello")
    async def hello() -> Any:
        """The ordinary case: a singleton and a request-scoped service."""
        scope = current_scope()
        greeting = await scope.resolve(Greeting)
        unit = await scope.resolve(RequestUnit)
        return jsonify({"message": greeting.greet("world"), "request_id": unit.request_id})

    @app.get("/stream")
    async def stream() -> Any:
        """The case that makes the close point measurable.

        Quart streams an async generator returned from a view, so the wrapper's
        close lands after the last chunk it yields — and ``unit.closed`` is read
        *inside* that generator, which is what makes a wrong owner visible.
        """
        scope = current_scope()
        unit = await scope.resolve(RequestUnit)
        recorder = await scope.resolve(Recorder)

        async def chunks() -> Any:
            """Yield three chunks, recording the service's live closed flag."""
            for index in range(3):
                recorder.record(f"chunk:{index}:closed={unit.closed}")
                yield f"{index}:{unit.request_id} closed={unit.closed}\n"

        async def audit() -> None:
            """Record that a background task still sees the unit open (QRT-2)."""
            recorder.record(f"background:closed={unit.closed}")

        app.add_background_task(audit)
        return chunks()

    @app.get("/echo")
    async def echo() -> Any:
        """The value token in action: the service holds the ASGI scope it was built in."""
        unit = await current_scope().resolve(RequestUnit)
        return jsonify({"path": unit.scope.get("path"), "request_id": unit.request_id})

    @app.get("/scope")
    async def scope_report() -> Any:
        """T11-04 / QRT-4: ``g.di`` holds a live ``ResolutionScope``, not ``None``."""
        from quart import g

        scope = current_scope()
        return jsonify(
            {
                "is_resolution_scope": isinstance(scope, ResolutionScope),
                "closed": scope.closed,
                "g_keys": sorted(key for key in vars(g) if not key.startswith("_")),
            }
        )

    @app.get("/sync")
    def sync_handler() -> Any:
        """A ``def`` view: run on a worker thread, never the event loop (QRT-5, T11-12)."""
        assert request.path == "/sync", "a sync view sees the same request context"
        return jsonify({"thread": threading.current_thread().name})

    @app.get("/teardown-error")
    async def teardown_error() -> Any:
        """A failing ``@pre_destruct`` is reported, never raised (IC-8)."""
        await current_scope().resolve(DoomedTeardown)
        return jsonify({"ok": True})

    @app.get("/boom")
    async def boom() -> Any:
        """Raise after resolving, so the error path's close count is observable."""
        await current_scope().resolve(RequestUnit)
        raise RuntimeError("handler exploded on purpose")


def create_app(*, report: Any = None, teardown_owner: bool = False) -> Example:
    """Build one complete example: recorder, container, app, wrapper.

    Args:
        report: Where teardown failures go; defaults to the recorder's log (IC-8).
        teardown_owner: Install the *wrong* owner instead, so its cost is
            measurable (QRT-2, T11-06).

    Returns:
        The example, including the wrapper so its close log is reachable.
    """
    recorder = Recorder()
    container = create_container(recorder)
    app = Quart("lauren_di_quart_example")
    callback = report or recorder.report

    if teardown_owner:
        install_teardown_owner(app, container, report=callback)
        wrapper: DIWrapper | None = None
        """No wrapper here on purpose: the framework's teardown hook is the owner,
        which is exactly the trap this variant exists to make measurable (T11-06)."""
    else:
        wrapper = install(app, container, report=callback)

    _routes(app)
    return Example(recorder=recorder, container=container, app=app, wrapper=wrapper)


example = create_app()
"""The module-level app, for ``hypercorn quart_app:app``."""

app = example.app
"""The Quart application the server runs; its ``asgi_app`` is the adapter's wrapper."""

container = example.container
"""The one container of the module-level app; started by ``before_serving``."""


async def demo() -> int:
    """Exercise the app in-process through the ASGI seam and print the measurement.

    Returns:
        The process exit code: 0 when every step behaved.
    """
    built = create_app()
    print("lauren_di + quart — one container, one unit per request\n")
    await startup(built.app)
    try:
        print("after before_serving:", built.recorder.events)
        for path in ("/health", "/hello", "/echo", "/sync", "/teardown-error"):
            call = await conversation(built.app.asgi_app, path)
            print(f"GET {path:16} -> {call.status} {call.body.decode().strip()[:64]}")
        print("\nGET /stream (the close point is recorded by the wrapper):")
        shown = await conversation(built.app.asgi_app, "/stream")
        print("  " + shown.body.decode().strip().replace("\n", "\n  "))
    finally:
        await shutdown(built.app)
    for close in built.wrapper.closes:
        print(f"  closed {close.path:18} after {close.body_messages} body message(s)")
    print("\nevent order:")
    for event in built.recorder.events:
        print(f"  {event}")
    print("\nevery streamed chunk observed an open unit.")
    return 0


if __name__ == "__main__":
    import asyncio

    raise SystemExit(asyncio.run(demo()))
