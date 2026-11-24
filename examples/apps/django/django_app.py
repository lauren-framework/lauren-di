"""The Django app: settings, URLs, providers, and one runnable demo.

Everything Django needs lives in this one module — a settings dict, the
``AppConfig`` whose ``ready()`` starts the container, the providers, the views
and the URLconf — because the interesting part of the example is the adapter
next door, not project scaffolding. :func:`configure` is the operator's half
(``manage.py`` in a real project): it applies the settings and calls
``django.setup()``, which is what populates the registry and runs ``ready()``
(PRD 13 §5.6).

Run it as a script to serve every route through ``django.test.Client``::

    python examples/apps/django/django_app.py
"""

from __future__ import annotations

import asyncio
import pathlib
import sys
import threading
import uuid
from contextvars import ContextVar
from typing import Annotated, Any

# `python examples/apps/django/django_app.py` puts only this directory on
# sys.path, so the repository root (`lauren_di`) and `examples/apps` (`_host`)
# have to be appended before the imports below. The tests get the same two paths
# from `examples/apps/conftest.py`, which is why this block is not a no-op there.
for _path in (
    pathlib.Path(__file__).resolve().parents[3],
    pathlib.Path(__file__).resolve().parents[1],
):
    if str(_path) not in sys.path:
        sys.path.append(str(_path))

import django  # noqa: E402
import django_di  # noqa: E402
from _host import build_container  # noqa: E402
from asgiref.sync import sync_to_async  # noqa: E402
from django.apps import AppConfig  # noqa: E402
from django.conf import settings  # noqa: E402
from django.db import connections  # noqa: E402
from django.http import HttpRequest, HttpResponse, JsonResponse, StreamingHttpResponse  # noqa: E402
from django.urls import path  # noqa: E402
from lauren_di import (  # noqa: E402
    DIContainer,
    Inject,
    Scope,
    injectable,
    post_construct,
    pre_destruct,
)

#: Everything the app needs from Django, in one dict (PRD 13 §5.6).
#: ``MIDDLEWARE`` is empty on purpose: the decorator is the bracket (DJ-7, DJ-8),
#: and a middleware that is not there cannot be declared sync- or async-capable
#: wrongly either (DJ-14).
SETTINGS: dict[str, Any] = {
    "ALLOWED_HOSTS": ["*"],
    "DATABASES": {
        # sqlite3 on purpose: the connection wrapper is what matters here, and
        # it is created lazily, so no file and no migration is needed (DJ-15).
        "default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}
    },
    "DEBUG": False,
    "FEATURE_LABEL": "built",  # captured when the container is built (DJ-18)
    "INSTALLED_APPS": ["django_app.DjangoAppConfig"],
    "MIDDLEWARE": [],
    "ROOT_URLCONF": "django_app",
    "SECRET_KEY": "examples-only-not-a-secret",
    "USE_TZ": True,
}

#: One record per request: ``["open"]`` until the scope closes, then
#: ``[..., "closed"]``. The tests read it to watch the bracket (DJ-7, DJ-9, DJ-10).
TRACE: dict[str, list[str]] = {}

#: What the lifecycle actually did, so a test can assert "exactly once" (IC-9).
LIFECYCLE: dict[str, int] = {"shutdown": 0, "startup": 0}

#: Written inside a view. It is *not* how the scope travels: it leaks out of a
#: sync view into the caller's context, and it does not come back out of a
#: `sync_to_async` thread (PRD 13 §5.4, §5.7 — DJ-13, DJ-21).
MARKER: ContextVar[str] = ContextVar("lauren_di_django_marker", default="unset")


@injectable()
class Engine:
    """The process-wide collaborator: started once, on the bridge loop (IC-1, DJ-6)."""

    def __init__(self) -> None:
        self.loop: Any = None

    @post_construct
    async def start(self) -> None:
        """Record the loop ``startup()`` ran on, so a view can prove where it ran."""
        LIFECYCLE["startup"] += 1
        self.loop = asyncio.get_running_loop()

    @pre_destruct
    async def stop(self) -> None:
        """Count the one shutdown, so a test can assert it is not repeated (IC-9)."""
        LIFECYCLE["shutdown"] += 1


@injectable()
class BuildSettings:
    """A singleton that captures a setting when the container is built (DJ-18).

    ``override_settings`` changes ``settings.FEATURE_LABEL`` inside its block, but
    a value read at build time stays at the build-time value — which is why a test
    that needs different settings builds its *own* container inside the override.
    """

    def __init__(self) -> None:
        self.label = settings.FEATURE_LABEL


@injectable(scope=Scope.REQUEST)
class RequestState:
    """Per-request state: one instance per request, closed at one point (IC-3, IC-7)."""

    def __init__(
        self,
        request: Annotated[HttpRequest, Inject(django_di.REQUEST)],
        connection: Annotated[Any, Inject(django_di.CONNECTION)],
        engine: Engine,
    ) -> None:
        self.request = request
        self.connection = connection
        self.engine = engine
        self.uid = uuid.uuid4().hex
        self.thread = threading.current_thread().name
        # The loop this instance was built on: the bridge loop for a `def` view,
        # the view's own loop for an `async def` view (DJ-6, DJ-11).
        self.loop = asyncio.get_running_loop()
        self.closed = False

    @post_construct
    async def opened(self) -> None:
        """Record the open half of the bracket (IC-3)."""
        TRACE[self.uid] = ["open"]

    @pre_destruct
    async def shut(self) -> None:
        """Record the close half of the bracket, after the response (IC-7, DJ-9)."""
        self.closed = True
        TRACE.setdefault(self.uid, []).append("closed")


class DjangoAppConfig(AppConfig):
    """The host hook: ``ready()`` starts the container and does nothing else (DJ-1).

    Django calls ``ready()`` once per process once the app registry is populated,
    which is also the first moment ``settings`` (and therefore a container built
    from it) can be trusted (PRD 13 §5.2, DJ-1, DJ-5).
    """

    name = "django_app"
    label = django_di.APP_LABEL

    def ready(self) -> None:
        """Build and start the process container (IC-1, DJ-1 … DJ-6)."""
        django_di.start_container(self, build_app_container)


def build_app_container() -> DIContainer:
    """Build the container: host values declared, providers registered (IC-1, IC-6).

    Returns:
        A compiled-on-call container; ``start_container`` compiles and starts it.
    """
    container = build_container(value_tokens=django_di.VALUE_TOKENS)
    container.register(Engine)
    container.register(BuildSettings)
    container.register(RequestState)
    return container


def _snapshot(state: RequestState) -> dict[str, Any]:
    """The evidence a test reads back from a response (DJ-6, DJ-7, DJ-11)."""
    return {
        "built_on_loop": id(state.loop),
        "built_on_thread": state.thread,
        "scope_closed": state.closed,
        "uid": state.uid,
    }


@django_di.with_scope
def sync_view(request: HttpRequest) -> HttpResponse:
    """A ``def`` view: every DI call crosses the bridge to the one loop (DJ-12)."""
    MARKER.set("set-by-sync-view")  # DJ-21: this lands in the caller's context.
    state = django_di.resolve(request, RequestState)
    # Resolving twice must not build twice: the scope is the only cache (IC-5).
    again = django_di.resolve(request, RequestState)
    body = {
        "same_instance": again is state,
        "view": "sync",
        "view_thread": threading.current_thread().name,
        **_snapshot(state),
    }
    return JsonResponse(body)


@django_di.with_scope
async def async_view(request: HttpRequest) -> HttpResponse:
    """An ``async def`` view: ``await scope.resolve`` and no thread hop (DJ-11)."""
    state = await django_di.scope_of(request).resolve(RequestState)
    MARKER.set("set-in-the-view")
    # DJ-13: `thread_sensitive=True` copies this context *into* the thread; the
    # thread's own write does not come back out.
    copied_in, helper_thread = await sync_to_async(
        _overwrite_marker_in_thread, thread_sensitive=True
    )()
    body = {
        "helper_thread": helper_thread,
        "marker_after": MARKER.get(),
        "marker_in_thread": copied_in,
        "view": "async",
        "view_thread": threading.current_thread().name,
        **_snapshot(state),
    }
    return JsonResponse(body)


def _overwrite_marker_in_thread() -> tuple[str, str]:
    """Read the context copied into the thread, overwrite it, and say where it ran."""
    copied_in = MARKER.get()
    MARKER.set("set-in-the-thread")
    return copied_in, threading.current_thread().name


@django_di.with_scope
def stream_view(request: HttpRequest) -> StreamingHttpResponse:
    """A streaming view: the scope outlives the view body (DJ-9)."""
    state = django_di.resolve(request, RequestState)

    def chunks() -> Any:
        # Reads `state` while streaming: that is only safe because the scope is
        # still open here, which is exactly what DJ-9 asks the adapter to do.
        for index in range(3):
            TRACE.setdefault(state.uid, []).append(f"chunk-{index}")
            yield f"chunk-{index} {state.uid}\n".encode()

    response = StreamingHttpResponse(chunks(), content_type="text/plain")
    response["X-UID"] = state.uid
    return response


@django_di.with_scope
def boom_view(request: HttpRequest) -> HttpResponse:
    """A view that raises: the scope still closes before the error leaves (DJ-10)."""
    state = django_di.resolve(request, RequestState)
    raise RuntimeError(f"boom from {state.uid}")


@django_di.with_scope
def orm_view(request: HttpRequest) -> HttpResponse:
    """Serve the host's ORM wrapper, handed in as a value (DJ-15 … DJ-17)."""
    state = django_di.resolve(request, RequestState)
    body = {
        **_snapshot(state),
        "is_this_threads_connection": state.connection is connections["default"],
        "view": "orm",
    }
    return JsonResponse(body)


@django_di.with_scope
def settings_view(request: HttpRequest) -> HttpResponse:
    """Show the build-time setting next to the live one (DJ-18)."""
    captured = django_di.resolve(request, BuildSettings)
    body = {"built_with": captured.label, "live": settings.FEATURE_LABEL, "view": "settings"}
    return JsonResponse(body)


urlpatterns = [
    path("async/", async_view),
    path("boom/", boom_view),
    path("orm/", orm_view),
    path("settings/", settings_view),
    path("stream/", stream_view),
    path("sync/", sync_view),
]


def configure() -> None:
    """Configure settings and populate the app registry exactly once (PRD 13 §5.6).

    ``settings.configure()`` and ``django.setup()`` are both idempotent, so a
    test module, a second import, and ``main()`` can all call this: only the
    first call runs ``AppConfig.ready()`` and therefore only one container is
    ever built (DJ-1).

    This is deliberately *not* called at import time: importing this module must
    not build a container, and the stack lauren-di reads forward references from
    must be the host's own (``manage.py``) rather than this module's namespace.
    """
    if not settings.configured:
        settings.configure(**SETTINGS)
    if django.apps.apps.loading:
        # Populating the registry imports this module, so `ready()` is already
        # on its way; a nested setup() would raise "populate() isn't reentrant".
        return
    django.setup()


def reset_trace() -> None:
    """Forget every request record, so a test can assert on one request only."""
    TRACE.clear()


def main() -> None:
    """Serve every route through the test client and print the evidence."""
    from django.test import AsyncClient, Client

    configure()
    client = Client(raise_request_exception=False)
    print(f"django {django.get_version()} with settings from {__name__}.SETTINGS")
    print(f"container: {django_di.container()!r}")
    print(f"bridge loop: id={id(django_di.bridge().loop)} (one thread per process)")

    for route in ("/sync/", "/orm/", "/settings/", "/boom/"):
        response = client.get(route)
        # Django's own 500 page is long; the status line is the evidence here.
        body = response.content.decode().strip().replace("\n", " ")[:110]
        print(f"{route:<12} {response.status_code} {body}")

    stream = client.get("/stream/")
    print(
        f"{'/stream/':<12} {stream.status_code} "
        + " ".join(chunk.decode().strip() for chunk in stream.streaming_content)
    )

    async def call_async() -> str:
        response = await AsyncClient().get("/async/")
        return response.content.decode().strip()

    print(f"{'/async/':<12} 200 {asyncio.run(call_async())}")

    closed = sum(record[-1] == "closed" for record in TRACE.values())
    print(f"scope opened and closed {closed}/{len(TRACE)} times")
    print(f"lifecycle: {LIFECYCLE}")
    print(f"teardown errors: {django_di.stop_container(django_di.host())}")
    print(f"lifecycle after shutdown: {LIFECYCLE}")


# Nothing is configured or started at import time: the host's entry point
# (`manage.py` in a real project, `main()` here) calls `configure()`, and that
# call is what runs `AppConfig.ready()` and builds the container (DJ-1).


if __name__ == "__main__":
    # A single-file Django app run as a script exists twice: as ``__main__``
    # (this file) and as ``django_app`` (the module the registry imports via
    # INSTALLED_APPS). Aliasing them keeps one set of classes, which matters
    # because a class token is resolved from the names on the calling stack.
    sys.modules.setdefault("django_app", sys.modules[__name__])
    main()
