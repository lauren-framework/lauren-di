"""``lauren_di`` hosted in Sanic: one container, one unit per request (PRD 11 §5.4).

Run it::

    python examples/apps/sanic/sanic_app.py          # in-process ASGI self-check
    hypercorn sanic_app:asgi --bind 127.0.0.1:8000   # the real thing

Both go through :class:`sanic_di.DIASGIApp`, because on Sanic the close point is the
server seam (§5.4). Handlers never reach for the container: they take their scope from
``request.ctx.di`` and resolve through it.
"""

from __future__ import annotations

import sys
from pathlib import Path

if __name__ == "__main__":  # a direct run must still find the repo and the shared files
    for _parent in (Path(__file__).resolve().parents[1], Path(__file__).resolve().parents[3]):
        if str(_parent) not in sys.path:
            sys.path.insert(0, str(_parent))

import asyncio
import threading
from dataclasses import dataclass
from itertools import count
from typing import Annotated, Any
from uuid import uuid4

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
from sanic import Sanic, response
from sanic.request import Request
from sanic.response import HTTPResponse
from sanic_di import (
    SANIC_SCOPE_TOKEN,
    Conversation,
    DIASGIApp,
    asgi_conversation,
    asgi_lifespan,
    current_scope,
    install,
)

_NAMES = count(1)


@injectable()
class Recorder:
    """The example's event log: providers and routes append to it.

    A container-scoped singleton, so the log is part of the object graph the example
    is demonstrating rather than a module global.
    """

    def __init__(self) -> None:
        self.events: list[str] = []

    def record(self, tag: str) -> None:
        """Append one event."""
        self.events.append(tag)


class Greeting:
    """The abstraction the app resolves; the container picks the implementation."""

    def greet(self, name: str) -> str:
        """Say hello."""
        raise NotImplementedError


@injectable(provides=[Greeting])
class DefaultGreeting(Greeting):
    """A container-scoped provider: built once, reused by every request."""

    def __init__(self, recorder: Recorder) -> None:
        self.recorder = recorder

    def greet(self, name: str) -> str:
        """Say hello."""
        return f"hello, {name}"

    @post_construct
    async def _started(self) -> None:
        self.recorder.record("greeting:start")

    @pre_destruct
    async def _stopped(self) -> None:
        self.recorder.record("greeting:stop")


@injectable(scope=Scope.REQUEST)
class RequestUnit:
    """The request-scoped service: one instance per unit, disposed at the close point."""

    def __init__(
        self,
        recorder: Recorder,
        scope: Annotated[Any, Inject(SANIC_SCOPE_TOKEN)],
    ) -> None:
        self.recorder = recorder
        self.scope = scope
        self.request_id = uuid4().hex[:8]
        self.closed = False

    @post_construct
    async def _opened(self) -> None:
        self.recorder.record(f"unit:open:{self.request_id}")

    @pre_destruct
    async def _closed(self) -> None:
        self.closed = True
        self.recorder.record(f"unit:close:{self.request_id}")


@injectable(scope=Scope.REQUEST)
class DoomedTeardown:
    """A service whose teardown raises: the adapter reports it and the response lives."""

    def __init__(self, recorder: Recorder) -> None:
        self.recorder = recorder

    @pre_destruct
    async def _closed(self) -> None:
        self.recorder.record("doomed:teardown")
        raise RuntimeError("teardown exploded on purpose")


@dataclass
class Example:
    """Everything one app owns: the container, the recorder, the app and its seam."""

    recorder: Recorder
    container: DIContainer
    app: Sanic
    asgi: DIASGIApp


def create_container(recorder: Recorder) -> DIContainer:
    """Build the one container this app owns (``strict_request_scope=True``)."""

    @injectable(provides=[Recorder])
    def _recorder() -> Recorder:
        return recorder

    container = build_container(value_tokens=(SANIC_SCOPE_TOKEN,))
    container.register(_recorder)
    container.register(DefaultGreeting)
    container.register(RequestUnit)
    container.register(DoomedTeardown)
    return container


def build_app(container: DIContainer, *, name: str | None = None) -> Sanic:
    """Build the Sanic app around ``container``: routes plus the adapter's middleware."""
    app = Sanic(name or f"lauren_di_sanic_example_{next(_NAMES)}")
    install(app, container)

    @app.on_response
    async def record_response_middleware(request: Request, resp: HTTPResponse) -> HTTPResponse:
        """Recorded only to show *where* it runs.

        A response middleware looks like the natural close point and is not one: it
        runs before the body is written, so a unit closed here would be disposed while
        the response is still streaming (SNC-2). The event log proves the order.
        """
        recorder = await current_scope(request).resolve(Recorder)
        recorder.record("response-middleware")
        return resp

    @app.get("/health")
    async def health(request: Request) -> HTTPResponse:
        return response.json({"status": "ok"})

    @app.get("/hello")
    async def hello(request: Request) -> HTTPResponse:
        """The ordinary case: a singleton, a request-scoped service, nothing ambient."""
        scope = current_scope(request)
        greeting = await scope.resolve(Greeting)
        unit = await scope.resolve(RequestUnit)
        return response.json({"message": greeting.greet("world"), "request_id": unit.request_id})

    @app.get("/stream")
    async def stream(request: Request) -> HTTPResponse:
        """The case that makes the close point measurable: the handler owns the body."""
        scope = current_scope(request)
        unit = await scope.resolve(RequestUnit)
        recorder = await scope.resolve(Recorder)
        resp = await request.respond(content_type="text/plain")
        for index in range(3):
            recorder.record(f"chunk:{index}")
            await resp.send(f"{index}:{unit.request_id}\n")
        await resp.eof()
        return resp

    @app.get("/echo")
    async def echo(request: Request) -> HTTPResponse:
        """The value token in action: the request-scoped service holds the ASGI scope."""
        unit = await current_scope(request).resolve(RequestUnit)
        return response.json({"path": unit.scope.get("path"), "request_id": unit.request_id})

    @app.get("/scope")
    async def scope_report(request: Request) -> HTTPResponse:
        """T11-04 / SNC-3: the host store ``request.ctx.di`` holds a live scope.

        Read straight out of ``request.ctx`` — not through :func:`current_scope`, which
        would only prove the helper works — so the test can assert what the host store
        itself contains: a ``ResolutionScope``, not ``None``, and the same object the
        handler resolves through.
        """
        stored = getattr(request.ctx, "di", None)
        scope = current_scope(request)
        return response.json(
            {
                "is_resolution_scope": isinstance(stored, ResolutionScope),
                "is_the_one_the_handler_uses": stored is scope,
                "closed": scope.closed,
            }
        )

    @app.get("/sync")
    def sync_handler(request: Request) -> HTTPResponse:
        """T11-12 / SNC-4: a ``def`` handler is measured on ``MainThread``.

        Sanic runs a synchronous handler inline on the event loop, so this route is
        the measurement the page records rather than a recommendation: an example
        that blocks here blocks every other request (SNC-4).
        """
        del request
        return response.json({"thread": threading.current_thread().name})

    @app.get("/background")
    async def background(request: Request) -> HTTPResponse:
        """T11-10: Sanic's own background API, measured where it actually runs.

        ``app.add_task`` is scheduled from the handler and runs *after* the response
        has been sent, which on this host is after the ASGI seam closed the unit: the
        recorded flag is ``closed=True``. The page's row expects an open unit, so this
        route measures the difference instead of hiding it (see the README).
        """
        scope = current_scope(request)
        unit = await scope.resolve(RequestUnit)
        recorder = await scope.resolve(Recorder)

        async def audit() -> None:
            """Record what the background task saw when it eventually ran."""
            recorder.record(f"background:closed={unit.closed}")

        request.app.add_task(audit())
        return response.json({"scheduled": True})

    @app.get("/stream-none")
    async def stream_none(request: Request) -> None:
        """T11-15 / SNC-1: a handler that streams and returns ``None`` is supported.

        ``request.respond()`` writes the body; returning ``None`` afterwards is what
        Sanic asks for, and the test asserts no ``will not be sent to client`` error
        reaches the log for this route.
        """
        scope = current_scope(request)
        unit = await scope.resolve(RequestUnit)
        recorder = await scope.resolve(Recorder)
        resp = await request.respond(content_type="text/plain")
        for index in range(3):
            recorder.record(f"chunk:{index}")
            await resp.send(f"{index}:{unit.request_id}\n")
        await resp.eof()

    @app.get("/boom")
    async def boom(request: Request) -> HTTPResponse:
        """T11-14: raise after resolving, so the error path's close count is visible."""
        await current_scope(request).resolve(RequestUnit)
        raise RuntimeError("handler exploded on purpose")

    @app.get("/teardown-error")
    async def teardown_error(request: Request) -> HTTPResponse:
        """A failing ``@pre_destruct`` is captured and reported, not raised (IC-8)."""
        await current_scope(request).resolve(DoomedTeardown)
        return response.json({"ok": True})

    return app


def create_app(*, name: str | None = None) -> Example:
    """Build one complete example: recorder, container, Sanic app and ASGI seam."""
    recorder = Recorder()
    container = create_container(recorder)
    app = build_app(container, name=name)
    return Example(recorder=recorder, container=container, app=app, asgi=DIASGIApp(app, container))


example = create_app()
"""The module-level app; its container starts on lifespan startup, before it serves."""

container = example.container
"""The one container of the module-level app."""

app = example.app
"""The Sanic app, served through the ASGI wrapper below."""

asgi = example.asgi
"""The callable to hand the server: ``hypercorn sanic_app:asgi``."""


async def demo() -> list[Conversation]:
    """Exercise the app in-process through the ASGI seam and print the measurements."""
    calls: list[Conversation] = []
    async with asgi_lifespan(asgi):
        for path in ("/health", "/hello", "/stream", "/echo", "/teardown-error"):
            call = await asgi_conversation(asgi, path, snapshot=lambda: list(asgi.events))
            calls.append(call)
            print(f"{path:16} {call.status} body={call.body[:48]!r}")
            print(f"{'':16} seam at last body message: {call.at_body_end}")
        print(f"seam after the calls: {asgi.events}")
        print(f"reported teardown failures: {[type(e).__name__ for e in asgi.reported]}")
        print(f"reported instead of raised: {calls[-1].status} (expected 200)")
        print(f"app events: {example.recorder.events}")
    return calls


if __name__ == "__main__":
    asyncio.run(demo())
