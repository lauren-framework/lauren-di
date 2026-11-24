"""PRD 12 §10.1 for the Pyramid example, through a real WSGI client.

The client is ``webtest.TestApp`` — §5.8 names "any WSGI client" for Pyramid, and
``werkzeug.test.Client`` is not installed here. Two consequences the cases below
respect:

* it runs the view on ``MainThread`` (PYR-1), so T12-01 is checked where it can
  be: the *container* work leaves the caller's thread through the bridge, and
  T12-14 records why a real threaded server is still the only way to check the
  view's own thread;
* it consumes the whole body before ``TestApp.get`` returns, which is what makes
  the close-point assertion (T12-05) a statement about the *end* of the response.

The close point itself is never taken on trust: the body carries ``closed=`` as
each chunk is produced, and the module's ``EVENTS`` log is compared in full.
"""

from __future__ import annotations

import threading
import time
import urllib.request
import wsgiref.util
from collections.abc import Iterator
from typing import Any

import pytest

try:  # pinned by requirements.txt; imported defensively so a bare environment
    # skips with a reason instead of raising ImportError at collection time.
    import webtest
except ImportError:  # pragma: no cover - `require` below turns this into a skip
    webtest = None  # type: ignore[assignment]

from _testing import load, require

require("pyramid")
require("webtest")

pyramid_app = load("pyramid", "pyramid_app")


def parse_body(text: str) -> dict[str, str]:
    """Turn a ``key=value`` response body into a mapping.

    Args:
        text: The response body, one ``key=value`` pair per line.

    Returns:
        The parsed pairs, so a test can name the measurement it asserts on.
    """
    pairs = (line.split("=", 1) for line in text.splitlines() if "=" in line)
    return dict(pairs)


def wsgi_environ(path: str) -> dict[str, Any]:
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


def start_response(captured: dict[str, Any]) -> Any:
    """Build a WSGI ``start_response`` that remembers the status line.

    Args:
        captured: Where to record ``status`` and ``headers``.

    Returns:
        The callable to hand to the WSGI app.
    """

    def _start(status: str, headers: list[tuple[str, str]], exc_info: Any = None) -> Any:
        del exc_info
        captured["status"] = status
        captured["headers"] = headers
        return None

    return _start


@pytest.fixture
def client() -> Iterator[tuple[webtest.TestApp, pyramid_app.Demo]]:
    """A started container behind a ``webtest`` client, closed afterwards.

    Yields:
        The client and the demo object, so a test can reach the bridge and the
        container as well as the app.
    """
    pyramid_app.reset()
    demo = pyramid_app.Demo()
    # ``Demo`` boots the singleton; the logs are cleared *before* it so every test
    # starts from zero and T12-02 sees exactly one boot (its own container's).
    try:
        yield webtest.TestApp(demo.wsgi_app), demo
    finally:
        demo.close()


def test_startup_precedes_the_first_request(client: tuple[webtest.TestApp, Any]) -> None:
    """T12-02: the singleton is built by ``startup()``, once, before any request."""
    http, _demo = client
    assert pyramid_app.BOOTS == ["lauren-di-loop"]
    http.get("/report")
    http.get("/report")
    assert pyramid_app.BOOTS == ["lauren-di-loop"], "a per-request build would repeat this"


def test_each_request_opens_its_own_scope(client: tuple[webtest.TestApp, Any]) -> None:
    """T12-03: two requests, two scopes."""
    http, _demo = client
    first = parse_body(http.get("/report").text)
    second = parse_body(http.get("/report").text)
    assert first["scope-id"] != second["scope-id"]


def test_the_scope_is_reachable_through_the_environ(
    client: tuple[webtest.TestApp, Any],
) -> None:
    """T12-04: a view reads a ``ResolutionScope`` back from ``request.environ``.

    The body also carries the ``PATH_INFO`` the scope was handed, which proves the
    host value reached the provider (IC-6) rather than being re-derived.
    """
    http, _demo = client
    measured = parse_body(http.get("/report").text)
    assert measured["scope"] == "ResolutionScope"
    assert measured["path"] == "/report"


def test_container_work_leaves_the_caller_thread(
    client: tuple[webtest.TestApp, Any],
) -> None:
    """T12-01/T12-14: the view is on ``MainThread``, the container work is not.

    An in-process client cannot reproduce a threaded server (PYR-1), so what is
    checked here is the half that *can* be: the view runs on ``MainThread`` and
    every container call hops onto the bridge loop, which is the only thread the
    container is ever touched from.
    """
    http, demo = client
    measured = parse_body(http.get("/report").text)
    assert measured["view-thread"] == "MainThread"
    assert measured["built-on"] == "lauren-di-loop"
    assert measured["registry-thread"] == "lauren-di-loop"
    assert demo.bridge.loop.is_running()


def test_the_singleton_keeps_loop_affinity(client: tuple[webtest.TestApp, Any]) -> None:
    """T12-13: the second request reuses the singleton on the same open loop.

    A bridge that opened a loop per request would build a second ``Registry`` on a
    closed loop here — or raise ``RuntimeError: Event loop is closed``.
    """
    http, _demo = client
    first = parse_body(http.get("/report").text)
    second = parse_body(http.get("/report").text)
    assert first["registry-loop"] == second["registry-loop"]
    assert len(pyramid_app.BOOTS) == 1


def test_teardown_follows_the_last_chunk(client: tuple[webtest.TestApp, Any]) -> None:
    """T12-05/T12-12: three chunks, all served with the scope open, then teardown.

    ``TestApp.get`` reads the body to the end, so by the time it returns the
    server has closed the iterable: the whole event log is comparable, and
    ``torn-down`` is last.
    """
    http, _demo = client
    response = http.get("/stream")
    assert response.text.splitlines() == [
        "chunk:0:closed=False",
        "chunk:1:closed=False",
        "chunk:2:closed=False",
    ]
    assert pyramid_app.EVENTS == [
        "built",
        "hook:NewResponse:closed=False",
        "chunk:0",
        "chunk:1",
        "chunk:2",
        "torn-down",
    ]
    assert pyramid_app.CLOSES == [()]


def test_new_response_is_not_the_owner(client: tuple[webtest.TestApp, Any]) -> None:
    """T12-08: Pyramid's own hook fires before ``chunk:0``, so it cannot own it."""
    http, _demo = client
    http.get("/stream")
    events = pyramid_app.EVENTS
    assert events.index("hook:NewResponse:closed=False") < events.index("chunk:0")
    assert events.count("torn-down") == 1


def test_a_failing_view_closes_once(client: tuple[webtest.TestApp, Any]) -> None:
    """T12-11: a view raising ``DIError`` answers 500, and the error view sees it open."""
    http, _demo = client
    response = http.get("/fail", expect_errors=True)
    assert response.status_int == 500
    assert response.text == "the view failed on purpose\n"
    assert "error-view:closed=False" in pyramid_app.EVENTS
    assert pyramid_app.EVENTS.count("torn-down") == 1
    assert pyramid_app.CLOSES == [()]


def test_an_abandoned_body_still_closes_once(client: tuple[webtest.TestApp, Any]) -> None:
    """T12-10: a client that drops after one chunk still gets exactly one close.

    ``webtest`` always reads to the end, so this case drives the WSGI callable
    directly — the disconnect is the test closing the iterable early, which is
    what a server does when the socket dies.
    """
    _http, demo = client
    captured: dict[str, Any] = {}
    iterable = demo.wsgi_app(wsgi_environ("/stream"), start_response(captured))
    assert captured["status"] == "200 OK"
    assert next(iter(iterable)) == b"chunk:0:closed=False\n"
    assert pyramid_app.EVENTS == ["built", "hook:NewResponse:closed=False", "chunk:0"]
    assert pyramid_app.CLOSES == [], "the unit must not close before the body is over"
    iterable.close()
    assert pyramid_app.EVENTS[-1] == "torn-down"
    assert pyramid_app.CLOSES == [()]


def test_the_in_process_client_is_not_a_threaded_server(
    client: tuple[webtest.TestApp, Any],
) -> None:
    """T12-14: document the gap, so nobody mistakes a green suite for T12-01."""
    http, _demo = client
    http.get("/report")
    assert threading.current_thread().name == "MainThread"
    assert parse_body(http.get("/report").text)["view-thread"] == "MainThread"


def test_a_real_server_serves_the_view_off_the_calling_thread() -> None:
    """T12-01: through the threaded ``wsgiref`` server, the view leaves ``MainThread``.

    ``pyramid_app.serve`` is the demo's own threaded server (\u00a75.6, PYR-1), so this
    is the row an in-process client cannot reach: the view runs on a worker
    thread, the container work still hops onto the one bridge loop, and the close
    point lands after the body on that same server thread.
    """
    pyramid_app.reset()
    demo = pyramid_app.Demo()
    try:
        with (
            pyramid_app.serve(demo.wsgi_app) as base_url,
            urllib.request.urlopen(f"{base_url}/report", timeout=5) as response,
        ):
            measured = parse_body(response.read().decode())
        deadline = time.monotonic() + 5.0
        while "torn-down" not in pyramid_app.EVENTS and time.monotonic() < deadline:
            time.sleep(0.01)
    finally:
        demo.close()

    assert measured["view-thread"] != "MainThread", "the view ran on the test's own thread"
    assert measured["view-thread"] != "lauren-di-loop", "the view ran on the bridge's loop thread"
    assert measured["built-on"] == "lauren-di-loop"
    assert "torn-down" in pyramid_app.EVENTS, "the server never reached the close point"
    assert pyramid_app.CLOSES == [()]
