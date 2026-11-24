"""aiohttp's close point: the handler owns the body, the middleware closes (§5.5).

aiohttp has one middleware seam and two kinds of response:

* the handler writes the body itself (``prepare`` -> ``write`` -> ``write_eof``), so by
  the time the handler returns the response is finished and the middleware's close is
  at the transport's finished point (AIO-4);
* the handler returns an unwritten ``web.Response`` that aiohttp writes *after* the
  middleware chain unwinds, so a close there would dispose request-scoped services
  before their body goes out (AIO-3).

Every route in this example takes the first shape, and the middleware records which
shape it saw so the difference is measured rather than assumed.
"""

from __future__ import annotations

import logging
import warnings
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from _host import HostUnit
from aiohttp import web
from lauren_di import Token

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from lauren_di import DestructError, DIContainer, ResolutionScope

LOGGER = logging.getLogger("examples.aiohttp")

AIOHTTP_REQUEST_TOKEN = Token("aiohttp.request")
"""The example's one value token: the ``web.Request`` the handler is serving."""

CONTAINER: web.AppKey[Any] = web.AppKey("lauren_di_container")
REPORT: web.AppKey[Any] = web.AppKey("lauren_di_report")

SCOPE_KEY = "di"
"""Where the unit's scope lands on the request, ``request["di"]``."""


def current_scope(request: web.Request) -> ResolutionScope:
    """Return the resolution scope the middleware published on ``request``."""
    scope = request.get(SCOPE_KEY)
    if scope is None:
        raise RuntimeError(
            "no lauren_di scope on this request: the app was built without "
            "`aiohttp_di.install`, so no unit was opened for it"
        )
    return scope


@dataclass
class Closed:
    """What the middleware saw while closing one unit: the measurement, per request."""

    path: str
    after_body: bool
    """``True`` when the handler had already run the body to its end (AIO-4)."""

    errors: tuple[DestructError, ...] = ()
    records: list[str] = field(default_factory=list)


CLOSES: web.AppKey[list[Closed]] = web.AppKey("lauren_di_closes")
"""Every close the middleware performed, in order: the evidence a test reads back."""


async def _start(app: web.Application) -> None:
    """IC-1: the container starts with the host, before any request is served."""
    await app[CONTAINER].startup()


async def _stop(app: web.Application) -> None:
    """IC-2: the container stops with the host, after the last request."""
    errors = await app[CONTAINER].shutdown()
    for error in errors:
        LOGGER.warning("shutdown teardown failed: %r", error)


def install(
    app: web.Application,
    container: DIContainer,
    *,
    report: Callable[[tuple[DestructError, ...] | None], None] | None = None,
) -> None:
    """Attach the unit-of-work middleware and the container lifecycle to ``app``."""
    app[CONTAINER] = container
    app[REPORT] = report
    app[CLOSES] = []
    app.middlewares.append(unit_of_work)
    app.on_startup.append(_start)
    app.on_cleanup.append(_stop)


@web.middleware
async def unit_of_work(
    request: web.Request,
    handler: Callable[[web.Request], Awaitable[web.StreamResponse]],
) -> web.StreamResponse:
    """One unit per request: opened before the handler, closed exactly once after it.

    The close is here — not in a ``finally`` around a background task and not in an
    ``on_response_prepare`` hook — because this is the last point aiohttp gives the
    adapter after the handler, and the handler owns its body.
    """
    container: DIContainer = request.app[CONTAINER]
    unit = HostUnit(
        container,
        values={AIOHTTP_REQUEST_TOKEN: request},
        report=request.app[REPORT],
    )
    scope = await unit.open()
    with warnings.catch_warnings():
        # aiohttp would rather see an ``AppKey`` here, but this is the *request* store and
        # PRD 11 §5.5 publishes ``request["di"]`` for handlers to read back, so the
        # advisory warning about string keys would only be noise.
        warnings.simplefilter("ignore", web.NotAppKeyWarning)
        request[SCOPE_KEY] = scope
    try:
        response = await handler(request)
    except BaseException:
        # A handler that raised wrote nothing: closing here cannot cut a body short.
        await unit.close()
        raise
    after_body = bool(response.prepared)
    errors = await unit.close()
    request.app[CLOSES].append(Closed(path=request.path, after_body=after_body, errors=errors))
    if not after_body:
        # Deliberately no ``request.path`` here: it is client-controlled, and a
        # path with a newline in it can forge a second log line (log injection).
        # The fixed message is what an operator greps for; the path is in the
        # request's own access log, where the server escapes it.
        LOGGER.warning(
            "a handler returned an unwritten response: the unit closed before aiohttp "
            "wrote the body (AIO-3). Own the body in the handler instead."
        )
    return response
