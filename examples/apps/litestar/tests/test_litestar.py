"""Tests for the Litestar example: PRD 11 §5.2 hooks and §10.1 cases.

Each test names the §10.1 row it answers (`T11-nn`), so coverage of the page's
test plan is checkable by grep rather than by memory:

======  ==========================================================
Row     What is measured here
======  ==========================================================
T11-01  ``on_startup`` builds the singleton before the first request
T11-02  ``on_shutdown`` disposes it once and ``shutdown()`` returned ``()``
T11-03  one scope per request
T11-04  ``scope["state"]`` — i.e. ``request.state`` — holds the live scope (LIT-4)
T11-05  the streamed body finishes before the close (LIT-2)
T11-10  the response's ``BackgroundTask`` sees an open unit
T11-11  ``after_response`` is a valid second owner (LIT-3)
T11-12  a ``def`` handler runs on a portal thread, not the loop (LIT-5)
T11-13  the test client runs the hooks only inside ``with`` (LIT-1)
T11-14  a raising handler closes exactly one unit
======  ==========================================================

``T11-16`` (an absent host is SKIP, never a fake) is answered by the module-level
``require("litestar")`` call below.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
from _testing import load, require

require("litestar")

from litestar.testing import TestClient  # noqa: E402

litestar_app = load("litestar", "litestar_app")
litestar_di = load("litestar", "litestar_di")


def _index(events: list[str], prefix: str) -> int:
    """Index of the first event starting with ``prefix``; -1 when it never happened.

    Args:
        events: The app's event log.
        prefix: The event prefix to find.

    Returns:
        The zero-based position, or -1.
    """
    return next((i for i, event in enumerate(events) if event.startswith(prefix)), -1)


def test_boot_hook_starts_the_container_before_the_first_request() -> None:
    """T11-01 / LIT-1: ``on_startup`` constructs the singleton, once, before traffic."""
    built = litestar_app.create_app()
    assert built.recorder.events == [], "nothing runs at import time"

    with TestClient(built.app) as client:
        assert built.recorder.events == ["greeting:start"], built.recorder.events
        assert client.get("/health").status_code == 200
        assert built.recorder.events == ["greeting:start"], "a request rebuilt the singleton"
    assert built.recorder.events[-1] == "greeting:stop"


def test_stop_hook_shuts_the_container_down_once() -> None:
    """T11-02 / IC-9: one ``@pre_destruct``; ``shutdown()`` returned ``()``."""
    reported: list[Any] = []
    built = litestar_app.create_app(report=reported.extend)
    with TestClient(built.app) as client:
        client.get("/hello")
    assert built.recorder.events.count("greeting:stop") == 1
    assert reported == [], f"a healthy shutdown reported teardown errors: {reported}"


def test_one_scope_per_request() -> None:
    """T11-03 / IC-3: two requests, two scopes, two request ids."""
    built = litestar_app.create_app()
    with TestClient(built.app) as client:
        first = client.get("/hello").json()
        second = client.get("/hello").json()

    assert first["request_id"] != second["request_id"]
    events = built.recorder.events
    assert len([e for e in events if e.startswith("unit:open")]) == 2
    assert len([e for e in events if e.startswith("unit:close")]) == 2


def test_scope_is_in_the_host_store() -> None:
    """T11-04 / LIT-4: ``scope["state"]`` (``request.state``) holds the live scope."""
    built = litestar_app.create_app()
    with TestClient(built.app) as client:
        body = client.get("/scope").json()

    assert body["is_resolution_scope"] is True, "the host store held something else"
    assert body["closed"] is False, "the handler ran against a closed scope"
    assert litestar_di.SCOPE_KEY in body["state_keys"]


def test_streaming_closes_after_the_body() -> None:
    """T11-05 / LIT-2: four body messages crossed the seam before the close."""
    built = litestar_app.create_app()
    with TestClient(built.app) as client:
        body = client.get("/stream").text

    lines = body.strip().splitlines()
    assert len(lines) == 3, lines
    close = built.adapter.closes[-1]
    assert close.path == "/stream"
    assert close.body_messages == 4, "three chunks plus the terminator, all before the close"
    assert close.errors == ()

    events = built.recorder.events
    assert _index(events, "chunk:2") < _index(events, "unit:close:"), events


def test_background_task_sees_an_open_scope() -> None:
    """T11-10 / LIT-2: the response's ``BackgroundTask`` runs before the close."""
    built = litestar_app.create_app()
    with TestClient(built.app) as client:
        assert client.get("/stream").status_code == 200

    events = built.recorder.events
    assert "background" in events
    assert _index(events, "background") < _index(events, "unit:close:"), events


def test_after_response_is_a_valid_second_owner() -> None:
    """T11-11 / LIT-3: the hooks-only install closes after the body and the task."""
    built = litestar_app.create_app(hooks_only=True)

    with TestClient(built.app) as client:
        body = client.get("/stream").text
        scope = client.get("/scope").json()

    assert len(body.strip().splitlines()) == 3
    events = built.recorder.events
    assert _index(events, "chunk:2") < _index(events, "background") < _index(events, "unit:close:")
    assert scope["is_resolution_scope"] is True
    assert scope["closed"] is False, "before_request did not publish the unit's scope"


def test_after_response_hook_is_a_single_callable() -> None:
    """The measured correction to §5.2: these hooks take one callable, not a list."""
    hooks = litestar_di.hooks_only(litestar_app.create_container(litestar_app.Recorder()))
    assert callable(hooks["after_response"]), "a list here fails every request on 2.25.0"
    assert callable(hooks["before_request"])
    assert isinstance(hooks["on_startup"], list), "lifespan hooks do take a sequence"


def test_sync_handler_runs_off_the_event_loop() -> None:
    """T11-12 / LIT-5: a ``def`` handler runs on a worker thread, never the loop.

    Measured on litestar 2.25.0 with ``sync_to_thread=True`` the handler lands on
    anyio's worker thread, named ``asyncio_0``; without that flag it also emits
    ``LitestarWarning`` and lands on the router's portal thread. The property
    this example relies on is the weaker, stable one: a ``def`` handler is never
    ``MainThread``, so it cannot block the event loop (LIT-5).
    """
    built = litestar_app.create_app()
    with TestClient(built.app) as client:
        name = client.get("/sync").json()["thread"]

    assert name != "MainThread", "the handler would have blocked the event loop"
    assert name.startswith(("asyncio_", "asyncio-portal-")), name


def test_test_client_runs_the_hooks_only_inside_the_context() -> None:
    """T11-13 / LIT-1: constructing a client starts nothing; entering it starts the app."""
    built = litestar_app.create_app()
    client = TestClient(built.app)
    assert built.recorder.events == [], "the hooks ran before the context was entered"

    with client:
        assert built.recorder.events == ["greeting:start"]
        assert client.get("/health").status_code == 200
    assert built.recorder.events[-1] == "greeting:stop"


def test_raising_handler_closes_exactly_one_unit() -> None:
    """T11-14: a 500 still closes once — an exception is not a leak."""
    built = litestar_app.create_app()
    with TestClient(built.app) as client:
        response = client.get("/boom")

    assert response.status_code == 500
    events = built.recorder.events
    assert len([e for e in events if e.startswith("unit:close")]) == 1, events
    assert built.adapter.closes[-1].errors == ()


def test_failing_teardown_is_reported_not_raised() -> None:
    """IC-8: a raising ``@pre_destruct`` is reported and the response still lands."""
    built = litestar_app.create_app()
    with TestClient(built.app) as client:
        response = client.get("/teardown-error")

    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert "doomed:teardown" in built.recorder.events
    assert "teardown-error:DestructError" in built.recorder.events
    assert len(built.adapter.closes[-1].errors) == 1


def test_concurrent_requests_get_separate_units() -> None:
    """No ambient state: two in-flight requests never share a unit (D-58)."""
    built = litestar_app.create_app()

    async def both() -> list[dict[str, Any]]:
        """Run two requests through the ASGI app at the same time."""
        transport = httpx.ASGITransport(app=built.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            responses = await asyncio.gather(client.get("/hello"), client.get("/hello"))
        await built.container.shutdown()
        return [response.json() for response in responses]

    bodies = asyncio.run(both())
    assert len({body["request_id"] for body in bodies}) == 2, bodies
    assert len([e for e in built.recorder.events if e.startswith("unit:open")]) == 2


def test_scope_lookup_without_the_middleware_explains_the_seam() -> None:
    """A host built without ``install`` gets a diagnosis, not a silent leak."""

    class FakeRequest:
        """The smallest object that answers like a Litestar request."""

        def __init__(self) -> None:
            """Give it an empty host store, as an un-wired app would."""
            self.scope = {"state": {}}

    try:
        litestar_di.current_scope(FakeRequest())
    except RuntimeError as exc:
        assert "install" in str(exc)
    else:  # pragma: no cover - the guard is the point
        raise AssertionError("a request with no scope resolved anyway")
