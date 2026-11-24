"""The Falcon example, executed: the close point is the body, not the responder.

PRD 12 §10.1 for this host, one test per row, with the row number in the
docstring so coverage is checkable by grep:

======  ==========================================================
Row     What is measured here
======  ==========================================================
T12-01  a real threaded server runs the responder off the caller's thread
T12-02  ``startup()`` builds the singleton before the first request
T12-03  one scope per request
T12-04  the scope is reachable from ``req.env["di"]`` — and is the one closed
T12-05  three chunks, all served with the scope open, then closed
T12-07  Falcon's ``process_response`` is not an owner (negative control)
T12-10  a client that hangs up after one chunk still gets exactly one close
T12-11  a failing responder answers 500, closes once, and the error handler sees it open
T12-12  the ``@pre_destruct`` marker follows the last chunk
T12-13  the singleton keeps loop affinity across requests (D-59)
T12-14  the in-process client runs the responder on ``MainThread``
======  ==========================================================

Every assertion here is about *order*, because Falcon's own hooks are all on the
wrong side of the stream. The resource returns ``resp.stream`` and Falcon runs
``process_response`` immediately afterwards — while the WSGI server has not read
a single chunk yet. So the adapter cannot use a Falcon hook, and the tests
measure exactly why.
"""

from __future__ import annotations

import io
import json
import threading
import time
import urllib.request
from socketserver import ThreadingMixIn
from types import ModuleType
from typing import Any
from wsgiref.simple_server import WSGIRequestHandler, WSGIServer, make_server

import pytest
from _testing import load, require
from falcon import testing
from lauren_di import ResolutionScope

require("falcon")

falcon_app = load("falcon", "falcon_app")
falcon_di = load("falcon", "falcon_di")

#: How long a test waits for the *other* thread's close point to land.
TIMEOUT = 5.0


def marks(module: ModuleType, since: int) -> list[str]:
    """Trace entries recorded after ``since``; no test assumes a clean log."""
    return module.EVENTS[since:]


def commits(seen: list[str]) -> list[str]:
    """Every ``uow:commit:<id>`` marker in ``seen``."""
    return [mark for mark in seen if mark.startswith("uow:commit:")]


def commit_of(seen: list[str]) -> str:
    """The single ``uow:commit:<id>`` marker in ``seen``, asserting there is one."""
    found = commits(seen)
    assert len(found) == 1, f"expected exactly one commit, saw {found}"
    return found[0]


def wait_for(marker: str) -> bool:
    """Poll for ``marker`` without a blind sleep, so a race reports itself as False."""
    deadline = time.monotonic() + TIMEOUT
    while time.monotonic() < deadline:
        if marker in falcon_app.EVENTS:
            return True
        time.sleep(0.01)
    return False


def environ_for(path: str) -> dict[str, Any]:
    """A minimal PEP 3333 environ: a WSGI app may assume nothing more."""
    return {
        "REQUEST_METHOD": "GET",
        "PATH_INFO": path,
        "QUERY_STRING": "",
        "SERVER_NAME": "127.0.0.1",
        "SERVER_PORT": "80",
        "SERVER_PROTOCOL": "HTTP/1.1",
        "HTTP_HOST": "127.0.0.1",
        "CONTENT_LENGTH": "",
        "wsgi.version": (1, 0),
        "wsgi.url_scheme": "http",
        "wsgi.input": io.BytesIO(b""),
        "wsgi.errors": io.StringIO(),
        "wsgi.multithread": False,
        "wsgi.multiprocess": False,
        "wsgi.run_once": False,
    }


def fetch(path: str) -> tuple[Any, list[str]]:
    """GET ``path`` through Falcon's client, which drains and closes the iterable.

    Returns:
        ``(result, seen)``: the test client's result and the trace it produced.
    """
    start = len(falcon_app.EVENTS)
    result = testing.TestClient(falcon_app.APPLICATION).simulate_get(path)
    return result, marks(falcon_app, start)


def open_body(path: str) -> tuple[dict[str, Any], dict[str, Any], Any]:
    """Call the WSGI application by hand, so the test owns the close.

    Returns:
        ``(environ, captured, body)`` with ``body`` still undrained.
    """
    environ = environ_for(path)
    captured: dict[str, Any] = {}

    def start_response(status: str, headers: list[tuple[str, str]], exc_info: Any = None) -> None:
        """Record the status line; the headers are not what this test checks."""
        captured["status"] = status
        return None

    return environ, captured, falcon_app.APPLICATION(environ, start_response)


def test_startup_runs_once_before_the_first_request() -> None:
    """T12-02: ``startup()`` precedes every request, and builds the singleton once."""
    result, _ = fetch("/hello")
    assert result.status_code == 200

    first_begin = next(
        index for index, mark in enumerate(falcon_app.EVENTS) if mark.startswith("uow:begin:")
    )
    assert falcon_app.EVENTS.count("settings:started") == 1
    assert falcon_app.EVENTS.index("settings:started") < first_begin


def test_one_scope_per_request_and_a_fresh_object_each_time() -> None:
    """T12-03: two requests must not share a request-scoped object."""
    first, _ = fetch("/hello")
    second, _ = fetch("/hello")

    assert first.json["id"] != second.json["id"], (
        "two requests resolved the same request-scoped object"
    )


def test_the_scope_is_reachable_from_the_host_store() -> None:
    """T12-04: ``req.env["di"]`` holds a live ``ResolutionScope``, not ``None``."""
    environ, captured, body = open_body("/hello")
    scope = environ[falcon_di.SCOPE_KEY]

    assert captured["status"].startswith("200")
    assert isinstance(scope, ResolutionScope), f"the host store held {type(scope).__name__}"
    assert scope.closed is False, "the responder ran against a closed scope"

    list(body)
    body.close()
    assert scope.closed is True


def test_the_object_the_responder_used_is_the_one_the_scope_closed() -> None:
    """T12-04: the id the client sees is the id the teardown committed."""
    result, seen = fetch("/hello")

    assert commit_of(seen) == f"uow:commit:{result.json['id']}"


def test_three_chunks_are_served_with_the_scope_open_then_closed() -> None:
    """T12-05: three chunks, each with ``closed=False``, then one close."""
    result, seen = fetch("/stream")

    lines = result.text.splitlines()
    assert [line.split(":")[1] for line in lines] == ["0", "1", "2"], lines
    assert all(line.endswith("closed=False") for line in lines), lines
    assert seen.index("handler:returned") < seen.index("chunk:0"), (
        "the body was produced before the responder returned; the case is not the trap"
    )
    assert commits(seen) == [f"uow:commit:{lines[0].split(':')[0]}"]
    assert seen.index("chunk:2") < seen.index(commits(seen)[0]), (
        "the scope closed before the last chunk"
    )


def test_the_teardown_marker_follows_the_last_chunk() -> None:
    """T12-12: the ``@pre_destruct`` marker is recorded after the last chunk."""
    _, seen = fetch("/stream")

    marker = commit_of(seen)
    assert seen.index("chunk:2") < seen.index(marker)
    assert marker == seen[max(index for index, mark in enumerate(seen) if mark.startswith("uow:"))]


def test_falcon_process_response_fires_before_the_body_is_streamed() -> None:
    """T12-07: ``process_response`` is measurably too early to own the scope."""
    _, seen = fetch("/stream")

    assert seen.index("falcon:process-response") < seen.index("chunk:0"), (
        "process_response ran after the body, so it could have owned the scope"
    )
    assert seen.index("chunk:2") < seen.index(commit_of(seen))


def test_an_abandoned_body_still_closes_once() -> None:
    """T12-10: a client that hangs up after one chunk gets exactly one close."""
    start = len(falcon_app.EVENTS)
    environ, captured, body = open_body("/stream")
    assert captured["status"].startswith("200")

    first_chunk = next(iter(body))
    seen = marks(falcon_app, start)
    assert "chunk:0" in seen
    assert commits(seen) == [], "the unit must not close before the body is over"
    assert environ[falcon_di.SCOPE_KEY].closed is False

    # The server gives up on the rest of the body: this is ``close()``, the only
    # close point that comes after the chunks already handed out.
    body.close()
    seen = marks(falcon_app, start)
    request_id = first_chunk.decode().split(":", 1)[0]
    assert commits(seen) == [f"uow:commit:{request_id}"]
    assert not [mark for mark in seen if mark.startswith("teardown-error")], (
        "a healthy close reported a teardown failure"
    )
    assert environ[falcon_di.SCOPE_KEY].closed is True


def test_a_failing_responder_closes_once_and_the_error_handler_sees_it_open() -> None:
    """T12-11: 500, exactly one close, and the error handler still sees an open scope."""
    result, seen = fetch("/boom")

    assert result.status_code == 500
    assert "error-handler:closed=False" in seen, seen
    assert len(commits(seen)) == 1, seen
    assert seen.index("error-handler:closed=False") < seen.index(commit_of(seen))


def test_the_singleton_keeps_loop_affinity() -> None:
    """T12-13: the second request reuses the singleton on the same open loop (D-59).

    A bridge that opened a loop per request would leave the second request with a
    different loop — or raise ``RuntimeError: Event loop is closed``.
    """
    first = fetch("/loop")[0].json
    second = fetch("/loop")[0].json

    assert first["loop"] == second["loop"], "the singleton was rebuilt on another loop"
    assert first["running"] is True, "the loop the singleton lives on is not running"
    assert first["is_bridge_loop"] is True
    assert falcon_app.BRIDGE.loop is falcon_app.ADAPTER.bridge.loop


def test_a_failing_teardown_is_reported_not_raised() -> None:
    """IC-8: a ``@pre_destruct`` that raises cannot rewrite a 200 into a 500."""
    result, seen = fetch("/oops")

    assert result.status_code == 200
    assert "teardown-error:DestructError" in seen


def test_a_host_that_raises_before_returning_an_iterable_does_not_leak_the_scope() -> None:
    """IC-7: no iterable reached the server, so the middleware closes the unit itself."""

    def broken(environ: dict[str, Any], start_response: Any) -> Any:
        """Fail the way a host does when routing or binding blows up."""
        raise RuntimeError("host exploded")

    wrapped = falcon_app.ADAPTER.wrap(broken)
    environ = environ_for("/")
    with pytest.raises(RuntimeError, match="host exploded"):
        wrapped(environ, lambda status, headers, exc_info=None: None)

    assert environ[falcon_di.SCOPE_KEY].closed is True


def test_the_in_process_client_runs_the_responder_on_main_thread() -> None:
    """T12-14: the test client is not a threaded server, so T12-01 needs a server."""
    result, _ = fetch("/hello")

    assert result.json["thread"] == "MainThread", (
        "the in-process client produced a worker thread; T12-01 could be faked here"
    )
    assert result.json["built_on"] == "lauren-di-loop"


class _QuietHandler(WSGIRequestHandler):
    """Silence the per-request stderr line: the trace is this suite's assertion."""

    def log_message(self, format: str, *args: Any) -> None:
        """Drop the access log."""
        return None


class _ThreadedServer(ThreadingMixIn, WSGIServer):
    """One thread per request, so the responder is off the calling thread (T12-01)."""

    daemon_threads = True


def test_a_real_server_serves_the_responder_off_the_calling_thread() -> None:
    """T12-01: through a real threaded server — worker thread, one loop, close after."""
    server = make_server(
        "127.0.0.1",
        0,
        falcon_app.APPLICATION,
        server_class=_ThreadedServer,
        handler_class=_QuietHandler,
    )
    serving = threading.Thread(target=server.serve_forever, daemon=True, name="test-server")
    serving.start()
    try:
        url = f"http://127.0.0.1:{server.server_port}/hello"
        with urllib.request.urlopen(url, timeout=TIMEOUT) as response:
            payload = json.loads(response.read())
    finally:
        server.shutdown()
        server.server_close()
        serving.join(TIMEOUT)

    assert payload["thread"] != "MainThread", "the responder ran on the test's own thread"
    assert payload["thread"] != "lauren-di-loop", "the responder ran on the bridge's loop thread"
    assert payload["built_on"] == "lauren-di-loop"
    assert wait_for(f"uow:commit:{payload['id']}"), (
        "the server never reached the close point, so the scope leaked"
    )
