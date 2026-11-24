"""The Django adapter: the host hook, nothing else (PRD 13 §5).

Three parts, in the order Django calls them:

``start_container``
    ``AppConfig.ready()`` builds, compiles and starts the process container on a
    dedicated loop, once per process (IC-1, IC-9, DJ-1 … DJ-6).
``with_scope``
    a *decorator*, not middleware: it opens one ``HostUnit`` per request after
    Django has bound the request, stores it on ``request``, and closes it at the
    host's finished point (IC-3, IC-4, IC-7, DJ-7 … DJ-10).
``scope_of`` / ``resolve``
    the two call shapes a view needs: the scope itself for an ``async def`` view,
    one bridge hop for a ``def`` view (IC-5, DJ-11 … DJ-13).

Every Django noun lives here and nowhere else; ``lauren_di`` sees no framework
type (D-57). The container is stored on the ``AppConfig`` instance, never in a
module global, so a test can reach it through the app registry (DJ-5).
"""

from __future__ import annotations

import atexit
import functools
import inspect
import logging
from collections.abc import AsyncIterator, Callable, Iterator
from typing import Any

from _host import SCOPE_KEY, HostUnit, SyncBridge
from django.apps import apps
from django.core.exceptions import ImproperlyConfigured
from django.db import connections
from django.http import HttpRequest, HttpResponseBase
from lauren_di import DestructError, DIContainer, DIError, ResolutionScope, Token

LOGGER = logging.getLogger("lauren_di.examples.django")

#: The app config whose ``ready()`` starts the container (PRD 13 §5.2).
APP_LABEL = "django_app"

#: The request itself: handed in per unit, never registered as a provider (IC-6).
REQUEST = Token("django.request")

#: The ORM connection: per thread, never a singleton, never closed here (DJ-15 … DJ-17).
CONNECTION = Token("django.connection")

#: What this host promises to supply for every unit (IC-6).
VALUE_TOKENS = (REQUEST, CONNECTION)

# Attribute names on the ``AppConfig`` and on the request. The scope and the
# unit live in the host's own state, so nothing about them is ambient (D-58).
CONTAINER_ATTR = "di_container"
BRIDGE_ATTR = "di_bridge"
UNIT_ATTR = "di_unit"

__all__ = [
    "APP_LABEL",
    "BRIDGE_ATTR",
    "CONNECTION",
    "CONTAINER_ATTR",
    "REQUEST",
    "UNIT_ATTR",
    "VALUE_TOKENS",
    "bridge",
    "container",
    "host",
    "resolve",
    "scope_of",
    "start_container",
    "stop_container",
    "with_scope",
]


def start_container(config: Any, build: Callable[[], DIContainer]) -> DIContainer:
    """Build, compile and start the process container inside ``ready()``.

    ``ready()`` is synchronous, cannot await, and — measured — runs once per
    process but in *both* the autoreload watcher and its child (PRD 13 §5.2), so
    the container is built here rather than at import time (DJ-1) and the loop
    that owns it is created here too (DJ-6).

    Args:
        config: The ``AppConfig`` instance that will own the container (DJ-5).
        build: A callable returning a fresh, fully registered ``DIContainer``.

    Returns:
        The started container, also stored on ``config``.

    Raises:
        ImproperlyConfigured: The wiring is invalid; raised from ``compile()`` so
            the operator sees it at boot, not on the first request (DJ-2).
        BaseException: Whatever ``startup()`` raised, re-raised unchanged (DJ-4).
    """
    container = build()
    try:
        container.compile()
    except DIError as exc:  # DJ-2: every wiring error is a configuration error.
        raise ImproperlyConfigured(f"lauren-di: the container does not compile: {exc}") from exc

    # DJ-6: one long-lived loop for the process. `asyncio.run()` in `ready()`
    # would leave every loop-bound singleton on a loop that is already closed.
    loop = SyncBridge()
    try:
        loop.run(container.startup())  # DJ-3: `startup()` is a coroutine.
    except BaseException:
        # DJ-4: log and re-raise. `sys.exit()` here would take the autoreload
        # watcher down with the child that actually failed.
        loop.close()
        LOGGER.exception("lauren-di: startup() failed, so this process cannot serve")
        raise

    setattr(config, CONTAINER_ATTR, container)
    setattr(config, BRIDGE_ATTR, loop)
    atexit.register(stop_container, config)
    return container


def stop_container(config: Any) -> tuple[DestructError, ...]:
    """Shut the container down once and stop its loop (IC-9, DJ-6).

    Idempotent: ``atexit`` may run after a test already stopped it, and a
    second ``shutdown()`` on a closed loop must not raise.

    Args:
        config: The ``AppConfig`` instance that owns the container.

    Returns:
        The teardown errors, in the order the container produced them. They are
        logged rather than raised: at process exit there is nobody to tell (IC-8).
    """
    container = getattr(config, CONTAINER_ATTR, None)
    loop: SyncBridge | None = getattr(config, BRIDGE_ATTR, None)
    if container is None or loop is None or loop.loop.is_closed():
        return ()
    try:
        errors = loop.run(container.shutdown())
    finally:
        loop.close()
    for error in errors:
        LOGGER.error("lauren-di: teardown failed: %s", error)
    return errors


def host() -> Any:
    """Return the app config that ``ready()`` populated (IC-1, DJ-5).

    Read through the registry on every call so nothing is cached in a module
    global that a test could not reset.
    """
    return apps.get_app_config(APP_LABEL)


def container() -> DIContainer:
    """The process container (IC-1)."""
    return host().di_container


def bridge() -> SyncBridge:
    """The process bridge loop, the one every sync view resolves on (DJ-6)."""
    return host().di_bridge


def scope_of(request: HttpRequest) -> ResolutionScope:
    """The scope the decorator opened for ``request`` (IC-4, D-58).

    Args:
        request: The request Django bound; the scope travels on it, not in a
            ``ContextVar`` and not in a module global.

    Returns:
        The open scope for this request.

    Raises:
        RuntimeError: The view was not decorated with :func:`with_scope`, so
            there is no ambient scope to fall back on (DJ-22).
    """
    scope = getattr(request, SCOPE_KEY, None)
    if scope is None:
        raise RuntimeError("no scope on this request: decorate the view with `with_scope`")
    return scope


def resolve(request: HttpRequest, token: Any) -> Any:
    """Resolve ``token`` for a ``def`` view: one hop to the bridge loop (DJ-12).

    ``asyncio.run()`` is not an option here: a ``def`` view may be called from
    inside a running loop (an ``async def`` view calling a sync helper), where it
    would raise instead of resolving.

    Args:
        request: The decorated request.
        token: The token or injectable type to resolve (IC-5: through the scope).

    Returns:
        The resolved instance, cached in this request's scope.
    """
    return bridge().run(scope_of(request).resolve(token))


def with_scope(view: Callable[..., HttpResponseBase]) -> Callable[..., HttpResponseBase]:
    """Open one ``HostUnit`` per request and close it at the finished point.

    A decorator rather than middleware, for two measured reasons (PRD 13 §6.3):
    a middleware's ``finally`` runs *before* the first chunk of a streaming
    response, and a middleware that resolves ``REQUEST`` tokens is the middleware
    that decides when the scope opens — the view is the only place that makes
    both true (DJ-7, DJ-8). ``async def`` and ``def`` views get their own wrapper:
    Django runs the first on the handler's loop and the second in a thread, so
    the scope is opened and closed the way that view can afford.

    Args:
        view: A Django view function, sync or async.

    Returns:
        The view wrapped: the same signature, plus one scope per call.

    DJ-9: a streaming response outlives the view, so its ``streaming_content``
    is replaced with a generator that closes the unit in a ``finally``.
    DJ-10: a view that raises still closes the unit before the exception leaves.
    """
    if inspect.iscoroutinefunction(view):

        @functools.wraps(view)
        async def async_view(request: HttpRequest, *args: Any, **kwargs: Any) -> Any:
            unit = _bind(request)
            setattr(request, SCOPE_KEY, await unit.open())
            try:
                response = await view(request, *args, **kwargs)
            except BaseException:
                await unit.close()
                raise
            if getattr(response, "streaming", False):
                response.streaming_content = _close_after_astream(response.streaming_content, unit)
                return response
            response.close()
            await unit.close()
            return response

        return async_view

    @functools.wraps(view)
    def sync_view(request: HttpRequest, *args: Any, **kwargs: Any) -> Any:
        loop = bridge()
        unit = _bind(request)
        setattr(request, SCOPE_KEY, loop.run(unit.open()))
        try:
            response = view(request, *args, **kwargs)
        except BaseException:
            loop.run(unit.close())
            raise
        if getattr(response, "streaming", False):
            response.streaming_content = _close_after_stream(response.streaming_content, unit, loop)
            return response
        # The body is finished, so the response goes first and the scope second:
        # a `finally` that touched the response would outlive it.
        response.close()
        loop.run(unit.close())
        return response

    return sync_view


def _bind(request: HttpRequest) -> HostUnit:
    """Bind one unit to ``request`` and to this thread's ORM wrapper (DJ-15, DJ-16).

    The connection is passed as a *value*: it is thread-affine (a worker thread
    sees a different wrapper, and using another thread's raises ``DatabaseError``),
    so it can be neither a singleton nor a generator provider, and this adapter
    never closes it — ``close_old_connections`` is the host's job (DJ-17).
    """
    process = container()
    unit = HostUnit(
        process,
        values={REQUEST: request, CONNECTION: connections["default"]},
    )
    setattr(request, CONTAINER_ATTR, process)
    setattr(request, UNIT_ATTR, unit)
    return unit


def _close_after_stream(chunks: Iterator[Any], unit: HostUnit, loop: SyncBridge) -> Iterator[Any]:
    """Re-yield ``chunks`` and close the unit after the last one (DJ-9).

    ``finally`` covers the three ways a stream ends: exhausted, ``close()``d by
    the server, or abandoned mid-iteration.
    """
    try:
        yield from chunks
    finally:
        loop.run(unit.close())


async def _close_after_astream(chunks: Any, unit: HostUnit) -> AsyncIterator[Any]:
    """The async twin of :func:`_close_after_stream` (DJ-9, DJ-11).

    ``close`` is awaited on the view's own loop: an async view must not hop to
    the bridge to tear its own scope down.
    """
    try:
        if hasattr(chunks, "__aiter__"):
            async for chunk in chunks:
                yield chunk
        else:
            # A sync iterator in an async view: Django warns about the shape but
            # serves it, so the scope still closes on the loop that opened it.
            for chunk in chunks:
                yield chunk
    finally:
        await unit.close()
