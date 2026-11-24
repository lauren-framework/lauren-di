"""Tests for the Quart example: PRD 11 §5.3 hooks and §10.1 cases.

Each test names the §10.1 row it answers (``T11-nn``), so coverage of the page's
test plan is checkable by grep rather than by memory:

======  ==========================================================
Row     What is measured here
======  ==========================================================
T11-01  ``before_serving`` builds the singleton before the first request
T11-02  ``after_serving`` disposes it once and ``shutdown()`` returned ``()``
T11-03  one scope per request
T11-04  ``g.di`` holds the live scope (QRT-4)
T11-05  the streamed body finishes before the close (QRT-3)
T11-06  ``teardown_request`` is *not* an owner — the negative control
T11-10  the response's background task sees an open scope
T11-12  a ``def`` view runs on ``asyncio_0``, not the loop (QRT-5)
T11-13  the test client runs no serving hook at all (QRT-5)
T11-14  a raising view closes exactly one unit
======  ==========================================================

``T11-16`` (an absent host is SKIP, never a fake) is answered by the module-level
``require("quart")`` call below.

Everything goes through the ASGI seam the wrapper owns, because that seam is the
subject: ``TestClient`` reads the whole body for you, which erases the ordering
this page is about (QRT-2/QRT-3). :func:`quart_di.conversation` drives
``app.asgi_app`` the way a server does and records every message it sends.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Sequence
from typing import Any

from _testing import load, require

require("quart")

quart_app = load("quart", "quart_app")
quart_di = load("quart", "quart_di")


def _index(events: Sequence[str], prefix: str) -> int:
    """Index of the first event starting with ``prefix``; -1 when it never happened.

    Args:
        events: The app's event log.
        prefix: The event prefix to find.

    Returns:
        The zero-based position, or -1.
    """
    return next((i for i, event in enumerate(events) if event.startswith(prefix)), -1)


async def _exercise(
    paths: Sequence[str],
    *,
    teardown_owner: bool = False,
    snapshots: bool = False,
) -> tuple[Any, list[Any]]:
    """Run ``paths`` through one fresh app, bracketed by Quart's serving hooks.

    ``create_app`` never starts the container and ``test_client()`` never runs the
    serving hooks (QRT-5), so :func:`quart_di.startup` / :func:`quart_di.shutdown`
    bracket the run explicitly — those helpers call ``app.startup()`` and
    ``app.shutdown()``, which is what a real server does.

    Args:
        paths: The request paths to walk, in order.
        teardown_owner: Build the ``teardown_request`` negative control instead.
        snapshots: Take a snapshot of the event log at each finished body point.

    Returns:
        The example and one :class:`quart_di.Conversation` per path.
    """
    example = quart_app.create_app(teardown_owner=teardown_owner)
    calls: list[Any] = []
    await quart_di.startup(example.app)
    try:
        for path in paths:
            snapshot = (lambda: list(example.recorder.events)) if snapshots else None
            calls.append(await quart_di.conversation(example.app.asgi_app, path, snapshot=snapshot))
    finally:
        await quart_di.shutdown(example.app)
    return example, calls


def _json(call: Any) -> dict[str, Any]:
    """Decode one conversation's body as JSON.

    Args:
        call: A :class:`quart_di.Conversation`.

    Returns:
        The decoded object.
    """
    return json.loads(call.body)


def test_serving_hook_starts_the_container_before_the_first_request() -> None:
    """T11-01 / QRT-1 / IC-1: ``before_serving`` builds the singleton, once, first."""
    example, calls = asyncio.run(_exercise(["/hello"]))

    assert [call.status for call in calls] == [200]
    events = example.recorder.events
    assert events.count("greeting:start") == 1, f"the singleton was rebuilt: {events}"
    assert events.index("greeting:start") < _index(events, "unit:open"), (
        "the container started after the first request, not before it"
    )


def test_serving_hook_shuts_the_container_down_once() -> None:
    """T11-02 / IC-9: one ``@pre_destruct``; ``shutdown()`` returned ``()``."""
    reported: list[Any] = []
    example = quart_app.create_app(report=reported.extend)

    async def run() -> None:
        """Serve one request between the two serving hooks."""
        await quart_di.startup(example.app)
        try:
            await quart_di.conversation(example.app.asgi_app, "/hello")
        finally:
            await quart_di.shutdown(example.app)

    asyncio.run(run())

    assert example.recorder.events.count("greeting:stop") == 1
    assert reported == [], f"a healthy shutdown reported teardown errors: {reported}"


def test_one_unit_per_request() -> None:
    """T11-03 / IC-3: two requests, two units, two request ids, two closes."""
    example, calls = asyncio.run(_exercise(["/hello", "/hello"]))

    ids = [_json(call)["request_id"] for call in calls]
    assert len(set(ids)) == 2, f"two requests shared a unit: {ids}"
    events = example.recorder.events
    assert len([e for e in events if e.startswith("unit:open")]) == 2
    assert len([e for e in events if e.startswith("unit:close")]) == 2


def test_scope_is_in_the_host_store() -> None:
    """T11-04 / QRT-4: ``g.di`` holds a live ``ResolutionScope``, not ``None``."""
    _, calls = asyncio.run(_exercise(["/scope"]))
    body = _json(calls[0])

    assert body["is_resolution_scope"] is True, "``g.di`` held something else"
    assert body["closed"] is False, "the view ran against a closed scope"
    assert "di" in body["g_keys"], body["g_keys"]


def test_value_token_carries_the_asgi_scope() -> None:
    """IC-6: the request-scoped service holds the ASGI scope it was built in."""
    _, calls = asyncio.run(_exercise(["/echo"]))

    assert _json(calls[0])["path"] == "/echo"


def test_streaming_closes_after_the_body() -> None:
    """T11-05 / QRT-3: four body messages crossed the seam before the close."""
    example, calls = asyncio.run(_exercise(["/stream"]))
    lines = calls[0].body.decode().strip().splitlines()

    assert len(lines) == 3, lines
    assert all("closed=False" in line for line in lines), lines
    close = example.wrapper.closes[-1]
    assert close.path == "/stream"
    assert close.body_messages == 4, "three chunks plus the terminator, all before the close"
    assert close.errors == ()
    events = example.recorder.events
    assert _index(events, "chunk:2") < _index(events, "unit:close:"), events


def test_background_task_sees_an_open_scope() -> None:
    """T11-10 / QRT-2: Quart's background task runs before the wrapper's close."""
    example, calls = asyncio.run(_exercise(["/stream"]))

    assert calls[0].status == 200
    events = example.recorder.events
    assert "background:closed=False" in events, events
    assert _index(events, "background:closed=False") < _index(events, "unit:close:"), events


def test_teardown_request_is_not_an_owner() -> None:
    """T11-06 / QRT-2: the negative control — every chunk sees ``closed=True``.

    This asserts the *trap*, not a desired behaviour: with the unit owned by
    ``teardown_request``, Quart closes it before producing the body, so the
    streamed chunks are generated by an already-disposed service. That is the
    measurement that makes ``app.asgi_app`` the documented install (§5.3).
    """
    example, calls = asyncio.run(_exercise(["/stream"], teardown_owner=True))
    lines = calls[0].body.decode().strip().splitlines()

    assert len(lines) == 3, lines
    assert all("closed=True" in line for line in lines), lines
    events = example.recorder.events
    assert _index(events, "unit:close:") < _index(events, "chunk:0"), (
        f"the trap did not reproduce: {events}"
    )
    assert example.wrapper is None, "the negative control must not also install the wrapper"


def test_sync_view_runs_off_the_event_loop() -> None:
    """T11-12 / QRT-5: a ``def`` view runs on ``asyncio_0``, never ``MainThread``."""
    _, calls = asyncio.run(_exercise(["/sync"]))
    name = _json(calls[0])["thread"]

    assert name == "asyncio_0", f"measured on quart 0.23.1: asyncio_0, got {name}"
    assert name != "MainThread", "the view would have blocked the event loop"


def test_test_client_runs_no_serving_hook() -> None:
    """T11-13 / QRT-5: ``test_client()`` runs neither ``before_serving`` nor the other.

    So an example whose container is started only by the serving hooks will fail
    under the test client: this is why the tests call ``quart_di.startup`` and why
    the helpers exist at all.
    """
    example = quart_app.create_app()

    async def run() -> list[str]:
        """Enter the client, make one request, and report the log at each step."""
        client = example.app.test_client()
        async with client:
            assert example.recorder.events == [], "a serving hook ran on client entry"
            response = await client.get("/hello")
            assert response.status_code == 200
        return list(example.recorder.events)

    events = asyncio.run(run())

    assert "greeting:start" in events, "the request path still constructed the singleton"
    assert "greeting:stop" not in events, "the test client ran after_serving"


def test_raising_view_closes_exactly_one_unit() -> None:
    """T11-14: a 500 still closes once — an exception is not a leak."""
    example, calls = asyncio.run(_exercise(["/boom"]))

    assert calls[0].status == 500
    events = example.recorder.events
    assert len([e for e in events if e.startswith("unit:close")]) == 1, events
    assert example.wrapper.closes[-1].errors == ()


def test_failing_teardown_is_reported_not_raised() -> None:
    """IC-8: a raising ``@pre_destruct`` is reported and the response still lands."""
    example, calls = asyncio.run(_exercise(["/teardown-error"]))

    assert calls[0].status == 200
    assert _json(calls[0]) == {"ok": True}
    events = example.recorder.events
    assert "doomed:teardown" in events
    assert "teardown-error:DestructError" in events
    assert len(example.wrapper.closes[-1].errors) == 1


def test_current_scope_without_the_wrapper_explains_the_seam() -> None:
    """A host built without ``install`` gets a diagnosis, not a silent leak."""
    try:
        quart_di.current_scope()
    except RuntimeError as exc:
        assert "install" in str(exc)
    else:  # pragma: no cover - the guard is the point
        raise AssertionError("a request with no scope resolved anyway")
