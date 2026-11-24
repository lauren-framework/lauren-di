"""Tests for the Sanic example: PRD 11 §5.4 hooks and §10.1 cases.

Each test names the §10.1 row it answers (``T11-nn``), so coverage of the page's
test plan is checkable by grep rather than by memory:

======  ==========================================================
Row     What is measured here
======  ==========================================================
T11-01  the lifespan hook builds the singleton before the first request
T11-02  the lifespan hook disposes it once, after the last request
T11-03  one scope per request
T11-04  ``request.ctx.di`` holds the live scope (SNC-3)
T11-05  the streamed body finishes before the close (SNC-3)
T11-07  Sanic's response middleware is *not* an owner (SNC-2)
T11-10  ``app.add_task`` runs *after* the close — measured, not assumed
T11-12  a ``def`` handler runs on ``MainThread`` (SNC-4)
T11-13  the lifespan must be entered explicitly (SNC-5)
T11-14  a failing handler closes exactly once
T11-15  a handler that streams and returns ``None`` (SNC-1)
======  ==========================================================

``T11-16`` (an absent host is SKIP, never a fake) is answered by the module-level
``require("sanic")`` call below.

Everything is driven through the ASGI seam the adapter wraps, because that seam is the
subject: the tests ask "was the unit still open when the final body message crossed
it?", which no app-level hook can answer.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from types import SimpleNamespace

import pytest
from _testing import load, require
from lauren_di import DestructError

require("sanic")

sanic_app = load("sanic", "sanic_app")
sanic_di = load("sanic", "sanic_di")


async def _exercise(paths: Sequence[str]) -> tuple[object, list[object]]:
    """Run ``paths`` through one fresh app, bracketed by the server's lifespan."""
    example = sanic_app.create_app()
    calls = []
    async with sanic_di.asgi_lifespan(example.asgi):
        for path in paths:
            snapshot = lambda: list(example.asgi.events)  # noqa: E731 - a probe, not a name
            calls.append(await sanic_di.asgi_conversation(example.asgi, path, snapshot=snapshot))
    return example, calls


def _index(events: Sequence[str], prefix: str) -> int:
    """Index of the first event starting with ``prefix``; -1 when it never happened."""
    return next((i for i, event in enumerate(events) if event.startswith(prefix)), -1)


def test_container_starts_before_the_host_serves_and_stops_after() -> None:
    """T11-01 / T11-02: the container owns singletons: built once, disposed last."""
    example, calls = asyncio.run(_exercise(["/hello", "/hello"]))

    assert [call.status for call in calls] == [200, 200]
    events = example.recorder.events
    assert events.count("greeting:start") == 1, "the singleton was rebuilt per request"
    assert events[-1] == "greeting:stop", "the container outlived its last request"
    assert events.index("greeting:stop") > _index(events, "unit:close")


def test_one_unit_per_request_is_opened_and_closed_exactly_once() -> None:
    """T11-03 / IC-3/IC-7: one unit of work per request, one close per unit."""
    example, calls = asyncio.run(_exercise(["/hello", "/hello"]))

    ids = [call.body.split(b'"request_id":"')[1].split(b'"')[0].decode() for call in calls]
    assert len(set(ids)) == 2, f"two requests shared a unit: {ids}"
    events = example.recorder.events
    assert len([e for e in events if e.startswith("unit:open")]) == 2
    assert len([e for e in events if e.startswith("unit:close")]) == 2
    for request_id in ids:
        opened = _index(events, f"unit:open:{request_id}")
        closed = _index(events, f"unit:close:{request_id}")
        assert -1 < opened < closed


def test_scope_is_in_the_host_store() -> None:
    """T11-04 / SNC-3: ``request.ctx.di`` holds a ``ResolutionScope``, not ``None``."""
    _, calls = asyncio.run(_exercise(["/scope"]))
    body = calls[0].body

    assert calls[0].status == 200
    assert b'"is_resolution_scope":true' in body, body
    assert b'"is_the_one_the_handler_uses":true' in body, body
    assert b'"closed":false' in body, "the handler ran against a closed scope"


def test_close_happens_after_the_final_body_message() -> None:
    """T11-05 / SNC-3: on the streaming route the unit is open at the last body message."""
    example, calls = asyncio.run(_exercise(["/stream"]))
    call = calls[0]

    assert call.status == 200
    assert call.at_body_end == [("open", "/stream")], "the unit closed at the finished point"
    assert example.asgi.events == [("open", "/stream"), ("close", "/stream")]

    lines = call.body.decode().splitlines()
    assert len(lines) == 3, lines
    request_ids = {line.split(":", 1)[1] for line in lines}
    assert len(request_ids) == 1, "the streamed body was resolved from one unit"
    request_id = request_ids.pop()
    assert example.recorder.events.count(f"unit:close:{request_id}") == 1


def test_response_middleware_runs_before_the_body_so_it_cannot_close() -> None:
    """T11-07 / SNC-2: the tempting close point fires first, so this adapter is not it."""
    example, _calls = asyncio.run(_exercise(["/stream"]))
    events = example.recorder.events

    middleware = _index(events, "response-middleware")
    first_chunk = _index(events, "chunk:0")
    last_chunk = _index(events, "chunk:2")
    closed = _index(events, "unit:close:")

    assert -1 < middleware < first_chunk, "the response middleware ran after the body"
    assert first_chunk < last_chunk < closed, "the unit closed before its body was written"


def test_framework_written_body_is_also_closed_after_its_message() -> None:
    """T11-05: the close point does not depend on who writes the body — /health too."""
    example, calls = asyncio.run(_exercise(["/health"]))

    assert calls[0].status == 200
    assert calls[0].at_body_end == [("open", "/health")]
    assert example.asgi.events == [("open", "/health"), ("close", "/health")]


def test_value_token_carries_the_asgi_scope() -> None:
    """IC-6: one value token, supplied per unit: the service holds the ASGI scope."""
    example, calls = asyncio.run(_exercise(["/echo"]))

    assert calls[0].status == 200
    assert b'"path":"/echo"' in calls[0].body
    assert len(example.asgi.events) == 2


def test_failing_teardown_is_reported_not_raised() -> None:
    """IC-8: a raising ``@pre_destruct`` never corrupts the response, it gets reported."""
    example, calls = asyncio.run(_exercise(["/teardown-error"]))
    call = calls[0]

    assert call.status == 200
    assert call.body == b'{"ok":true}'
    assert len(example.asgi.reported) == 1
    assert isinstance(example.asgi.reported[0], DestructError)
    assert "doomed:teardown" in example.recorder.events


def test_concurrent_requests_get_separate_units() -> None:
    """T11-03 / D-58: two in-flight requests never share a unit."""
    example = sanic_app.create_app()

    async def both() -> list[object]:
        async with sanic_di.asgi_lifespan(example.asgi):
            return await asyncio.gather(
                sanic_di.asgi_conversation(example.asgi, "/hello"),
                sanic_di.asgi_conversation(example.asgi, "/hello"),
            )

    calls = asyncio.run(both())
    ids = {call.body.split(b'"request_id":"')[1].split(b'"')[0] for call in calls}
    assert len(ids) == 2, "concurrent requests shared a unit"
    assert example.asgi.events.count(("open", "/hello")) == 2
    assert example.asgi.events.count(("close", "/hello")) == 2
    assert example.asgi.reported == []


def test_scope_lookup_without_the_wrapper_explains_the_seam() -> None:
    """A host that bypasses the wrapper gets a diagnosis, not a silently broken unit."""
    request = SimpleNamespace(ctx=SimpleNamespace())

    with pytest.raises(RuntimeError, match="DIASGIApp"):
        sanic_di.current_scope(request)


def test_background_task_runs_after_the_close() -> None:
    """T11-10 / SNC-2: measured — ``app.add_task`` sees the unit *closed*.

    PRD 11 §10.1 expects an open unit for every host. On Sanic 25.12.1 that is not
    what ``app.add_task`` does: the task is scheduled from the handler and runs after
    the response has been sent, which is after the ASGI seam closed the unit. The
    test asserts the measurement so the page can be corrected, and so a reader knows
    that Sanic's only background API is not a place to touch request-scoped services.
    """

    async def run() -> tuple[object, object]:
        """Request ``/background`` and let the scheduled task run to completion."""
        example = sanic_app.create_app()
        async with sanic_di.asgi_lifespan(example.asgi):
            call = await sanic_di.asgi_conversation(example.asgi, "/background")
            # The task is scheduled on Sanic's own task registry; one loop turn is
            # not enough, so wait for it the way the server would: until it is done.
            await asyncio.gather(*example.app.tasks)
        return example, call

    example, call = asyncio.run(run())

    assert call.status == 200
    events = example.recorder.events
    assert "background:closed=True" in events, events
    assert _index(events, "unit:close") < _index(events, "background:closed=True"), events


def test_sync_handler_runs_on_the_main_thread() -> None:
    """T11-12 / SNC-4: a ``def`` handler runs inline, on ``MainThread``."""
    _, calls = asyncio.run(_exercise(["/sync"]))

    assert calls[0].status == 200
    assert b'"thread":"MainThread"' in calls[0].body, calls[0].body


def test_the_lifespan_must_be_entered_explicitly() -> None:
    """T11-13 / SNC-5: no client runs the hooks for you; ``asgi_lifespan`` is the seam.

    Sanic ships no in-process client, so the example's tests enter the lifespan
    themselves (which is why ``_exercise`` wraps every request in ``asgi_lifespan``).
    This asserts the *cost* of forgetting, as measured on 25.12.1: the request does
    not quietly run without a container, it fails in Sanic's own loop lookup.
    """
    example = sanic_app.create_app()

    async def run() -> None:
        """Serve one request without entering the lifespan."""
        await sanic_di.asgi_conversation(example.asgi, "/health")

    with pytest.raises(Exception, match="Loop can only be retrieved"):
        asyncio.run(run())
    assert example.recorder.events == [], "the container started without its lifespan"


def test_raising_handler_closes_exactly_one_unit() -> None:
    """T11-14: a 500 still closes once — an exception is not a leak."""
    example, calls = asyncio.run(_exercise(["/boom"]))

    assert calls[0].status == 500
    events = example.recorder.events
    assert len([e for e in events if e.startswith("unit:close")]) == 1, events
    assert example.asgi.events.count(("close", "/boom")) == 1


def test_streaming_handler_may_return_none() -> None:
    """T11-15 / SNC-1: ``request.respond()`` plus ``return None`` is a valid route.

    Sanic logs ``Response was not sent to client`` when a handler both responds and
    returns a response object; returning ``None`` is the supported form, and the body
    is still finished before the close point (SNC-3).
    """
    example, calls = asyncio.run(_exercise(["/stream-none"]))
    call = calls[0]

    assert call.status == 200
    assert len(call.body.decode().splitlines()) == 3, call.body
    assert call.at_body_end == [("open", "/stream-none")], "the unit closed at the finished point"
    assert example.asgi.events == [("open", "/stream-none"), ("close", "/stream-none")]
    assert example.asgi.reported == []


def test_teardown_owner_is_not_the_response_middleware() -> None:
    """T11-07 / SNC-2: the response middleware runs first, so it cannot own the close.

    Kept next to the runtime measurements because it is the *reason* this host uses
    the ASGI seam: the only Sanic hook that fires after the body is the seam itself.
    """
    example, _calls = asyncio.run(_exercise(["/stream-none"]))
    events = example.recorder.events

    assert _index(events, "response-middleware") < _index(events, "chunk:0"), events
    assert events.count("response-middleware") == 1, "one response, one middleware call"
