"""One conformance adapter per installed host framework (PRD 09 §5.10).

The suite is *parametrised by adapter, not by framework*: every driver here
presents the same four operations — ``start``, ``request``, ``stop``,
``release`` — so the twelve invariants are written once in ``conformance.py``
and every framework answers the same questions.

A driver is deliberately thin. It does not re-implement the framework: it
loads the example's own adapter module (``examples/apps/<app>/<app>_di.py``)
and drives it through the seam that adapter installs —

======  ===================================================================
shape   seam
======  ===================================================================
asgi    the pure ASGI middleware (``DIScopeMiddleware``, ``DIWrapper``,
        ``DIASGIApp``, Litestar's ``ASGIMiddleware``), fed a real ASGI
        conversation that resolves through ``scope["state"]``
wsgi    the WSGI callable (``WSGIAdapter.wrap`` / ``DIMiddleware``), fed an
        ``environ`` and consumed one chunk at a time, then ``close()``d
cli     ``CliHost.invocation``: one process invocation is one unit
http    aiohttp's ``install`` driven through ``aiohttp.test_utils.TestClient``
======  ===================================================================

``django`` is not driven here: its adapter keeps the container on the
``AppConfig`` that ``ready()`` populated, so substituting the probe container
would mean booting a second Django project. Its invariants are measured by
``examples/apps/django/tests/test_django.py`` (DJ-1 … DJ-22, T13-01 … T13-16),
and :func:`exclusions` states that in one machine-checkable place.
"""

from __future__ import annotations

import asyncio
import contextlib
import importlib
import importlib.metadata
import io
import pathlib
import sys
from dataclasses import dataclass, field
from types import ModuleType
from typing import Any

import probe
import pytest
from lauren_di import DestructError, ResolutionScope, Token

ROOT = pathlib.Path(__file__).resolve().parents[3]
EXAMPLES = ROOT / "examples"
APPS = EXAMPLES / "apps"

for _path in (ROOT, APPS):
    if str(_path) not in sys.path:
        sys.path.append(str(_path))

import _host  # noqa: E402  (the shared reference adapter)

#: Any framework type in a driver is imported here, so a missing package is a
#: skip rather than an ImportError at collection time (D-62).
SKIP_REASON = (
    "{name} is not installed, so this adapter is skipped (PRD 09 §5.10, D-62). "
    "Install it with `pip install -r examples/apps/{app}/requirements.txt`."
)


@dataclass
class UnitResult:
    """What one unit of work observed: the raw material of the twelve checks."""

    first: Any = None
    second: Any = None
    scope: ResolutionScope | None = None
    body: list[bytes] = field(default_factory=list)
    host_value: Any = None
    errors: tuple[Any, ...] = ()
    events: list[str] = field(default_factory=list)
    exit_code: int = 0
    container_instance: Any = None


def _load(app: str, module: str) -> ModuleType:
    """Import ``module`` from ``examples/apps/<app>/`` (appended, never inserted)."""
    directory = APPS / app
    if str(directory) not in sys.path:
        sys.path.append(str(directory))
    return importlib.import_module(module)


class Driver:
    """The four operations every conformance adapter offers."""

    #: ``"asgi"``, ``"wsgi"``, ``"cli"`` or ``"http"``.
    shape = ""
    #: ``"container"`` when the process owns one container across units,
    #: ``"invocation"`` when the host is one command per process (CLI).
    lifecycle = "container"
    #: ``True`` when every container call runs on one long-lived loop (IC-10).
    single_loop = True

    def __init__(self, app: str, distribution: str) -> None:
        """Reset the probe trace and build the (unstarted) probe container."""
        probe.reset()
        self.app = app
        self.distribution = distribution
        self.container = probe.build()
        self.host_token: Any = Token(f"conformance.{app}.host")
        self.events = probe.EVENTS
        self.reported: list[Any] = []
        self._bridge: _host.SyncBridge | None = None

    # -- operations the invariants call ------------------------------------
    def start(self) -> None:
        """Start the host's process container (IC-1)."""
        raise NotImplementedError

    def stop(self) -> tuple[DestructError, ...]:
        """Stop it and return the teardown tuple (IC-9)."""
        raise NotImplementedError

    def request(self, *, fragile: bool = False) -> UnitResult:
        """Serve exactly one unit of work and return its observations."""
        raise NotImplementedError

    def release(self) -> None:
        """Give back whatever the driver owns (a loop, a server). Never raises."""
        if self._bridge is not None:
            self._bridge.close()
            self._bridge = None

    # -- helpers -----------------------------------------------------------
    def run(self, awaitable: Any) -> Any:
        """Run one container coroutine on the loop this host shape uses."""
        if self._bridge is not None:
            return self._bridge.run(awaitable)
        return asyncio.run(awaitable)

    def make_bridge(self) -> _host.SyncBridge:
        """Create the one loop a synchronous host keeps open (IC-10)."""
        self._bridge = _host.SyncBridge()
        return self._bridge

    def resolve_on_container(self, token: Any) -> Any:
        """Resolve on the *container* — the IC-5 hazard the adapters must avoid."""
        return self.run(self.container.resolve(token))

    def tear_down_quietly(self) -> None:
        """Best-effort cleanup for a test that failed mid-adapter."""
        with contextlib.suppress(Exception):
            self.stop()


class WsgiDriver(Driver):
    """A WSGI host: one ``environ`` per request, closed on the response iterable."""

    shape = "wsgi"

    def __init__(self, app: str, distribution: str) -> None:
        """Wrap a minimal WSGI app with the example's own middleware."""
        super().__init__(app, distribution)
        self.module = _load(app, f"{app}_di")
        self.bridge = self.make_bridge()
        self._result: dict[str, Any] = {}
        self._environ: dict[str, Any] = {}
        self._host_value: Any = None
        self._fragile = False
        self.host_token = getattr(self.module, "ENVIRON", self.host_token)
        self._expected = Token(f"conformance.{app}.expected")
        self.wsgi_app = self._minimal_app()
        if hasattr(self.module, "WSGIAdapter"):
            adapter = self.module.WSGIAdapter(self.container, self.bridge)
            self._resolve = adapter.resolve
            self.callable = adapter.wrap(self.wsgi_app, report=self.reported.extend)
        else:
            adapter = self.module.DIMiddleware(
                self.wsgi_app,
                container=self.container,
                bridge=self.bridge,
                values=lambda environ: {self._expected: environ},
                on_close=lambda _unit, errors: self.reported.extend(errors),
            )
            self._resolve = self._resolve_through_environ
            self.callable = adapter
            self.host_token = self._expected

    def _resolve_through_environ(self, environ: dict[str, Any], token: Any) -> Any:
        """Resolve on the scope the middleware published in ``environ`` (IC-4)."""
        return self.module.resolve(self.bridge, environ[_host.SCOPE_KEY], token)

    def _minimal_app(self) -> Any:
        """The downstream WSGI app: resolve twice, then stream three chunks."""
        driver = self

        def app(environ: dict[str, Any], start_response: Any) -> Any:
            driver._result = probe.serve(
                lambda token: driver._resolve(environ, token),
                fragile=driver._fragile,
            )
            driver._host_value = driver._resolve(environ, driver.host_token)
            driver._environ = environ
            start_response("200 OK", [("Content-Type", "text/plain")])
            for _ in range(3):
                probe.record("chunk")
                yield b"chunk"

        return app

    def start(self) -> None:
        """Start the process container on the host's one loop (IC-1, IC-10)."""
        self.bridge.run(self.container.startup())

    def stop(self) -> tuple[DestructError, ...]:
        """Stop the container once; the tuple is the host's to read (IC-9)."""
        return self.bridge.run(self.container.shutdown())

    def request(self, *, fragile: bool = False) -> UnitResult:
        """Serve one request and consume the body to its ``close()`` (IC-7)."""
        start = len(self.events)
        self._fragile = fragile
        environ = {
            "REQUEST_METHOD": "GET",
            "PATH_INFO": "/conformance",
            "SERVER_NAME": "localhost",
            "SERVER_PORT": "80",
            "SCRIPT_NAME": "",
            "QUERY_STRING": "",
            "wsgi.version": (1, 0),
            "wsgi.url_scheme": "http",
            "wsgi.input": io.BytesIO(b""),
            "wsgi.errors": sys.stderr,
            "wsgi.multithread": False,
            "wsgi.multiprocess": False,
            "wsgi.run_once": False,
        }
        body: list[bytes] = []
        iterable = self.callable(environ, lambda *_args: None)
        try:
            for chunk in iterable:
                body.append(chunk)
        finally:
            close = getattr(iterable, "close", None)
            if close is not None:
                close()
        result = self._result
        return UnitResult(
            first=result["first"],
            second=result["second"],
            scope=environ[_host.SCOPE_KEY],
            body=body,
            host_value=self._host_value,
            errors=tuple(self.reported),
            events=self.events[start:],
        )


class AsgiDriver(Driver):
    """An ASGI host: one conversation per unit, closed after the last message."""

    shape = "asgi"
    single_loop = False

    def __init__(self, app: str, distribution: str) -> None:
        """Wrap a minimal ASGI app with the example's own middleware."""
        super().__init__(app, distribution)
        self.module = _load(app, f"{app}_di")
        for attribute in (
            "REQUEST_ID",
            "QUART_REQUEST_TOKEN",
            "LITESTAR_SCOPE_TOKEN",
            "SANIC_SCOPE_TOKEN",
        ):
            if hasattr(self.module, attribute):
                self.host_token = getattr(self.module, attribute)
                break
        self._fragile = False
        self._result: dict[str, Any] = {}
        self._host_value: Any = None
        self._scope: Any = None
        self.app_callable = self._minimal_app()
        self.middleware = self._install()

    def _install(self) -> Any:
        """Build the middleware the example installs, in whatever shape it has."""
        report = self.reported.extend
        if hasattr(self.module, "DIScopeMiddleware"):
            return self.module.DIScopeMiddleware(
                self.app_callable, container=self.container, report=report
            )
        if hasattr(self.module, "DIWrapper"):
            return self.module.DIWrapper(self.app_callable, self.container, report=report)
        if hasattr(self.module, "DIASGIApp"):
            return self.module.DIASGIApp(self.app_callable, self.container, report=report)
        middleware = self.module.DIMiddleware(self.container, report=report)

        async def handle(scope: Any, receive: Any, send: Any) -> None:
            await middleware.handle(scope, receive, send, self.app_callable)

        return handle

    def _resolver(self, scope: dict[str, Any]) -> Any:
        """Return a coroutine resolver bound to this connection's scope (IC-4)."""
        if self.app == "sanic":
            unit = self.module._UNIT.get()
            return unit.scope.resolve
        state = scope["state"]
        key = self.module.SCOPE_KEY
        return state[key].resolve

    def _minimal_app(self) -> Any:
        """The downstream ASGI app: resolve twice, then send three chunks."""
        driver = self

        async def app(scope: dict[str, Any], receive: Any, send: Any) -> None:
            resolver = driver._resolver(scope)
            driver._scope = (
                driver.module._UNIT.get().scope
                if driver.app == "sanic"
                else scope["state"][driver.module.SCOPE_KEY]
            )
            driver._result = await probe.aserve(resolver, fragile=driver._fragile)
            driver._host_value = await resolver(driver.host_token)
            await send({"type": "http.response.start", "status": 200, "headers": []})
            for _ in range(3):
                probe.record("chunk")
                await send({"type": "http.response.body", "body": b"chunk", "more_body": True})
            await send({"type": "http.response.body", "body": b"", "more_body": False})

        return app

    def start(self) -> None:
        """Start the process container (IC-1)."""
        self.run(self.container.startup())

    def stop(self) -> tuple[DestructError, ...]:
        """Stop it and hand the tuple back (IC-9)."""
        return self.run(self.container.shutdown())

    def request(self, *, fragile: bool = False) -> UnitResult:
        """Serve one ASGI conversation to its last body message (IC-7)."""
        start = len(self.events)
        self._fragile = fragile
        sent: list[dict[str, Any]] = []
        box: dict[str, Any] = {}

        async def one() -> None:
            scope = {
                "type": "http",
                "asgi": {"version": "3.0"},
                "http_version": "1.1",
                "method": "GET",
                "path": "/conformance",
                "raw_path": b"/conformance",
                "query_string": b"",
                "headers": [],
                "client": ("127.0.0.1", 1234),
                "server": ("testserver", 80),
                "scheme": "http",
                "state": {},
            }
            box["scope"] = scope

            async def receive() -> dict[str, Any]:
                return {"type": "http.request", "body": b"", "more_body": False}

            async def send(message: dict[str, Any]) -> None:
                sent.append(message)

            await self.middleware(scope, receive, send)

        asyncio.run(one())
        return UnitResult(
            first=self._result["first"],
            second=self._result["second"],
            scope=self._scope,
            body=[m.get("body", b"") for m in sent if m["type"] == "http.response.body"],
            host_value=self._host_value,
            errors=tuple(self.reported),
            events=self.events[start:],
        )


class CliDriver(Driver):
    """A CLI host: one invocation is one unit, and it owns the whole lifecycle."""

    shape = "cli"
    lifecycle = "invocation"
    single_loop = False

    def __init__(self, app: str, distribution: str) -> None:
        """Load the CLI adapter; each request builds one fresh ``CliHost``."""
        super().__init__(app, distribution)
        self.module = _load(app, f"{app}_di")
        self.host_token = Token(f"conformance.{app}.host")

    def start(self) -> None:
        """Nothing to start: the invocation starts the container itself (IC-1)."""

    def stop(self) -> tuple[DestructError, ...]:
        """Nothing left to stop: ``invocation`` already shut the container down."""
        return ()

    def request(self, *, fragile: bool = False) -> UnitResult:
        """Run one command invocation, capturing what it printed to stderr."""
        start = len(self.events)
        host = self.module.CliHost(
            value_tokens=(self.host_token,),
            providers=(probe.Config, probe.Scoped, probe.Closing, probe.Fragile),
        )
        self.container = host.container
        host_object = object()
        captured = io.StringIO()
        exit_code = 0
        scope = None
        result: dict[str, Any] = {}
        host_value = None
        with contextlib.redirect_stderr(captured):
            try:
                with host.invocation(values={self.host_token: host_object}) as unit:
                    scope = unit.scope
                    result = probe.serve(lambda token: host.resolve(unit, token), fragile=fragile)
                    host_value = host.resolve(unit, self.host_token)
                    for _ in range(3):
                        probe.record("chunk")
            except SystemExit as exit_request:
                exit_code = exit_request.code if isinstance(exit_request.code, int) else 1
        lines = tuple(
            line for line in captured.getvalue().splitlines() if line.startswith("destruct_error:")
        )
        return UnitResult(
            first=result.get("first"),
            second=result.get("second"),
            scope=scope,
            body=[line.encode() for line in captured.getvalue().splitlines()],
            host_value=host_value,
            errors=lines,
            events=self.events[start:],
            exit_code=exit_code,
        )


class HttpDriver(Driver):
    """an aiohttp host: the middleware closes after the handler owns its body."""

    shape = "http"

    def __init__(self, app: str, distribution: str) -> None:
        """Install the middleware and signal hooks on a real ``web.Application``."""
        super().__init__(app, distribution)
        from aiohttp import web

        self.module = _load(app, f"{app}_di")
        self.host_token = getattr(self.module, "AIOHTTP_REQUEST_TOKEN", self.host_token)
        self.bridge = self.make_bridge()
        self._fragile = False
        self._scope: Any = None
        self._result: dict[str, Any] = {}
        self._host_value: Any = None
        self._app = web.Application()
        self.module.install(self._app, self.container, report=self.reported.extend)
        self._client: Any = None

        async def handler(request: Any) -> Any:
            scope = self.module.current_scope(request)
            self._scope = scope
            self._result = await probe.aserve(scope.resolve, fragile=self._fragile)
            self._host_value = await scope.resolve(self.host_token)
            response = web.StreamResponse()
            await response.prepare(request)
            for _ in range(3):
                probe.record("chunk")
                await response.write(b"chunk")
            await response.write_eof()
            return response

        self._handler = handler
        self._app.router.add_get("/conformance", self._handler)

    def start(self) -> None:
        """Start the server, which runs ``on_startup`` — the container's startup."""
        from aiohttp.test_utils import TestClient, TestServer

        async def begin() -> Any:
            client = TestClient(TestServer(self._app))
            await client.start_server()
            return client

        self._client = self.bridge.run(begin())

    def stop(self) -> tuple[DestructError, ...]:
        """Close the server: ``on_cleanup`` shuts the container down (IC-9)."""
        before = len(self.reported)
        self.bridge.run(self._client.close())
        return tuple(self.reported[before:])

    def request(self, *, fragile: bool = False) -> UnitResult:
        """Serve one request over a real socket and read the whole body (IC-7)."""
        start = len(self.events)
        self._fragile = fragile

        async def one() -> bytes:
            response = await self._client.get("/conformance")
            return await response.read()

        body = self.bridge.run(one())
        return UnitResult(
            first=self._result["first"],
            second=self._result["second"],
            scope=self._scope,
            body=[body],
            host_value=self._host_value,
            errors=tuple(self.reported),
            events=self.events[start:],
        )


#: Every adapter the suite parametrises over: app name -> (shape, distribution).
ADAPTERS: dict[str, tuple[str, str]] = {
    "starlette": ("asgi", "starlette"),
    "fastapi": ("asgi", "fastapi"),
    "litestar": ("asgi", "litestar"),
    "quart": ("asgi", "quart"),
    "sanic": ("asgi", "sanic"),
    "flask": ("wsgi", "flask"),
    "falcon": ("wsgi", "falcon"),
    "pyramid": ("wsgi", "pyramid"),
    "bottle": ("wsgi", "bottle"),
    "aiohttp": ("http", "aiohttp"),
    "argparse": ("cli", None),
    "click": ("cli", "click"),
    "typer": ("cli", "typer"),
    "cyclopts": ("cli", "cyclopts"),
    "fire": ("cli", "fire"),
}

_SHAPES = {"asgi": AsgiDriver, "wsgi": WsgiDriver, "cli": CliDriver, "http": HttpDriver}

#: Adapters that are measured elsewhere, with the file that measures them.
EXCLUSIONS: dict[str, str] = {
    "django": (
        "the Django adapter keeps its container on the AppConfig that ready() "
        "populated, so the probe container cannot be substituted without booting "
        "a second project; PRD 13's invariants are measured by "
        "examples/apps/django/tests/test_django.py (DJ-1…DJ-22, T13-01…T13-16)"
    ),
}


def exclusions() -> dict[str, str]:
    """Adapters not driven here, each with the reason and the file that covers it."""
    return dict(EXCLUSIONS)


def require(app: str) -> None:
    """Skip the calling test unless ``app``'s distribution is installed (D-62)."""
    _, distribution = ADAPTERS[app]
    if distribution is None:  # argparse is stdlib: there is no distribution to hide
        return
    try:
        importlib.metadata.distribution(distribution)
    except importlib.metadata.PackageNotFoundError:
        pytest.skip(SKIP_REASON.format(name=distribution, app=app))


@contextlib.contextmanager
def session(app: str) -> Any:
    """Open one adapter and guarantee its loop/server is released."""
    shape, distribution = ADAPTERS[app]
    driver = _SHAPES[shape](app, distribution or app)
    try:
        yield driver
    finally:
        driver.release()


def app_names() -> list[str]:
    """Every adapted app, sorted — the parametrisation the suite uses."""
    return sorted(ADAPTERS)
