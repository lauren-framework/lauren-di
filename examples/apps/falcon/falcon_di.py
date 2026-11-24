"""The Falcon adapter: a WSGI middleware owns one scope, the body close ends it.

Falcon is a WSGI host, so the same rule as Flask applies: the scope must outlive
the responder. Falcon's own hooks all fire too early — ``process_response`` runs
inside ``App.__call__``, before the WSGI server reads a single chunk of
``resp.stream``, and ``req.context`` is dropped with the request object. Neither
can be the close point, so the Falcon app is wrapped *as a WSGI callable* and
the response iterable's ``close()`` (PEP 3333) closes the scope.

Every container entry point is a coroutine and a WSGI worker thread has no
running loop, so container calls cross into :class:`_host.SyncBridge`: one
long-lived loop and thread for the whole process (PRD 12 §5.2, IC-10).
``asyncio.run`` per call would create and destroy a loop per request, so a
scope opened on one request's loop could never be closed on another's, and each
request would pay for a thread and an event loop.
"""

from __future__ import annotations

import pathlib
import sys
from collections.abc import Callable, Iterable, Iterator
from typing import Any

# ``python examples/apps/falcon/falcon_app.py`` puts the *app* directory on
# ``sys.path``, not the shared examples directory, so the reference adapter is
# located relative to this file before the import below can work.
_APPS_DIR = pathlib.Path(__file__).resolve().parent.parent
if str(_APPS_DIR) not in sys.path:
    sys.path.insert(0, str(_APPS_DIR))

# Same reason, one level up: a source checkout runs the example without
# installing anything. In a real deployment ``lauren_di`` is already importable
# and this branch is skipped because no sibling package directory exists.
_CHECKOUT = _APPS_DIR.parents[1]
if (_CHECKOUT / "lauren_di").is_dir() and str(_CHECKOUT) not in sys.path:
    sys.path.insert(0, str(_CHECKOUT))

from _host import SCOPE_KEY, HostUnit, SyncBridge  # noqa: E402
from lauren_di import DestructError, ResolutionScope, Token  # noqa: E402

#: The token a provider uses to ask for the WSGI environ of its own request.
ENVIRON = Token("wsgi.environ")


#: What a WSGI application must look like to the middleware.
WSGIApp = Callable[[dict[str, Any], Callable[..., Any]], Iterable[bytes]]

#: Where teardown failures go: a metrics hook, a span, or a test's list.
Reporter = Callable[[tuple[DestructError, ...]], None]


class WSGIAdapter:
    """One container, one bridge, one middleware factory for the whole process.

    The app and its tests share this object, so both resolve through the same
    loop and the same middleware configuration — an adapter rebuilt per call is
    a second owner, and a second close point.
    """

    def __init__(self, container: Any, bridge: SyncBridge) -> None:
        """Bind the adapter to an already-started ``container`` and its ``bridge``."""
        self.container = container
        self.bridge = bridge

    def wrap(self, wsgi_app: WSGIApp, *, report: Reporter | None = None) -> WSGIApp:
        """Return ``wsgi_app`` wrapped so every request gets exactly one scope.

        Args:
            wsgi_app: the host's own WSGI callable.
            report: where a failed teardown is reported; ``None`` logs it.

        Returns:
            The middleware, which is itself the WSGI callable to serve.
        """
        return DIMiddleware(wsgi_app, self, report=report)

    def scope(self, environ: dict[str, Any]) -> ResolutionScope:
        """The scope this request was handed, for code that resolves repeatedly."""
        return environ[SCOPE_KEY]

    def resolve(self, environ: dict[str, Any], token: Any) -> Any:
        """Resolve ``token`` in this request's scope, on the shared loop.

        A Falcon resource passes ``req.env`` here; resolving in the responder
        rather than in the middleware keeps the scope lazy, so a request that
        touches nothing costs nothing.
        """
        return self.bridge.run(environ[SCOPE_KEY].resolve(token))


class DIMiddleware:
    """WSGI middleware: open the unit after ``environ`` exists, close it last."""

    def __init__(
        self, app: WSGIApp, adapter: WSGIAdapter, *, report: Reporter | None = None
    ) -> None:
        """Wrap ``app``; the unit is built per request by :meth:`__call__`."""
        self._app = app
        self._adapter = adapter
        self._report = report

    def __call__(
        self,
        environ: dict[str, Any],
        start_response: Callable[..., Any],
    ) -> Iterator[bytes]:
        """Open one unit of work, bind it to ``environ``, and stream the body.

        Args:
            environ: the WSGI environ, already populated by the host — which is
                why the unit is opened here and not earlier.
            start_response: the WSGI ``start_response`` callable.

        Returns:
            The host's response iterable, wrapped so that ending it closes the
            scope.
        """
        # ``values`` has to be complete when the scope opens; a provider cannot
        # discover the environ later, because the scope caches what it resolves.
        unit = HostUnit(
            self._adapter.container,
            values={ENVIRON: environ},
            report=self._report,
        )
        # The host store holds the *scope*, which is what a resource and the
        # §10.1 check look for (T12-04); the unit stays in this closure, because
        # the middleware is the only thing that opens or closes it.
        environ[SCOPE_KEY] = self._adapter.bridge.run(unit.open())
        try:
            body = self._app(environ, start_response)
        except BaseException:
            # No iterable reached the server, so no server will ever close it:
            # without this the scope leaks and its teardown never runs.
            self._close(unit)
            raise
        return self._ending_with(body, unit)

    def _ending_with(self, body: Iterable[bytes], unit: HostUnit) -> Iterator[bytes]:
        """Yield ``body`` and close the unit when the body finishes or is closed."""
        try:
            yield from body
        finally:
            # The host closes its own iterable first, so Falcon's own close
            # callbacks still see a live scope.
            close = getattr(body, "close", None)
            if close is not None:
                close()
            self._close(unit)

    def _close(self, unit: HostUnit) -> None:
        """Close ``unit`` exactly once; ``close`` itself reports any failures (IC-8)."""
        self._adapter.bridge.run(unit.close())
