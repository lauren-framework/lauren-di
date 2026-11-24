"""PRD 13 measured twice: the DJ-nn requirements, then the T13-nn test plan.

Two halves, because the PRD has two inventories and they answer different
questions:

``DJ-1 … DJ-22`` (§5)
    *what the adapter must do*. Each test is named after the requirement it
    settles, so a PRD edit and this file cannot drift apart silently.
``T13-01 … T13-16`` (§10)
    *how it is proved*. These drive the real ``WSGIHandler`` and ``ASGIHandler``,
    a subprocess pair, a ``transaction.atomic()`` block and a threaded Werkzeug
    server — the seams ``django.test.Client`` papers over (§5.7).

Nothing here needs a database server, a migration or a project scaffold: the
sqlite3 backend is lazy, ``DjangoAppConfig.ready()`` is reached through the
ordinary ``django.setup()``, and the client / handler / server cases share one
settings dict (PRD 13 §5.6).
"""

from __future__ import annotations

import asyncio
import inspect
import json
import pathlib
import subprocess
import sys
import threading
import urllib.request
import wsgiref.util
from contextvars import ContextVar
from typing import Any

import pytest
from _host import build_container
from _testing import load, require

require("django")
# T13-16 drives a threaded Werkzeug server; it is pinned in requirements.txt,
# so its absence is a skip for the module rather than a partial run (D-62).
require("werkzeug")

from django.conf import settings  # noqa: E402
from django.core.exceptions import ImproperlyConfigured  # noqa: E402
from django.core.handlers.asgi import ASGIHandler  # noqa: E402
from django.core.handlers.wsgi import WSGIHandler  # noqa: E402
from django.core.signals import request_finished, request_started  # noqa: E402
from django.db import connections, transaction  # noqa: E402
from django.db.backends.base.base import BaseDatabaseWrapper  # noqa: E402
from django.test import (  # noqa: E402
    AsyncClient,
    Client,
    RequestFactory,
    SimpleTestCase,
    override_settings,
)
from lauren_di import (  # noqa: E402
    DIContainer,
    DIScopeViolationError,
    MissingProviderError,
    OverridePlan,
    ProviderOverride,
    injectable,
    post_construct,
)

django_app = load("django", "django_app")

# The adapter module the app imported; going through the app keeps one module
# object even when this file is the first thing that imports the example.
django_di = django_app.django_di


def setup_module() -> None:
    """Be the operator's half: settings, then ``django.setup()`` (PRD 13 §5.6).

    This is ``manage.py`` in a real project, and it is the only reason
    ``AppConfig.ready()`` ever runs: importing the app module builds nothing
    (DJ-1).
    """
    django_app.configure()


def _body(client_response: Any) -> dict[str, Any]:
    """Decode a JSON response body."""
    return json.loads(client_response.content)


def _async_body(path: str) -> dict[str, Any]:
    """Drive ``path`` through ``AsyncClient`` on a loop of its own (PRD 13 §5.7)."""

    async def call() -> dict[str, Any]:
        response = await AsyncClient().get(path)
        return _body(response)

    return asyncio.run(call())


class NobodyRegistered:
    """A dependency nothing provides, which is the whole point of DJ-2."""


def _resolve_in_a_fresh_container(token: Any, *, overrides: Any = None) -> Any:
    """Start a throwaway container the way ``ready()`` does and resolve ``token``.

    Used by DJ-18: a container built *now* sees the settings of *now*, and
    starting it needs a loop of its own, because ``startup()`` is a coroutine.
    Only ``BuildSettings`` is registered, so the process container's singletons
    (and their lifecycle counters) are left alone.
    """
    container = build_container(value_tokens=django_di.VALUE_TOKENS, overrides=overrides)
    container.register(django_app.BuildSettings)

    async def run() -> Any:
        container.compile()
        await container.startup()
        try:
            return await container.resolve(token)
        finally:
            await container.shutdown()

    return asyncio.run(run())


# ---------------------------------------------------------------------------
# DJ-1 … DJ-6: startup, once per process, on a loop of its own
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_dj1_the_container_is_built_by_ready_not_at_import() -> None:
    """``ready()`` owns the container; no module global and no import-time build."""
    assert "di_container" not in vars(django_di)
    assert isinstance(django_di.container(), DIContainer)
    assert django_app.LIFECYCLE["startup"] == 1

    built_once = django_di.container()
    django_app.configure()  # a second setup() must not build a second container
    assert django_di.container() is built_once
    assert django_app.LIFECYCLE["startup"] == 1


@pytest.mark.integration
def test_dj2_bad_wiring_is_improperly_configured_at_boot() -> None:
    """``compile()`` in ``ready()`` turns a wiring error into a boot failure (DJ-2)."""

    class _Config:
        """Stands in for an ``AppConfig`` that never gets the container."""

    @injectable()
    class NeedsANobody:
        def __init__(self, nobody: NobodyRegistered) -> None:
            self.nobody = nobody

    container = DIContainer()
    container.register(NeedsANobody)

    config = _Config()
    with pytest.raises(ImproperlyConfigured, match="does not compile"):
        django_di.start_container(config, lambda: container)

    assert not hasattr(config, django_di.CONTAINER_ATTR)


@pytest.mark.integration
def test_dj3_compile_is_sync_and_startup_is_a_coroutine() -> None:
    """``compile()`` fits in sync ``ready()``; ``startup()`` needs the bridge (DJ-3)."""
    assert not inspect.iscoroutinefunction(DIContainer.compile)
    assert inspect.iscoroutinefunction(DIContainer.startup)
    # ... and ready() really did run it, once.
    assert django_app.LIFECYCLE["startup"] == 1


@pytest.mark.integration
def test_dj4_a_startup_failure_is_re_raised_not_sys_exit() -> None:
    """A failing ``startup()`` propagates; ``ready()`` never exits the process (DJ-4)."""

    @injectable()
    class ExplodesOnStart:
        @post_construct
        async def start(self) -> None:
            raise RuntimeError("startup failed")

    class Config:
        """Stands in for an ``AppConfig`` that never gets the container."""

    container = DIContainer()
    container.register(ExplodesOnStart)

    with pytest.raises(RuntimeError, match="startup failed"):
        django_di.start_container(Config(), lambda: container)


@pytest.mark.integration
def test_dj5_the_container_lives_on_the_app_config() -> None:
    """The container is process-local state on the ``AppConfig``, not a global (DJ-5)."""
    config = django_di.host()
    assert isinstance(config, django_app.DjangoAppConfig)
    assert getattr(config, django_di.CONTAINER_ATTR) is django_di.container()
    assert getattr(config, django_di.BRIDGE_ATTR) is django_di.bridge()
    assert "di_container" not in vars(django_di)


@pytest.mark.integration
def test_dj6_one_bridge_loop_per_process_and_sync_views_resolve_on_it() -> None:
    """One long-lived loop, shared by every sync request, never one per request (DJ-6)."""
    first = django_di.bridge()
    assert django_di.bridge() is first
    assert first.loop.is_running()
    assert not first.loop.is_closed()

    one = _body(Client().get("/sync/"))
    two = _body(Client().get("/sync/"))

    assert one["built_on_loop"] == id(first.loop)
    assert two["built_on_loop"] == id(first.loop)
    # The instances were built on the bridge thread, not on the caller's thread.
    assert one["built_on_thread"] == two["built_on_thread"]
    assert one["built_on_thread"] != threading.current_thread().name
    # Two requests, two scopes: the loop is shared, the instances are not.
    assert one["uid"] != two["uid"]


# ---------------------------------------------------------------------------
# DJ-7 … DJ-10: the bracket the decorator opens
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_dj7_the_decorator_opens_one_scope_per_request() -> None:
    """The scope is opened in the view wrapper, and only there (DJ-7)."""
    django_app.reset_trace()
    body = _body(Client().get("/sync/"))
    uid = body["uid"]

    assert body["scope_closed"] is False  # open while the view ran
    assert body["same_instance"] is True  # one instance per request (IC-5)
    assert django_app.TRACE[uid] == ["open", "closed"]  # one scope, one close
    assert len(django_app.TRACE) == 1


@pytest.mark.integration
def test_dj8_no_middleware_attaches_or_brackets_anything() -> None:
    """Nothing between the server and the view opens or closes the scope (DJ-8)."""
    assert settings.MIDDLEWARE == []
    assert django_app.SETTINGS["MIDDLEWARE"] == []
    # ... and the REQUEST-scoped provider still resolves, from the decorator alone.
    assert _body(Client().get("/sync/"))["uid"]


@pytest.mark.integration
def test_dj9_a_streaming_response_outlives_the_view() -> None:
    """The scope closes after the last chunk, not when the view returns (DJ-9)."""
    django_app.reset_trace()
    response = Client().get("/stream/")
    uid = response["X-UID"]

    assert response.streaming is True
    chunks = iter(response.streaming_content)

    assert next(chunks).startswith(b"chunk-0")
    assert "closed" not in django_app.TRACE[uid]  # still open mid-stream
    assert list(chunks)[-1].startswith(b"chunk-2")
    assert django_app.TRACE[uid] == ["open", "chunk-0", "chunk-1", "chunk-2", "closed"]


@pytest.mark.integration
def test_dj9b_an_async_stream_closes_after_the_last_chunk() -> None:
    """DJ-9 again, for an ``async def`` view: the async generator closes the unit."""
    seen: list[bytes] = []

    @django_di.with_scope
    async def streaming_view(request: Any) -> Any:
        state = await django_di.scope_of(request).resolve(django_app.RequestState)

        async def body() -> Any:
            seen.append(state.uid.encode())
            yield b"x"

        return django_app.StreamingHttpResponse(body())

    async def call() -> tuple[list[bytes], bool]:
        request = RequestFactory().get("/async-stream/")
        response = await streaming_view(request)
        received = [chunk async for chunk in response.streaming_content]
        return received, getattr(request, django_di.UNIT_ATTR).closed

    received, closed = asyncio.run(call())

    assert received == [b"x"]
    assert closed is True


@pytest.mark.integration
def test_dj10_a_raising_view_still_closes_the_scope() -> None:
    """The ``finally`` closes the unit even when the view never returns (DJ-10)."""
    django_app.reset_trace()
    response = Client(raise_request_exception=False).get("/boom/")

    assert response.status_code == 500
    (record,) = django_app.TRACE.values()
    assert record == ["open", "closed"]


# ---------------------------------------------------------------------------
# DJ-11 … DJ-14: sync/async, and the loop each one resolves on
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_dj11_an_async_view_awaits_the_scope_on_its_own_loop() -> None:
    """``async def`` views resolve by ``await``, on the loop the handler gave them."""
    body = _async_body("/async/")

    assert body["built_on_loop"] != id(django_di.bridge().loop)  # no thread hop
    assert body["built_on_thread"] == threading.current_thread().name
    assert body["scope_closed"] is False
    assert django_app.TRACE[body["uid"]] == ["open", "closed"]


@pytest.mark.integration
def test_dj12_a_sync_view_bridges_even_inside_a_running_loop() -> None:
    """A ``def`` view goes through the bridge; ``asyncio.run()`` here would raise (DJ-12)."""
    request = RequestFactory().get("/sync/")

    async def call_the_sync_view() -> dict[str, Any]:
        # Called from inside a running loop on purpose: exactly the shape that
        # makes an `asyncio.run()`-based adapter fail.
        return _body(django_app.sync_view(request))

    body = asyncio.run(call_the_sync_view())

    assert body["built_on_loop"] == id(django_di.bridge().loop)
    assert getattr(request, django_di.UNIT_ATTR).closed is True


@pytest.mark.integration
def test_dj13_a_context_var_cannot_carry_the_scope() -> None:
    """A thread hop copies the context in; the scope still travels explicitly."""
    body = _async_body("/async/")

    # `sync_to_async(..., thread_sensitive=True)` is how an async view calls sync
    # code, and it copies the view's context *into* that thread ...
    assert body["marker_in_thread"] == "set-in-the-view"
    # ... which is a real thread, not the view's own.
    assert body["helper_thread"] != body["view_thread"]
    # A ContextVar therefore says nothing about *which* scope a request owns, so
    # the adapter never keeps one: the scope lives on the request instead.
    assert not [value for value in vars(django_di).values() if isinstance(value, ContextVar)]


@pytest.mark.integration
def test_dj14_there_is_no_middleware_to_declare_sync_or_async_capable() -> None:
    """A decorator has no ``async_capable`` / ``sync_capable`` flags to get wrong."""
    assert settings.MIDDLEWARE == []
    assert not [name for name in dir(django_di) if "Middleware" in name]


# ---------------------------------------------------------------------------
# DJ-15 … DJ-17: the ORM connection is the host's, not the container's
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_dj15_the_connection_is_handed_in_as_a_value() -> None:
    """The wrapper arrives as a per-unit value, never as a provider (DJ-15)."""
    body = _body(Client().get("/orm/"))

    assert body["is_this_threads_connection"] is True
    assert django_di.CONNECTION in django_di.VALUE_TOKENS
    assert not django_di.container().has_provider(django_di.CONNECTION)


@pytest.mark.integration
def test_dj16_the_connection_is_never_a_singleton() -> None:
    """The wrapper is thread-affine, so a singleton would hand it to another thread."""
    seen: list[int] = []

    def other_thread() -> None:
        seen.append(id(connections["default"]))

    worker = threading.Thread(target=other_thread, name="probe-5")
    worker.start()
    worker.join()

    assert seen[0] != id(connections["default"])  # a different wrapper per thread
    assert django_di.CONNECTION not in django_di.container().singletons()


@pytest.mark.integration
def test_dj17_teardown_never_closes_the_connection(monkeypatch: pytest.MonkeyPatch) -> None:
    """The adapter must not call the ORM's ``close()``; the host owns it (DJ-17)."""
    closed: list[str] = []

    def refuse(self: Any) -> None:
        closed.append(self.alias)
        raise AssertionError("the adapter closed the host's connection")

    monkeypatch.setattr(BaseDatabaseWrapper, "close", refuse)

    assert _body(Client().get("/orm/"))["is_this_threads_connection"] is True
    assert closed == []


# ---------------------------------------------------------------------------
# DJ-18 … DJ-22: tests, settings, and the absence of ambient state
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_dj18_a_container_built_in_ready_is_a_process_singleton() -> None:
    """``override_settings`` cannot reach a container that was built before it (DJ-18)."""
    with override_settings(FEATURE_LABEL="overridden"):
        live = _body(Client().get("/settings/"))
        # A test that needs different settings builds its own container inside.
        fresh = _resolve_in_a_fresh_container(django_app.BuildSettings)

    assert live["built_with"] == "built"  # the process container kept the old value
    assert live["live"] == "overridden"  # the setting itself did change
    assert fresh.label == "overridden"  # a container built now sees it


@pytest.mark.integration
def test_dj18_an_override_plan_is_a_build_time_decision() -> None:
    """Overrides are applied when the container is built, through ``OverridePlan`` (IC-11)."""

    class FrozenSettings:
        """Stands in for the real singleton, with a value the test chose."""

        label = "frozen"

    plan = OverridePlan(
        {
            django_app.BuildSettings: ProviderOverride(
                django_app.BuildSettings, "class", use_class=FrozenSettings
            )
        }
    )
    built = _resolve_in_a_fresh_container(django_app.BuildSettings, overrides=plan)

    assert isinstance(built, FrozenSettings)
    # The live container was built before the plan existed, so it is untouched.
    assert isinstance(
        django_di.container().singletons()[django_app.BuildSettings], django_app.BuildSettings
    )


@pytest.mark.integration
class TestCaseUsesTheProductionBracket(SimpleTestCase):
    """DJ-19 and DJ-20: a ``SimpleTestCase`` reuses the adapter rather than its own hook."""

    def test_dj19_the_view_runs_inside_the_production_bracket(self) -> None:
        """DJ-19: no bespoke bracket — the decorated view is what the test calls."""
        request = RequestFactory().get("/sync/")
        response = django_app.sync_view(request)  # the same wrapper Django calls

        assert response.status_code == 200
        unit = getattr(request, django_di.UNIT_ATTR)
        assert unit.closed is True
        assert unit.errors == ()

    def test_dj20_a_simple_test_case_does_not_touch_startup(self) -> None:
        """DJ-20: ``SimpleTestCase`` runs without a database and without a new startup."""
        assert django_app.LIFECYCLE["startup"] == 1
        assert settings.DATABASES["default"]["ENGINE"].endswith("sqlite3")


@pytest.mark.integration
def test_dj21_the_client_runs_the_view_on_the_callers_thread_and_dirties_it() -> None:
    """A green test says nothing about threading or ambient state (DJ-21)."""
    token = django_app.MARKER.set("clean")
    try:
        body = _body(Client().get("/sync/"))
        assert body["view_thread"] == threading.current_thread().name
        # ... and the view's ContextVar write is still in *this* context:
        assert django_app.MARKER.get() == "set-by-sync-view"
    finally:
        django_app.MARKER.reset(token)


@pytest.mark.integration
def test_dj22_there_is_no_ambient_scope_to_lean_on() -> None:
    """DJ-22: opening the scope is explicit, exactly as the decorator does it."""
    with pytest.raises(DIScopeViolationError):
        django_di.bridge().run(django_di.container().resolve(django_app.RequestState))

    # An undecorated view is told so, rather than silently resolving outside a scope.
    with pytest.raises(RuntimeError, match="no scope on this request"):
        django_di.scope_of(RequestFactory().get("/undecorated/"))

    assert isinstance(
        _resolve_with_an_explicit_scope(django_app.RequestState), django_app.RequestState
    )


def _resolve_with_an_explicit_scope(token: Any) -> Any:
    """Open a scope by hand, around one resolve, and close it (DJ-22)."""
    container = django_di.container()
    values = {
        django_di.CONNECTION: connections["default"],
        django_di.REQUEST: RequestFactory().get("/sync/"),
    }

    async def run() -> Any:
        async with container.scope(values=values) as scope:
            return await scope.resolve(token)

    return django_di.bridge().run(run())


# ---------------------------------------------------------------------------
# PRD 13 §10 (T13-01 … T13-16): the same recipes through real handlers
# ---------------------------------------------------------------------------
#
# `django.test.Client` is convenient and lies about threading (DJ-21, T13-16), so
# these cases drive the seams a deployment actually uses: `WSGIHandler` and
# `ASGIHandler` with a server-shaped environ/scope, a subprocess pair for the
# reload model, and one threaded Werkzeug server as the production contrast.

#: How long a test waits for a worker thread or a socket to answer.
TIMEOUT = 5.0


def _wsgi_environ(path: str) -> dict[str, Any]:
    """A PEP 3333 environ for ``path``, as ``wsgiref`` would build it."""
    environ: dict[str, Any] = {}
    wsgiref.util.setup_testing_defaults(environ)
    environ.update(PATH_INFO=path, REQUEST_METHOD="GET", SCRIPT_NAME="")
    return environ


def _serve_wsgi(path: str) -> tuple[Any, str]:
    """Drive one request through Django's ``WSGIHandler``.

    Args:
        path: The URL path to request.

    Returns:
        The response object (iterable, and closable) and the status line.
    """
    captured: dict[str, str] = {}

    def start_response(status: str, headers: list[tuple[str, str]], exc_info: Any = None) -> Any:
        del headers, exc_info
        captured["status"] = status
        return lambda chunk: None

    return WSGIHandler()(_wsgi_environ(path), start_response), captured["status"]


def _asgi_scope(path: str) -> dict[str, Any]:
    """A minimal ASGI HTTP scope for ``path``."""
    return {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "root_path": "",
        "headers": [],
        "server": ("127.0.0.1", 80),
        "client": ("127.0.0.1", 1234),
    }


def _drive_asgi(path: str) -> tuple[int, bytes, list[dict[str, Any]]]:
    """Drive one request through Django's ``ASGIHandler``.

    ``receive`` hands over the request body once and then *blocks* until the
    response has been sent, exactly as T13-07 asks: the handler runs
    ``listen_for_disconnect`` in the same task group, so a receive that returned
    a second ``http.request`` would trip its "invalid ASGI message" assertion
    instead of measuring the adapter.

    Args:
        path: The URL path to request.

    Returns:
        The status code, the response body, and every ASGI message sent.
    """

    async def run() -> tuple[int, bytes, list[dict[str, Any]]]:
        sent: list[dict[str, Any]] = []
        finished = asyncio.Event()
        delivered = False

        async def receive() -> dict[str, Any]:
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": b"", "more_body": False}
            await finished.wait()
            return {"type": "http.disconnect"}

        async def send(message: dict[str, Any]) -> None:
            sent.append(message)
            if message["type"] == "http.response.body" and not message.get("more_body"):
                finished.set()

        await ASGIHandler()(_asgi_scope(path), receive, send)
        body = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
        status = next(m["status"] for m in sent if m["type"] == "http.response.start")
        return status, body, sent

    return asyncio.run(run())


@pytest.mark.integration
def test_t13_01_setup_twice_in_one_process_runs_ready_once() -> None:
    """T13-01: a second ``django.setup()`` neither rebuilds nor re-compiles."""
    django_app.configure()
    django_app.configure()

    assert django_app.LIFECYCLE["startup"] == 1
    # ``compile()`` is idempotent, so calling it again from ``ready()`` would not
    # have raised either — which is what the row asks to be true.
    django_di.container().compile()


@pytest.mark.integration
def test_t13_02_two_processes_each_run_ready_once() -> None:
    """T13-02: the reload model is two processes, one ``ready()`` each."""
    app_dir = pathlib.Path(django_app.__file__).resolve().parent
    apps_dir = app_dir.parent
    root = apps_dir.parent.parent
    probe = (
        "import os, sys\n"
        f"sys.path[:0] = {[str(app_dir), str(apps_dir), str(root)]!r}\n"
        "import django_app\n"
        "django_app.configure()\n"
        'print(os.getpid(), django_app.LIFECYCLE["startup"])\n'
    )

    runs = [
        subprocess.run(
            [sys.executable, "-c", probe],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        for _ in range(2)
    ]

    for run in runs:
        assert run.returncode == 0, run.stderr
    pid, startup = zip(*(run.stdout.split() for run in runs), strict=True)
    assert len(set(pid)) == 2, "both probes reported the same pid, so this measured one process"
    assert startup == ("1", "1")


@pytest.mark.integration
def test_t13_03_the_wsgi_handler_runs_a_sync_view_on_the_calling_thread() -> None:
    """T13-03: through ``WSGIHandler`` the view is the caller's, and the scope closes once."""
    django_app.reset_trace()
    response, status = _serve_wsgi("/sync/")
    body = json.loads(b"".join(response))

    assert status.startswith("200")
    assert body["view_thread"] == threading.current_thread().name == "MainThread"
    assert body["built_on_thread"] == "lauren-di-loop"
    assert list(django_app.TRACE.values()) == [["open", "closed"]]


@pytest.mark.integration
def test_t13_04_the_asgi_handler_runs_a_sync_view_on_a_worker_thread() -> None:
    """T13-04: ``ASGIHandler`` hands a ``def`` view to its thread pool, one close."""
    django_app.reset_trace()
    status, raw, _sent = _drive_asgi("/sync/")
    body = json.loads(raw)

    assert status == 200
    assert body["view_thread"] != "MainThread", "the sync view blocked the ASGI handler's loop"
    assert body["view_thread"].startswith("ThreadPoolExecutor")
    assert body["built_on_thread"] == "lauren-di-loop"
    assert list(django_app.TRACE.values()) == [["open", "closed"]]


@pytest.mark.integration
def test_t13_05_the_asgi_handler_runs_an_async_view_on_the_loop_thread() -> None:
    """T13-05: an ``async def`` view awaits on the handler's own loop, no hop."""
    django_app.reset_trace()
    status, raw, _sent = _drive_asgi("/async/")
    body = json.loads(raw)

    assert status == 200
    assert body["view_thread"] == "MainThread", "the async view left the handler's loop"
    assert body["built_on_loop"] != id(django_di.bridge().loop), "the async view used the bridge"
    assert list(django_app.TRACE.values()) == [["open", "closed"]]


@pytest.mark.integration
def test_t13_06_a_wsgi_stream_closes_after_the_last_chunk() -> None:
    """T13-06: ``WSGIHandler`` streaming — the close follows ``chunk-2``, not the view."""
    django_app.reset_trace()
    response, status = _serve_wsgi("/stream/")
    uid = response["X-UID"]
    chunks = iter(response)

    assert next(chunks).startswith(b"chunk-0")
    assert django_app.TRACE[uid] == ["open", "chunk-0"], "the scope closed before the body"
    assert [chunk[:7] for chunk in chunks] == [b"chunk-1", b"chunk-2"]
    assert django_app.TRACE[uid] == ["open", "chunk-0", "chunk-1", "chunk-2", "closed"]
    assert status.startswith("200")


@pytest.mark.integration
def test_t13_07_an_asgi_stream_closes_in_the_same_order() -> None:
    """T13-07: the ASGI path orders the close the same way, with a blocking receive.

    ``/stream/`` is a *sync* generator, which Django's ASGI handler serves while
    warning that it had to consume it eagerly; the ordering is still the one that
    matters here, and the async twin (T13-06's sibling in the DJ-9 pair) is
    covered by ``test_dj9b_an_async_stream_closes_after_the_last_chunk``.
    """
    django_app.reset_trace()
    status, raw, sent = _drive_asgi("/stream/")

    assert status == 200
    assert [line.split()[0] for line in raw.decode().splitlines()] == [
        "chunk-0",
        "chunk-1",
        "chunk-2",
    ]
    (record,) = django_app.TRACE.values()
    assert record == ["open", "chunk-0", "chunk-1", "chunk-2", "closed"]
    # The handler never saw an invalid message, and the last frame is final.
    assert sent[-1]["type"] == "http.response.body"
    assert not sent[-1].get("more_body"), "the last body frame claims there is more to come"


@pytest.mark.integration
def test_t13_08_an_abandoned_stream_closes_exactly_once() -> None:
    """T13-08: a client that hangs up after one chunk still gets one close (DJ-10)."""
    django_app.reset_trace()
    response, _status = _serve_wsgi("/stream/")
    uid = response["X-UID"]
    chunks = iter(response)

    assert next(chunks).startswith(b"chunk-0")
    response.close()  # what the server does when the socket dies
    response.close()  # ... and a second close must not repeat the teardown
    assert django_app.TRACE[uid] == ["open", "chunk-0", "closed"]


@pytest.mark.integration
def test_t13_09_request_finished_may_fire_twice_but_the_scope_closes_once() -> None:
    """T13-09: the host signal is not the close point, so its count does not matter."""
    started: list[int] = []
    finished: list[int] = []

    def on_started(**kwargs: Any) -> None:
        del kwargs
        started.append(1)

    def on_finished(**kwargs: Any) -> None:
        del kwargs
        finished.append(1)

    request_started.connect(on_started, weak=False)
    request_finished.connect(on_finished, weak=False)
    try:
        django_app.reset_trace()
        uid = _body(Client().get("/sync/"))["uid"]
    finally:
        request_started.disconnect(on_started)
        request_finished.disconnect(on_finished)

    assert len(started) == 1
    assert finished, "request_finished never fired, so the row measured nothing"
    # Measured: twice on this path. The adapter closes once regardless, because
    # its close point is the decorator's, not Django's signal.
    assert django_app.TRACE[uid] == ["open", "closed"]


@pytest.mark.integration
def test_t13_10_two_threads_get_two_connection_wrappers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T13-10: the connection is per thread, so a singleton would raise ``DatabaseError``."""
    refused: list[str] = []

    def refuse(self: Any) -> None:
        refused.append(self.alias)
        raise AssertionError("the container closed a connection it does not own")

    monkeypatch.setattr(BaseDatabaseWrapper, "close", refuse)

    resolved: dict[str, Any] = {}

    def run_in_thread(name: str) -> None:
        async def work() -> Any:
            values = {
                django_di.REQUEST: RequestFactory().get("/sync/"),
                django_di.CONNECTION: connections["default"],
            }
            async with django_di.container().scope(values=values) as scope:
                return await scope.resolve(django_app.RequestState)

        resolved[name] = asyncio.run(work())

    threads = [threading.Thread(target=run_in_thread, args=(name,)) for name in ("a", "b")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(TIMEOUT)

    assert set(resolved) == {"a", "b"}
    assert resolved["a"].connection is not resolved["b"].connection
    assert resolved["a"].thread != resolved["b"].thread
    assert refused == [], "container teardown touched the host's connections"
    assert django_di.CONNECTION not in django_di.container().singletons()


@pytest.mark.integration
def test_t13_11_teardown_inside_a_transaction_leaves_the_connection_usable() -> None:
    """T13-11: a scope closed inside ``transaction.atomic()`` never touches the ORM."""

    async def work() -> Any:
        values = {
            django_di.REQUEST: RequestFactory().get("/sync/"),
            django_di.CONNECTION: connections["default"],
        }
        async with django_di.container().scope(values=values) as scope:
            return await scope.resolve(django_app.RequestState)

    with transaction.atomic():
        state = django_di.bridge().run(work())  # the scope is closed on exit above
        with connections["default"].cursor() as cursor:
            cursor.execute("select 1")
            assert cursor.fetchone() == (1,), "teardown broke the host's transaction"

    assert state.closed is True
    assert django_app.TRACE[state.uid] == ["open", "closed"]


@pytest.mark.integration
def test_t13_12_a_test_that_changes_settings_builds_its_own_container() -> None:
    """T13-12: ``override_settings`` reaches a container built inside it, not the app's."""
    with override_settings(FEATURE_LABEL="overridden"):
        live = _body(Client().get("/settings/"))
        fresh = _resolve_in_a_fresh_container(django_app.BuildSettings)

    assert live["built_with"] == "built"
    assert live["live"] == "overridden"
    assert fresh.label == "overridden"


@pytest.mark.integration
def test_t13_13_there_is_no_middleware_to_declare_async_capable() -> None:
    """T13-13: the DJ-14 trap is unreachable, because the bracket is a decorator."""
    assert settings.MIDDLEWARE == []
    assert not [name for name in dir(django_di) if "Middleware" in name]
    # A plain function with a wrapped view: nothing Django can call as a
    # coroutine without a sync/async capability declaration.
    assert inspect.isfunction(django_app.sync_view)
    assert django_app.sync_view.__wrapped__ is not None


@pytest.mark.integration
def test_t13_14_a_dirty_context_cannot_hand_a_scope_to_the_next_request() -> None:
    """T13-14: the leak is real, so the adapter never reads the ``ContextVar``."""
    token = django_app.MARKER.set("dirty-from-a-previous-request")
    try:
        django_app.reset_trace()
        first = _body(Client().get("/sync/"))
        # The sync view's write lands in *this* context (DJ-21): the leak, measured.
        assert django_app.MARKER.get() == "set-by-sync-view"
        django_app.MARKER.set("dirty-again")
        second = _body(Client().get("/sync/"))
    finally:
        django_app.MARKER.reset(token)

    assert first["uid"] != second["uid"], "the dirty context leaked a scope into request two"
    assert django_app.TRACE[first["uid"]] == ["open", "closed"]
    assert django_app.TRACE[second["uid"]] == ["open", "closed"]
    assert django_app.RequestState not in django_di.container().singletons()


@pytest.mark.integration
def test_t13_15_a_missing_provider_fails_boot_not_the_first_request() -> None:
    """T13-15: ``ImproperlyConfigured``, chained from ``MissingProviderError``."""

    @injectable()
    class NeedsANobody:
        def __init__(self, nobody: NobodyRegistered) -> None:
            self.nobody = nobody

    class Config:
        """Stands in for an ``AppConfig`` that never gets the container."""

    container = DIContainer()
    container.register(NeedsANobody)

    with pytest.raises(ImproperlyConfigured) as raised:
        django_di.start_container(Config(), lambda: container)

    assert isinstance(raised.value.__cause__, MissingProviderError)


@pytest.mark.integration
def test_t13_16_the_client_is_not_a_threaded_server() -> None:
    """T13-16: same URLconf, two threads — the client's and the server's differ (DJ-21)."""
    from werkzeug.serving import WSGIRequestHandler, make_server

    class Quiet(WSGIRequestHandler):
        """Keep the test's output about the container, not about access logs."""

        def log(self, *args: Any, **kwargs: Any) -> None:
            """Drop werkzeug's access log."""

    token = django_app.MARKER.set("clean")
    try:
        client_body = _body(Client().get("/sync/"))
        assert client_body["view_thread"] == "MainThread"
        assert django_app.MARKER.get() == "set-by-sync-view", (
            "the client did not dirty this context"
        )

        django_app.MARKER.set("clean")
        server = make_server("127.0.0.1", 0, WSGIHandler(), threaded=True, request_handler=Quiet)
        serving = threading.Thread(target=server.serve_forever, name="werkzeug", daemon=True)
        serving.start()
        try:
            url = f"http://127.0.0.1:{server.server_port}/sync/"
            with urllib.request.urlopen(url, timeout=TIMEOUT) as response:
                server_body = json.loads(response.read())
        finally:
            server.shutdown()
            serving.join(TIMEOUT)
            server.server_close()

        assert server_body["view_thread"] != "MainThread"
        assert server_body["view_thread"] != "lauren-di-loop"
        assert django_app.MARKER.get() == "clean", "another thread wrote this thread's context"
    finally:
        django_app.MARKER.reset(token)
