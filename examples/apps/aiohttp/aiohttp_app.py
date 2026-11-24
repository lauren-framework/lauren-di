"""``lauren_di`` hosted in aiohttp: one container, one unit of work per request.

Run it::

    python examples/apps/aiohttp/aiohttp_app.py

The interesting part on aiohttp is the shape of a route: the handler writes the body
itself (``prepare`` -> ``write`` -> ``write_eof``), because only then is the
middleware's close at the transport's finished point (§5.5, AIO-4). The one route that
deliberately breaks that rule exists to measure what happens when you do (AIO-3).
"""

from __future__ import annotations

import sys
from pathlib import Path

if __name__ == "__main__":  # a direct run must still find the repo and the shared files
    for _parent in (Path(__file__).resolve().parents[1], Path(__file__).resolve().parents[3]):
        if str(_parent) not in sys.path:
            sys.path.insert(0, str(_parent))

import asyncio
import json
import threading
from dataclasses import dataclass
from typing import Annotated
from uuid import uuid4

from _host import build_container
from aiohttp import web
from aiohttp_di import AIOHTTP_REQUEST_TOKEN, CLOSES, SCOPE_KEY, Closed, current_scope, install
from lauren_di import (
    DIContainer,
    Inject,
    ResolutionScope,
    Scope,
    injectable,
    post_construct,
    pre_destruct,
)


@injectable()
class Recorder:
    """The example's event log, one per app, resolved through the container."""

    def __init__(self) -> None:
        self.events: list[str] = []

    def record(self, tag: str) -> None:
        """Append one event."""
        self.events.append(tag)

    def report_errors(self, errors: tuple[object, ...] | None = None) -> None:
        """Receive teardown failures: reported, never raised (IC-8)."""
        for error in errors or ():
            self.events.append(f"report:{type(error).__name__}")


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
    """The request-scoped service: disposed at the middleware's close point."""

    def __init__(
        self,
        recorder: Recorder,
        request: Annotated[web.Request, Inject(AIOHTTP_REQUEST_TOKEN)],
    ) -> None:
        self.recorder = recorder
        self.request = request
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
    """A service whose teardown raises: the adapter reports it, the response lives."""

    def __init__(self, recorder: Recorder) -> None:
        self.recorder = recorder

    @pre_destruct
    async def _closed(self) -> None:
        self.recorder.record("doomed:teardown")
        raise RuntimeError("teardown exploded on purpose")


@dataclass
class Example:
    """Everything one app owns: the recorder, the container and the aiohttp app."""

    recorder: Recorder
    container: DIContainer
    app: web.Application

    @property
    def closes(self) -> list[Closed]:
        """Every close the middleware performed, in order."""
        return self.app[CLOSES]


def create_container(recorder: Recorder) -> DIContainer:
    """Build the one container this app owns (``strict_request_scope=True``)."""

    @injectable(provides=[Recorder])
    def _recorder() -> Recorder:
        return recorder

    container = build_container(value_tokens=(AIOHTTP_REQUEST_TOKEN,))
    container.register(_recorder)
    container.register(DefaultGreeting)
    container.register(RequestUnit)
    container.register(DoomedTeardown)
    return container


async def _own_body(request: web.Request, lines: list[bytes]) -> web.StreamResponse:
    """Write ``lines`` as the response body, one chunk at a time, from the handler."""
    response = web.StreamResponse(headers={"content-type": "text/plain"})
    await response.prepare(request)
    for line in lines:
        await response.write(line)
    await response.write_eof()
    return response


def build_app(recorder: Recorder, container: DIContainer) -> web.Application:
    """Build the aiohttp app: the adapter's middleware, the routes, the signals."""
    app = web.Application()
    install(app, container, report=recorder.report_errors)

    async def record_prepare(request: web.Request, response: web.StreamResponse) -> None:
        """Recorded to show where aiohttp is about to write headers — and what is left.

        For an owned body this fires inside the handler; for a returned ``web.Response``
        it fires after the middleware already closed the unit (AIO-3), which is exactly
        why every other route here owns its body.
        """
        recorder.record("response-prepare")

    app.on_response_prepare.append(record_prepare)

    async def health(request: web.Request) -> web.StreamResponse:
        return await _own_body(request, [b'{"status":"ok"}'])

    async def hello(request: web.Request) -> web.StreamResponse:
        scope = current_scope(request)
        greeting = await scope.resolve(Greeting)
        unit = await scope.resolve(RequestUnit)
        payload = f'{{"message":"{greeting.greet("world")}","request_id":"{unit.request_id}"}}'
        return await _own_body(request, [payload.encode()])

    async def stream(request: web.Request) -> web.StreamResponse:
        """Three body chunks: the case that makes the close point measurable."""
        scope = current_scope(request)
        unit = await scope.resolve(RequestUnit)
        recorder = await scope.resolve(Recorder)
        response = web.StreamResponse(headers={"content-type": "text/plain"})
        await response.prepare(request)
        for index in range(3):
            await response.write(f"{index}:{unit.request_id}\n".encode())
            recorder.record(f"chunk:{index}")
        await response.write_eof()
        recorder.record("body:done")
        return response

    async def echo(request: web.Request) -> web.StreamResponse:
        """The value token in action: the service holds the ``web.Request`` itself."""
        unit = await current_scope(request).resolve(RequestUnit)
        payload = f'{{"path":"{unit.request.path}","method":"{unit.request.method}"}}'
        return await _own_body(request, [payload.encode()])

    async def scope_report(request: web.Request) -> web.StreamResponse:
        """T11-04 / AIO-4: the host store ``request["di"]`` holds a live scope.

        Read straight out of the request mapping — not through :func:`current_scope` —
        so the test asserts what the host store contains: a ``ResolutionScope``, not
        ``None``, and the same object the handler resolves through.
        """
        stored = request.get(SCOPE_KEY)
        scope = current_scope(request)
        payload = json.dumps(
            {
                "is_resolution_scope": isinstance(stored, ResolutionScope),
                "is_the_one_the_handler_uses": stored is scope,
                "closed": scope.closed,
            }
        )
        return await _own_body(request, [payload.encode()])

    def sync_handler(request: web.Request) -> web.StreamResponse:
        """T11-12 / AIO-5: a ``def`` handler runs inline, on the loop thread.

        aiohttp accepts a plain function and simply calls it, so ``MainThread`` here
        is not a name for "a thread pool": the handler is blocking the event loop for
        as long as it runs. Recorded because the page asks what a sync route costs.
        """
        del request
        payload = f'{{"thread":"{threading.current_thread().name}"}}'
        return web.Response(text=payload, content_type="application/json")

    async def background(request: web.Request) -> web.StreamResponse:
        """T11-10 / AIO-2: work that runs after the body but while the unit is open.

        aiohttp has no per-response background callable, so "background" here is the
        deterministic shape: the handler writes the body itself and *then* does the
        extra work before returning. The middleware has not closed yet, so the unit
        is open — the opposite of Sanic's ``app.add_task``, which runs after the close.
        """
        scope = current_scope(request)
        unit = await scope.resolve(RequestUnit)
        recorder = await scope.resolve(Recorder)
        response = await _own_body(request, [b'{"delivered":true}'])
        recorder.record(f"background:closed={unit.closed}")
        return response

    async def teardown_error(request: web.Request) -> web.StreamResponse:
        await current_scope(request).resolve(DoomedTeardown)
        return await _own_body(request, [b'{"ok":true}'])

    async def unwritten(request: web.Request) -> web.StreamResponse:
        """The anti-pattern, kept on purpose: aiohttp writes this body after we close."""
        await current_scope(request).resolve(RequestUnit)
        return web.Response(text="written by aiohttp after the middleware")

    async def boom(request: web.Request) -> web.StreamResponse:
        """A failing handler still closes its unit exactly once."""
        await current_scope(request).resolve(RequestUnit)
        raise web.HTTPBadRequest(text="handler refused")

    app.add_routes(
        [
            web.get("/health", health),
            web.get("/hello", hello),
            web.get("/stream", stream),
            web.get("/echo", echo),
            web.get("/scope", scope_report),
            web.get("/sync", sync_handler),
            web.get("/background", background),
            web.get("/teardown-error", teardown_error),
            web.get("/unwritten", unwritten),
            web.get("/boom", boom),
        ]
    )
    return app


def create_app() -> Example:
    """Build one complete example: recorder, container and the aiohttp application."""
    recorder = Recorder()
    container = create_container(recorder)
    return Example(recorder=recorder, container=container, app=build_app(recorder, container))


async def demo() -> Example:
    """Serve the app on a real socket and print what each request measured."""
    from aiohttp.test_utils import TestClient, TestServer

    example = create_app()
    client = TestClient(TestServer(example.app))
    await client.start_server()
    try:
        for path in ("/health", "/hello", "/stream", "/echo", "/teardown-error", "/unwritten"):
            response = await client.get(path)
            body = await response.text()
            print(f"{path:16} {response.status} body={body[:48]!r}")
        for closed in example.closes:
            print(f"closed {closed.path:16} after_body={closed.after_body} errors={closed.errors}")
        print(f"app events: {example.recorder.events}")
    finally:
        await client.close()
    return example


if __name__ == "__main__":
    asyncio.run(demo())
