"""PRD 12 §10.1 for the Bottle example, through the WSGI callable itself.

Bottle ships no test client (§5.8), so this module *is* the client: every request
below is an explicit environ handed to the WSGI callable, and the test — playing
the server's part — reads the returned iterable and calls ``close()`` on it. That
is exactly the point the example is about, so the missing client is an advantage
here: the close point is ours to observe.

Note what an in-process call cannot show: the route runs on ``MainThread`` (BTL-1)
unless a threaded server calls it. T12-01 is therefore checked against
``bottle_app.serve`` — the threaded ``wsgiref`` server the demo uses — and
T12-14 pins the in-process call to ``MainThread`` so the two are never
confused.
"""

from __future__ import annotations

import threading
import time
import urllib.request
import wsgiref.util
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import pytest
from _testing import load, require

require("bottle")

bottle_app = load("bottle", "bottle_app")


@dataclass(frozen=True)
class Response:
    """What a server sees of one response: the status, the headers, and the body."""

    status: str
    headers: list[tuple[str, str]]
    body: bytes

    @property
    def text(self) -> str:
        """The body decoded, for the ``key=value`` bodies the app returns."""
        return self.body.decode()


def environ_for(path: str) -> dict[str, Any]:
    """Build a PEP 3333 environ for ``path``, the way a server would.

    Args:
        path: The request path, e.g. ``"/stream"``.

    Returns:
        A fresh environ; the middleware writes the scope into it.
    """
    environ: dict[str, Any] = {}
    wsgiref.util.setup_testing_defaults(environ)
    environ["PATH_INFO"] = path
    environ["REQUEST_METHOD"] = "GET"
    return environ


def call(wsgi_app: Any, path: str) -> Response:
    """Serve one request through ``wsgi_app``, as a server would.

    Args:
        wsgi_app: The wrapped WSGI callable.
        path: The request path.

    Returns:
        The status, the headers and the whole body — read and then closed, in the
        order a server does it.
    """
    captured: dict[str, Any] = {}

    def start_response(status: str, headers: list[tuple[str, str]], exc_info: Any = None) -> Any:
        del exc_info
        captured["status"] = status
        captured["headers"] = headers
        return None

    iterable = wsgi_app(environ_for(path), start_response)
    try:
        body = b"".join(iterable)
    finally:
        iterable.close()
    return Response(captured["status"], captured["headers"], body)


def open_stream(wsgi_app: Any, path: str = "/stream") -> tuple[Any, dict[str, Any]]:
    """Start a request and stop before the body is over, like a live connection.

    Args:
        wsgi_app: The wrapped WSGI callable.
        path: The request path.

    Returns:
        The response iterable (the middleware's own wrapper, so ``close()`` is the
        close point) and the captured status.
    """
    captured: dict[str, Any] = {}

    def start_response(status: str, headers: list[tuple[str, str]], exc_info: Any = None) -> Any:
        del exc_info
        captured["status"] = status
        captured["headers"] = headers
        return None

    return wsgi_app(environ_for(path), start_response), captured


def parse_body(text: str) -> dict[str, str]:
    """Turn a ``key=value`` response body into a mapping.

    Args:
        text: The response body, one ``key=value`` pair per line.

    Returns:
        The parsed pairs, so a test can name the measurement it asserts on.
    """
    pairs = (line.split("=", 1) for line in text.splitlines() if "=" in line)
    return dict(pairs)


@pytest.fixture
def demo() -> Iterator[bottle_app.Demo]:
    """A started container wrapped in the middleware, closed afterwards.

    Yields:
        The demo object, so a test can reach the WSGI callable, the bridge and the
        container as well as the app.
    """
    bottle_app.reset()
    started = bottle_app.Demo()
    try:
        yield started
    finally:
        started.close()


def test_startup_precedes_the_first_request(demo: bottle_app.Demo) -> None:
    """T12-02: the singleton is built by ``startup()``, once, before any request."""
    assert bottle_app.BOOTS == ["lauren-di-loop"]
    call(demo.wsgi_app, "/report")
    call(demo.wsgi_app, "/report")
    assert bottle_app.BOOTS == ["lauren-di-loop"], "a per-request build would repeat this"


def test_each_request_opens_its_own_scope(demo: bottle_app.Demo) -> None:
    """T12-03: two requests, two scopes."""
    first = parse_body(call(demo.wsgi_app, "/report").text)
    second = parse_body(call(demo.wsgi_app, "/report").text)
    assert first["scope-id"] != second["scope-id"]


def test_the_scope_is_reachable_through_the_environ(demo: bottle_app.Demo) -> None:
    """T12-04: a route reads a ``ResolutionScope`` back from ``bottle.request``.

    The body also carries the ``PATH_INFO`` the scope was handed, which proves the
    host value reached the provider (IC-6) rather than being re-derived.
    """
    measured = parse_body(call(demo.wsgi_app, "/report").text)
    assert measured["scope"] == "ResolutionScope"
    assert measured["path"] == "/report"


def test_container_work_leaves_the_caller_thread(demo: bottle_app.Demo) -> None:
    """T12-01/T12-14: the route is on ``MainThread``, the container work is not.

    An in-process call cannot reproduce a threaded server (BTL-1), so what is
    checked here is the half that *can* be: the route runs on ``MainThread`` and
    every container call hops onto the bridge loop, the only thread the container
    is ever touched from.
    """
    measured = parse_body(call(demo.wsgi_app, "/report").text)
    assert measured["view-thread"] == "MainThread"
    assert measured["built-on"] == "lauren-di-loop"
    assert measured["registry-thread"] == "lauren-di-loop"
    assert demo.bridge.loop.is_running()


def test_the_singleton_keeps_loop_affinity(demo: bottle_app.Demo) -> None:
    """T12-13: the second request reuses the singleton on the same open loop.

    A bridge that opened a loop per request would build a second ``Registry`` on a
    closed loop here — or raise ``RuntimeError: Event loop is closed``.
    """
    first = parse_body(call(demo.wsgi_app, "/report").text)
    second = parse_body(call(demo.wsgi_app, "/report").text)
    assert first["registry-loop"] == second["registry-loop"]
    assert len(bottle_app.BOOTS) == 1


def test_teardown_follows_the_last_chunk(demo: bottle_app.Demo) -> None:
    """T12-05/T12-12: three chunks, all served with the scope open, then teardown."""
    response = call(demo.wsgi_app, "/stream")
    assert response.status.startswith("200")
    assert response.text.splitlines() == [
        "chunk:0:closed=False",
        "chunk:1:closed=False",
        "chunk:2:closed=False",
    ]
    assert bottle_app.EVENTS == [
        "built",
        "hook:after_request:closed=False",
        "chunk:0",
        "chunk:1",
        "chunk:2",
        "torn-down",
    ]
    assert bottle_app.CLOSES == [()]


def test_after_request_is_not_the_owner(demo: bottle_app.Demo) -> None:
    """T12-09: Bottle's own hook fires before ``chunk:0``, so it cannot own it."""
    call(demo.wsgi_app, "/stream")
    events = bottle_app.EVENTS
    assert events.index("hook:after_request:closed=False") < events.index("chunk:0")
    assert events.count("torn-down") == 1


def test_a_failing_route_closes_once(demo: bottle_app.Demo) -> None:
    """T12-11: a route raising ``DIError`` answers 500, and the handler sees it open."""
    response = call(demo.wsgi_app, "/fail")
    assert response.status.startswith("500")
    assert "the route failed on purpose" in response.text
    assert "error-view:closed=False" in bottle_app.EVENTS
    assert bottle_app.EVENTS.count("torn-down") == 1
    assert bottle_app.CLOSES == [()]


def test_an_abandoned_body_still_closes_once(demo: bottle_app.Demo) -> None:
    """T12-10: a client that drops after one chunk still gets exactly one close.

    Bottle pulls ``chunk:0`` while casting the return value, so the connection is
    one chunk old when the test stops reading, exactly like a browser closing a
    long download.
    """
    iterable, captured = open_stream(demo.wsgi_app)
    assert captured["status"].startswith("200")
    assert next(iterable) == b"chunk:0:closed=False\n"
    assert bottle_app.EVENTS == [
        "built",
        "hook:after_request:closed=False",
        "chunk:0",
    ]
    assert bottle_app.CLOSES == [], "the unit must not close before the body is over"
    iterable.close()  # type: ignore[attr-defined]
    assert bottle_app.EVENTS[-1] == "torn-down"
    assert bottle_app.CLOSES == [()]


def test_the_in_process_call_runs_on_the_main_thread(demo: bottle_app.Demo) -> None:
    """T12-14: document the gap, so nobody mistakes a green suite for T12-01."""
    measured = parse_body(call(demo.wsgi_app, "/report").text)
    assert threading.current_thread().name == "MainThread"
    assert measured["view-thread"] == "MainThread"


def test_a_real_server_serves_the_route_off_the_calling_thread() -> None:
    """T12-01: through the threaded ``wsgiref`` server, the route leaves ``MainThread``.

    ``bottle_app.serve`` is the demo's own threaded server (\u00a75.7, BTL-1), so this is
    the row an in-process call cannot reach: the route runs on a worker thread,
    the container work still hops onto the one bridge loop, and the close point
    lands after the body on that same server thread.
    """
    bottle_app.reset()
    started = bottle_app.Demo()
    try:
        with (
            bottle_app.serve(started.wsgi_app) as base_url,
            urllib.request.urlopen(f"{base_url}/report", timeout=5) as response,
        ):
            measured = parse_body(response.read().decode())
        deadline = time.monotonic() + 5.0
        while "torn-down" not in bottle_app.EVENTS and time.monotonic() < deadline:
            time.sleep(0.01)
    finally:
        started.close()

    assert measured["view-thread"] != "MainThread", "the route ran on the test's own thread"
    assert measured["view-thread"] != "lauren-di-loop", "the route ran on the bridge's loop thread"
    assert measured["built-on"] == "lauren-di-loop"
    assert "torn-down" in bottle_app.EVENTS, "the server never reached the close point"
    assert bottle_app.CLOSES == [()]
