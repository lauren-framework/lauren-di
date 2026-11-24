"""Tests for the aiohttp example: PRD 11 §5.5 hooks and §10.1 cases.

Each test names the §10.1 row it answers (``T11-nn``), so coverage of the page's
test plan is checkable by grep rather than by memory:

======  ==========================================================
Row     What is measured here
======  ==========================================================
T11-01  ``on_startup`` builds the singleton before the server accepts work
T11-02  ``on_cleanup`` disposes it once, after the last request
T11-03  one scope per request
T11-04  ``request["di"]`` holds the live scope (AIO-4)
T11-08  a handler-written body is closed after ``write_eof`` (AIO-4)
T11-09  an unwritten response closes before the body (AIO-3)
T11-10  work after the body, inside the handler, sees an open unit
T11-12  a ``def`` handler runs on ``MainThread`` (AIO-5)
T11-13  ``TestClient`` runs both lifecycle hooks (AIO-1)
T11-14  a failing handler closes exactly once
======  ==========================================================

``T11-16`` (an absent host is SKIP, never a fake) is answered by the module-level
``require("aiohttp")`` call below.

The app runs on a real socket (``TestServer``), so "the middleware closed at the
finished point" is measured against actual transport writes, not a mock.
"""

from __future__ import annotations

import asyncio
import json
import warnings
from collections.abc import Sequence

import pytest
from _testing import load, require
from aiohttp.test_utils import TestClient, TestServer
from lauren_di import DestructError

require("aiohttp")

aiohttp_app = load("aiohttp", "aiohttp_app")
aiohttp_di = load("aiohttp", "aiohttp_di")

pytestmark = pytest.mark.filterwarnings(
    "ignore:Bare functions are deprecated, use async ones:DeprecationWarning"
)
"""aiohttp warns at *registration* when a handler is a plain function (T11-12).

The example registers ``/sync`` on purpose, to measure a ``def`` route, and the
warning is asserted in that test; this marker keeps the other tests' output clean
without hiding the fact.
"""


async def _exercise(paths: Sequence[str]) -> tuple[object, list[tuple[int, str]]]:
    """Serve one fresh app on a real socket and request ``paths`` in order."""
    example = aiohttp_app.create_app()
    client = TestClient(TestServer(example.app))
    await client.start_server()
    calls: list[tuple[int, str]] = []
    try:
        for path in paths:
            response = await client.get(path)
            calls.append((response.status, await response.text()))
    finally:
        await client.close()
    return example, calls


def _index(events: Sequence[str], prefix: str) -> int:
    """Index of the first event starting with ``prefix``; -1 when it never happened."""
    return next((i for i, event in enumerate(events) if event.startswith(prefix)), -1)


def test_container_starts_before_the_host_serves_and_stops_after() -> None:
    """T11-01 / T11-02 / IC-1: the container spans the server's lifetime, not a request."""
    example, calls = asyncio.run(_exercise(["/hello", "/hello"]))

    assert [status for status, _body in calls] == [200, 200]
    events = example.recorder.events
    assert events.count("greeting:start") == 1, "the singleton was rebuilt per request"
    assert events[-1] == "greeting:stop", "the container outlived its last request"
    assert events.index("greeting:stop") > _index(events, "unit:close")


def test_one_unit_per_request_is_opened_and_closed_exactly_once() -> None:
    """T11-03 / IC-3/IC-7: one unit per request, one close per unit, at the finished point."""
    example, calls = asyncio.run(_exercise(["/hello", "/hello"]))

    ids = [body.split('"request_id":"')[1].split('"')[0] for _status, body in calls]
    assert len(set(ids)) == 2, f"two requests shared a unit: {ids}"
    opened = [e for e in example.recorder.events if e.startswith("unit:open")]
    closed = [e for e in example.recorder.events if e.startswith("unit:close")]
    assert len(opened) == len(closed) == 2
    assert [entry.path for entry in example.closes] == ["/hello", "/hello"]
    assert all(entry.after_body for entry in example.closes)
    assert all(entry.errors == () for entry in example.closes)


def test_scope_is_in_the_host_store() -> None:
    """T11-04 / AIO-4: ``request["di"]`` holds a ``ResolutionScope``, not ``None``."""
    _example, calls = asyncio.run(_exercise(["/scope"]))
    status, body = calls[0]
    payload = json.loads(body)

    assert status == 200
    assert payload["is_resolution_scope"] is True, "the host store held something else"
    assert payload["is_the_one_the_handler_uses"] is True
    assert payload["closed"] is False, "the handler ran against a closed scope"


def test_close_happens_after_the_final_body_chunk() -> None:
    """T11-08 / AIO-4: the handler owns the body, so the close is after ``write_eof``."""
    example, calls = asyncio.run(_exercise(["/stream"]))
    status, body = calls[0]

    assert status == 200
    lines = body.splitlines()
    assert len(lines) == 3, lines
    request_ids = {line.split(":", 1)[1] for line in lines}
    assert len(request_ids) == 1
    request_id = request_ids.pop()

    events = example.recorder.events
    assert -1 < _index(events, "chunk:0") < _index(events, "chunk:2")
    assert _index(events, "body:done") < _index(events, f"unit:close:{request_id}")
    assert example.closes[-1].after_body is True


def test_unwritten_response_closes_before_the_body() -> None:
    """T11-09 / AIO-3: a returned ``web.Response`` is written after the middleware."""
    example, calls = asyncio.run(_exercise(["/unwritten"]))
    status, body = calls[0]

    assert status == 200
    assert "written by aiohttp" in body, "the response still reached the client"
    events = example.recorder.events
    assert _index(events, "unit:close") < _index(events, "response-prepare")
    assert example.closes[-1].after_body is False


def test_handler_exception_closes_the_unit_exactly_once() -> None:
    """T11-14: a raising handler wrote no body, so closing early cannot cut one short."""
    example, calls = asyncio.run(_exercise(["/boom"]))
    status, body = calls[0]

    assert status == 400
    assert "handler refused" in body
    assert len([e for e in example.recorder.events if e.startswith("unit:close")]) == 1


def test_value_token_carries_the_aiohttp_request() -> None:
    """IC-6: one value token per unit: the request-scoped service holds the request."""
    example, calls = asyncio.run(_exercise(["/echo"]))
    status, body = calls[0]

    assert status == 200
    assert '"path":"/echo"' in body
    assert '"method":"GET"' in body
    assert example.closes[-1].errors == ()


def test_failing_teardown_is_reported_not_raised() -> None:
    """IC-8: a raising ``@pre_destruct`` is captured in ``scope.errors`` and reported."""
    example, calls = asyncio.run(_exercise(["/teardown-error"]))
    status, body = calls[0]

    assert status == 200
    assert body == '{"ok":true}'
    assert "doomed:teardown" in example.recorder.events
    assert "report:DestructError" in example.recorder.events
    assert len(example.closes[-1].errors) == 1
    assert isinstance(example.closes[-1].errors[0], DestructError)


def test_concurrent_requests_get_separate_units() -> None:
    """T11-03 / D-58: overlapping requests never share a unit."""
    example = aiohttp_app.create_app()

    async def both() -> list[str]:
        client = TestClient(TestServer(example.app))
        await client.start_server()
        try:
            responses = await asyncio.gather(client.get("/hello"), client.get("/hello"))
            return [await response.text() for response in responses]
        finally:
            await client.close()

    bodies = asyncio.run(both())
    ids = {body.split('"request_id":"')[1].split('"')[0] for body in bodies}
    assert len(ids) == 2, "concurrent requests shared a unit"
    assert len(example.closes) == 2
    assert all(entry.after_body for entry in example.closes)


def test_scope_lookup_without_the_middleware_explains_the_seam() -> None:
    """An app built without the adapter gets a diagnosis, not a silent leak."""
    request = {"nothing": True}

    with pytest.raises(RuntimeError, match="install"):
        aiohttp_di.current_scope(request)


def test_work_after_the_body_sees_an_open_unit() -> None:
    """T11-10 / AIO-2: the handler's post-body work runs before the middleware closes."""
    example, calls = asyncio.run(_exercise(["/background"]))
    status, body = calls[0]

    assert status == 200
    assert "delivered" in body
    events = example.recorder.events
    assert "background:closed=False" in events, events
    assert _index(events, "background:closed=False") < _index(events, "unit:close:"), events


def test_sync_handler_runs_on_the_main_thread() -> None:
    """T11-12 / AIO-5: a ``def`` handler runs inline, blocking the loop.

    It also lands on AIO-3's branch: a handler that returns a ``web.Response`` gets
    its body written by aiohttp *after* the middleware closed, which is why every
    other route here owns its body. Both facts are measured, neither is hidden.
    """
    example, calls = asyncio.run(_exercise(["/sync"]))
    status, body = calls[0]

    assert status == 200
    assert '"thread":"MainThread"' in body, body
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        aiohttp_app.create_app()  # registration is what warns, not the request
    assert any("Bare functions are deprecated" in str(entry.message) for entry in caught), (
        "aiohttp stopped deprecating bare-function handlers: re-measure AIO-5"
    )
    assert example.closes[-1].path == "/sync"
    assert example.closes[-1].after_body is False, "a returned response is written later"


def test_test_client_runs_both_lifecycle_hooks() -> None:
    """T11-13 / AIO-1: ``TestClient`` starts the app on ``start_server`` and stops it."""

    async def run() -> tuple[list[str], list[str], list[str]]:
        """Report the event log before, during and after a served request."""
        example = aiohttp_app.create_app()
        before = list(example.recorder.events)
        client = TestClient(TestServer(example.app))
        await client.start_server()
        started = list(example.recorder.events)
        try:
            await client.get("/health")
        finally:
            await client.close()
        return before, started, list(example.recorder.events)

    before, started, after = asyncio.run(run())

    assert before == [], "constructing the app started the container"
    assert started == ["greeting:start"], f"the startup hook did not run first: {started}"
    assert after.count("greeting:stop") == 1, (
        f"the cleanup hook ran {after.count('greeting:stop')}x"
    )
