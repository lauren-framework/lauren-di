# PRD 13 — Django Integration

**Status:** Draft — 2026-11-24
**Depends on:** PRDs 01–09
**Related:** `prds/README.md` §3 (product boundary), §4 (decision register)
**Probes:** `/tmp/compat/django/probe_1.py` … `probe_8.py`, transcripts `out_1.txt` … `out_8.txt`
**Frameworks (pinned):** Django 6.1.3, CPython 3.13.13, `lauren-di` 0.x (stdlib-only)

## 1. Problem Statement

Django is the first host in this corpus that owns *two* execution models at once. A single
`settings.MODULE` can be served by `WSGIHandler` behind gunicorn or by `ASGIHandler` behind
uvicorn/Daphne, and a single URLconf can mix `def` views with `async def` views. Django
adapts each mismatch internally with `asgiref` (`sync_to_async`, `async_to_sync`), so the
same view function runs on a thread pool in one deployment and on the event loop in the
other.

`lauren-di`'s seam is `ResolutionScope`, and `DIContainer.resolve`, `startup` and `shutdown`
are coroutine functions (measured: `inspect.iscoroutinefunction` is `True` for all three).
Django's request path is frequently *synchronous*, so every integration question collapses
into one: where does the host get an event loop, and who closes the scope when the view is a
`StreamingHttpResponse`?

Three properties of Django make the naive answers wrong, and all three were reproduced:

1. **`AppConfig.ready()` is not an async context.** It runs inside `django.setup()` on the
   main thread, before any loop exists (`probe_1.py`, `probe_6.py`). Container construction
   and `compile()` fit there; anything that awaits does not.
2. **The middleware is not a scope bracket.** A middleware's `finally` runs when the *view
   returns*, which for a streaming response is before the first body chunk is produced
   (`probe_4.py`, `probe_7.py`: `mw_exit` precedes `chunk0` in both deployments).
3. **`request_finished` is not a reliable close hook.** Under `WSGIHandler` it never fires at
   all, for a plain or a streaming response (`probe_7.py`: `wsgi.plain=st,mw_exit` and
   `wsgi.stream=st,mw_exit,chunk0,chunk1` — there is no `fin` in either). Under
   `ASGIHandler` and the test client it fires when the view returns, which for a streaming
   response is *before* the first chunk is produced (`probe_4.py`:
   `started,finished,mw_exit,chunk0,chunk1`), and it fires a second time when the response is
   closed (`started,finished,finished,mw_exit,chunk0,chunk1`). A hook that can arrive before
   the body *and* twice cannot own the scope.

The failure modes this page has to design around are therefore: a coroutine that is never
awaited (the WSGI chain returns it and Django raises
`AttributeError: 'coroutine' object has no attribute '_resource_closers'` — reproduced in
`probe_3.py` before the middleware capability flags were fixed), a scope that outlives its
request (ambient `ContextVar` state leaked between two sequential WSGI requests,
`probe_3.py`: `wsgi.leak2=leaked`), and a scope torn down while the response body is still
being produced.

"Compatible" for this page means: a Django project can obtain a `ResolutionScope` per
request, resolve `REQUEST`-scoped tokens inside it, and have that scope closed exactly once
after the response body is finished — in both deployments, for sync views, async views and
streaming views — using only public `lauren-di` API and public Django API, with no
framework noun entering `lauren-di`'s API (D-57) and no packaged adapter (D-57, PRD 09 §5).

## 2. Goals

| Id | Goal | Acceptance criterion |
| --- | --- | --- |
| G1 | One adapter module, no library change | The recipe is a single module the host project owns; `import lauren_di` never imports Django (D-57). |
| G2 | A scope per request, exactly once | Every request that touches the container opens exactly one `ResolutionScope` and closes it exactly once, measured by a close counter under WSGI, ASGI, sync, async and streaming views. |
| G3 | Correct under both deployments | The same recipe module serves `WSGIHandler` and `ASGIHandler` with no deployment-specific branch outside the bridge helper (D-59). |
| G4 | Streaming-safe teardown | For a `StreamingHttpResponse`, the scope is closed after the last chunk, not when the view returns; an abandoned stream still closes (a `finally` around the iterator). |
| G5 | No ambient scope | The adapter passes the scope explicitly (`request.di_scope`, function argument, or a container scope handle); it does not rely on a `ContextVar` (D-58), because that leaks on the WSGI path. |
| G6 | Host owns host resources | A Django database connection is never owned, closed, or cached across threads by the container; it is handed in as a value for the duration of one request (D-61). |
| G7 | Testable without a server | The recipe works under `django.test.Client` and inside `TestCase`, and documents that `override_settings` does not rebuild a container that was built in `ready()` (measured: `build_snapshot=built` vs `override_live=changed`). |

## 3. Non-Goals

- **No `lauren-di-django` distribution.** A packaged adapter would depend on both projects
  and is out of scope for v1 (D-57). This page is a recipe plus conformance tests.
- **No Django app, no middleware shipped by `lauren-di`.** The library stays stdlib-only;
  every Django symbol in this page lives in the host project (D-57, D-63).
- **No `current_scope()` / ambient context API.** v1 adds no `ContextVar` seam (D-58). The
  measured leak on the WSGI path (`probe_3.py`) is the reason.
- **No event-loop bridge in the library.** A synchronous host owns its bridge (D-59); this
  page specifies the two bridge shapes the host may choose between, and measures neither as
  a library feature.
- **No automatic singleton serialisation.** `lauren-di` does not serialise singleton
  construction; a multi-threaded host calls `startup()` before serving (D-60). Django's
  thread pool is multi-threaded, so the recipe must call `startup()` in `ready()`.
- **No management of Django's connection lifecycle.** `close_old_connections`, `CONN_MAX_AGE`
  and `transaction.atomic` stay entirely host-owned (D-61).
- **No coverage of Django's template engine, forms, admin, or the ORM beyond connection
  ownership.** Those are not DI seams.

## 4. Prior Art

| Tool | How it integrates with Django | What this page takes, and what it rejects |
| --- | --- | --- |
| `django-injector` | A single module-level `Injector` built at import, resolved inside views; state is process-global. | Takes the "one container per process, built at startup" shape. Rejects the global lookup: `lauren-di` requires an explicit `ResolutionScope` (D-58). |
| `pinject`-style bindings | Bindings are declared in a module and resolved by convention; no scope object. | Takes declarative registration (PRD 04). Rejects the absence of a scope: `REQUEST`-scoped teardown needs an owner. |
| Django's own `connection` handler | `django.db.connections` is thread-affine storage that lazily creates a wrapper per thread and closes it at request boundaries. | This is the model this page copies for *host-owned* resources: hand the value in, never own it (D-61). |
| `asgiref` adaptation | `sync_to_async(thread_sensitive=True)` / `async_to_sync` bridge the two worlds and copy context into the thread. | Measured here (`probe_3.py`): the copy is one-way and per call; it is not a request-scoped container. |
| ASGI servers (uvicorn, Daphne) | Run each request as its own task, so `ContextVar` state is task-local. | Explains why ASGI *looks* safe and WSGI is not; the recipe must not depend on either. |

The distinguishing property of this page against the async-web and WSGI pages of this series is that Django is the only host
in the corpus where the same deployment can be synchronous or asynchronous per *view*, and
where the response may be a stream. Both facts move the scope bracket away from middleware
and onto the view.

## 5. Proposed API

The "API" of a compatibility page is the adapter surface the host project writes, plus the
requirements it must satisfy. Requirements are local ids (`DJ-n`); they are not decisions
(D-57 … D-63 stay the decisions) and they do not extend `lauren-di`'s 38 public names.

### 5.1 The three-part recipe

The host project owns exactly three pieces:

| Piece | Lives in | Responsibility |
| --- | --- | --- |
| Startup | `AppConfig.ready()` | Build the container, `compile()` it, `await` `startup()` on a private loop, register the container on the app config. |
| Bridge | a small helper module | Provide `run_sync(coro)` for sync views and the sync test client. |
| Per-view scope | a decorator (and optionally a thin middleware that only *attaches* the container) | Open a `ResolutionScope`, run the view, close the scope after the body. |

```python skip
# myproject/di.py — the whole adapter, ~40 lines, owned by the host project.
import asyncio
import functools
import threading

from django.apps import apps
from django.http import StreamingHttpResponse

from lauren_di import DIContainer, ResolutionScope


class _Bridge:
    """One long-lived loop on a dedicated thread (D-59, pattern A)."""

    def __init__(self):
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(
            target=self._loop.run_forever, name="lauren-di-loop", daemon=True
        )

    def start(self):
        self._thread.start()
        return self

    def run(self, coro):
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result()

    def stop(self):
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=5)


def container() -> DIContainer:
    return apps.get_app_config("myproject").di_container
```

The bridge exists because `DIContainer.resolve` is a coroutine function while a Django sync
view is not. `asyncio.run()` per request also works but creates and destroys a loop on every
call; the dedicated-loop shape pays one thread and keeps loop-bound resources alive
(D-59, PRD 09 §5).

Measured with a singleton whose `post_construct` captures the loop and starts a task
(`probe_2.py`, `out_2.txt`):

| `startup()` runs on | Loop the view resolves on | Singleton's task |
| --- | --- | --- |
| `asyncio.run()` inside `ready()` | a different loop (`same: False`) — the ready loop is already closed | dead: `wrong_loop task_done=True` |
| the dedicated loop thread, view calls `bridge.run(...)` | the **same** loop (`same: True`) | alive: `ok task_alive` |
| the dedicated loop thread, view calls `asyncio.run(...)` | a different loop (`same: False`) | not dead, not usable: `wrong_loop task_done=False` |

The third row is the one that costs an afternoon: the singleton is still the same object,
so nothing looks wrong at construction time, and the failure only appears when the object
awaits something owned by the other loop. That is why DJ-6 forbids a per-request loop.

### 5.2 Startup: what `ready()` can and cannot do

Measured (`probe_1.py`, `probe_6.py`): `ready()` runs inside `django.setup()`, on the main
thread, with no running loop, once per process. A second `django.setup()` in the same
process does not run it again (`ready_after_1setup=1`, `ready_after_2setup=1`).

| DJ id | Requirement |
| --- | --- |
| DJ-1 | Build the container in `ready()`, not at import time: import-time construction breaks `override_settings` and runs before the app registry is ready. |
| DJ-2 | Call `container.compile()` in `ready()` so `MissingProviderError`, `CircularDependencyError`, `DuplicateBindingError` and `ProtocolAmbiguityError` surface as `ImproperlyConfigured` at boot, not on the first request. |
| DJ-3 | `compile()` is synchronous and may be called from `ready()`; `startup()` is a coroutine and must be run on the host's bridge (DJ-6). |
| DJ-4 | Never block the reloader child: if `startup()` fails, log and re-raise; do not `sys.exit()` from `ready()`, because autoreload runs `ready()` in both the watcher and the child. |
| DJ-5 | Store the container on the `AppConfig` instance (`apps.get_app_config(...).di_container`); it is process-local and must not be a module global that tests cannot reset. |

```python skip
# myproject/apps.py
import asyncio

from django.apps import AppConfig
from django.core.exceptions import ImproperlyConfigured

from lauren_di import DIError

from .di import _Bridge


class MyProjectConfig(AppConfig):
    name = "myproject"

    def ready(self):
        from .container import build_container  # import late: settings are loaded now

        container = build_container()
        try:
            container.compile()
        except DIError as exc:                     # DJ-2: fail at boot, not per request
            raise ImproperlyConfigured("lauren-di container is invalid") from exc
        bridge = _Bridge().start()
        bridge.run(container.startup())            # DJ-3, DJ-6
        self.di_container = container
        self.di_bridge = bridge
```

`ready()` cannot await, cannot assume a loop, and — measured — runs twice across two
processes under `runserver`'s autoreload (`probe_6.py` run twice: `pid=1842297` and
`pid=1842679`, one `ready()` each). That is why the container is built per process and why
DJ-4 forbids exiting the process from `ready()`.

### 5.3 Per-request scope: a decorator, not a middleware

Because the middleware's `finally` runs before a streamed body is produced, the scope
bracket belongs to the view wrapper (D-61: open after the host has bound the request, close
in a `finally` after the body has finished).

| DJ id | Requirement |
| --- | --- |
| DJ-6 | One bridge per process, started in `ready()` and stopped on `AppConfig` teardown or an `atexit` hook; never create a loop per request unless the host has measured that it can afford it. |
| DJ-7 | Open the scope in the view wrapper, immediately before calling the view; do not open it in middleware, because a middleware that resolves `REQUEST` tokens is also the middleware whose `finally` runs too early. |
| DJ-8 | A middleware may *attach* the container to `request` (so views can reach it), but must not open or close a scope. |
| DJ-9 | Wrap streaming responses so the scope closes after the last chunk: replace `response.streaming_content` with a generator whose `finally` closes the scope. |
| DJ-10 | Closing must be idempotent: measured, `request_finished` can fire twice for one streaming request, and an exception path plus a stream path can both reach the closer. |

```python fragment
def with_scope(view):
    @functools.wraps(view)
    def wrapper(request, *args, **kwargs):
        container = request.di_container
        scope = container.scope()                  # ResolutionScope(container, ...)
        request.di_scope = scope                   # explicit hand-off (D-58, G5)
        try:
            response = view(request, *args, **kwargs)
        except Exception:
            request.di_bridge.run(scope.close())   # DJ-10: closer is idempotent
            raise
        if isinstance(response, StreamingHttpResponse):
            response.streaming_content = _close_after(
                scope, response.streaming_content, request.di_bridge
            )
        else:
            request.di_bridge.run(scope.close())
        return response

    return wrapper


def _close_after(scope, chunks, bridge):
    try:
        yield from chunks                          # sync iterator (WSGI path)
    finally:
        bridge.run(scope.close())                  # DJ-9
```

Two caveats the host must handle, both measured:

- Django 6.1.3 warns `StreamingHttpResponse must consume synchronous iterators in order to
  serve them asynchronously` when a sync generator is served by `ASGIHandler`
  (`out_7.txt`). An async view returning a stream should wrap an async iterator instead.
- `streaming_content` assignment is only legal before the response is returned; assign in
  the wrapper, never in the view.

### 5.4 Sync views, async views, and the bridge

Measured (`probe_3.py`), with an async middleware and a fresh handler per case:

| Case | View thread | Running loop inside the view | `ContextVar` from middleware |
| --- | --- | --- | --- |
| `WSGIHandler` + `def` view | `main` | no | visible (`mw-async`) |
| `ASGIHandler` + `def` view | `worker` (thread pool) | no | visible (`mw-async`) |
| `ASGIHandler` + `async def` view | `main` (loop thread) | yes | visible (`mw-async`) |
| `ASGIHandler` + sync middleware + `async def` view | `main` | yes | visible (`mw-sync`) |

| DJ id | Requirement |
| --- | --- |
| DJ-11 | An `async def` view resolves with `await scope.resolve(Token)`: no bridge, no thread hop. |
| DJ-12 | A `def` view calls the bridge (`bridge.run(...)`); it must not call `asyncio.run()` when it may be running inside a loop (an `async def` view that calls a sync helper). |
| DJ-13 | `sync_to_async(..., thread_sensitive=True)` is the correct way for an async view to call sync code; it copies context *into* the thread but not back out, so a token resolved inside the thread must be returned explicitly, not stashed in a `ContextVar`. |
| DJ-14 | A middleware with `async def __call__` must declare `async_capable = True` and `sync_capable = False`, otherwise the WSGI chain returns a coroutine and Django raises `AttributeError: 'coroutine' object has no attribute '_resource_closers'` (reproduced in `probe_3.py` before the flags were added). |

### 5.5 The ORM connection is host-owned

Measured (`probe_5.py`, sqlite3): `django.db.connections["default"]` is thread-affine — a
worker thread sees a *different* wrapper with no raw connection (`worker.identity_differs=True`,
`worker.fresh_raw_none=True`), and using the main thread's wrapper in a worker raises
`DatabaseError` (`worker.captured_wrapper=DatabaseError`). Closing a connection inside
`transaction.atomic()` leaves `closed_in_transaction=True` and the next query raises
`ProgrammingError` (`close_atomic=ProgrammingError closed_in_tx=True`).

| DJ id | Requirement |
| --- | --- |
| DJ-15 | Hand the connection in as a value for the request scope: `container.register_value(Token(Connection), connections["default"])` inside the wrapper, never a generator provider. A generator provider's finalizer would close a connection the host still owns and may reuse (D-61). |
| DJ-16 | Never register the connection as a `SINGLETON`: the object is per thread and would be handed to another thread, which raises `DatabaseError`. |
| DJ-17 | Never close a Django connection from container teardown. The host closes it via `close_old_connections`; `lauren-di`'s teardown must not call into the ORM. |

### 5.6 Settings, tests, and the reloader

Measured (`probe_6.py`): `override_settings` changes `settings.FEATURE_FLAG` inside the
block (`override_live=changed`) and restores it afterwards (`after_exit=built`), but a value
captured when the container was built stays stale (`build_snapshot=built`).

| DJ id | Requirement |
| --- | --- |
| DJ-18 | A container built in `ready()` is a process singleton; a test that changes settings must build its own container inside the override and use `OverridePlan` (PRD 04) rather than expecting the app container to follow. |
| DJ-19 | `TestCase` subclasses that need `REQUEST` scope should call the same `with_scope` decorator, so the test exercises the production bracket instead of a bespoke one. |
| DJ-20 | `SimpleTestCase` must not touch the container's `startup()`: it may run without a database and without the app's bridge being started by a request. |

### 5.7 The test client

Measured (`probe_8.py`, `out_8.txt`) — `Client`, `AsyncClient` and a threaded Werkzeug server
over the same URLconf:

| Caller | View thread | Running loop | ContextVar left in the caller after the request |
| --- | --- | --- | --- |
| `django.test.Client().get("/sync/")` | `main` (the test's own thread) | no | yes — `client.cv_after=set-by-sync-view` |
| `AsyncClient().get("/async/")` (inside `asyncio.run`) | `main` | yes | n/a |
| threaded WSGI server (`werkzeug`, same app) | `worker` | no | n/a |

The test client reproduces the *ordering* problems this page designs around — the
middleware's `finally` before `chunk0`, `request_finished` before the body
(`probe_4.py`) — but it does not reproduce the server's threading, because it calls the
handler in the test's own context. PRD 12 measured the production shape for the same
WSGI family: `Thread-N (process_request_thread)`. Two consequences:

| DJ id | Requirement |
| --- | --- |
| DJ-21 | A green `TestCase` is not evidence about production threading or about ambient state: the client runs the view on the caller's thread and leaves the caller's context dirty (`client.cv_after=set-by-sync-view`), which is exactly why the scope travels on `request` (D-58). Keep at least one test per deployment that drives the real handler or a real server. |
| DJ-22 | In tests, open the scope the way the wrapper does (`with_scope`) or call `scope()` / `close()` explicitly around the client call; never rely on a `setUp`-installed ambient scope, which the client's shared context would leak into the next test. |

## 6. Architecture Fit & Feasibility

### 6.1 Layering

```
Django project (host)          myproject/di.py            lauren-di
---------------------          ---------------            ---------
AppConfig.ready()      ---->   build + compile     ---->  DIContainer.compile()
                               bridge.run(startup) ---->  DIContainer.startup()
view decorator         ---->   container.scope()   ---->  ResolutionScope
                               scope.resolve(...)  ---->  ResolutionScope.resolve()
streaming wrapper      ---->   scope.close()       ---->  ResolutionScope.close()
```

The host layer is the only one that imports Django; `lauren-di` sees no framework noun
(D-57). The adapter is a *user* of `ResolutionScope`, not a new abstraction (PRD 09 §5).

### 6.2 What the adapter needs from the host

| Need | Django provides | Measured caveat |
| --- | --- | --- |
| A place to build and compile | `AppConfig.ready()` | No loop, main thread, once per process (`probe_1.py`, `probe_6.py`). |
| A loop to await `startup()` | none — the host must create one | Two viable shapes (D-59); `ready()` cannot await. |
| A request-bound object | `HttpRequest` | The middleware `__call__` and the view both receive it; attaching attributes is the documented way to carry state. |
| A bracket around the body | none that is exact | Middleware `finally` precedes the body; `request_finished` is missing under WSGI and duplicated under streaming (`probe_4.py`, `probe_7.py`). |
| A close signal | `HttpResponseBase.close()` (server-driven), `request_finished` (ASGI, and on `close()`) | Not idempotent (`started,finished` then `finished` again). |
| Thread-affine DB access | `django.db.connections` | Per-thread wrapper, `DatabaseError` across threads (`probe_5.py`). |

### 6.3 What the host must not do

| Anti-pattern | Why it fails (measured) |
| --- | --- |
| Open the scope in middleware and close it in `finally` | `mw_exit` precedes `chunk0` in WSGI, ASGI and the test client (`out_4.txt`, `out_7.txt`). |
| Close the scope on `request_finished` only | `WSGIHandler` never sends it (`wsgi.plain=st,mw_exit`); the test client and `ASGIHandler` send it before the body and again on close (`out_4.txt`: `started,finished` then `started,finished,finished`). |
| Rely on a `ContextVar` as the scope | Two sequential WSGI requests leaked the value (`wsgi.leak2=leaked`); the ASGI harness leaked too, because the handler ran in the caller's context (`asgi.leak2=leaked`). |
| Cache a Django connection in a singleton | `worker.captured_wrapper=DatabaseError` (`out_5.txt`). |
| Close the connection in container teardown | `close_atomic=ProgrammingError` — a closed connection inside an atomic block poisons the transaction (`out_5.txt`). |
| `asyncio.run()` inside an `async def` view | `RuntimeError` — a loop is already running in that thread. |

### 6.4 Cost

| Cost | Measurement / note |
| --- | --- |
| One bridge thread per process | One daemon thread; `asyncio.run_coroutine_threadsafe(...).result()` per sync call. PRD 09 §5 measured the dedicated-loop pattern at ≈0.20 ms per call (0.18–0.22), which is the figure this page reuses rather than re-measuring (D-59). |
| One `ResolutionScope` per request | Allocation only; no thread hop in the async path. |
| One decorator frame per view | Negligible; `functools.wraps` preserves the view's signature for Django's URL resolver. |
| No extra query, no extra connection | The connection is passed by reference (`register_value`), so the ORM cost is unchanged. |

### 6.5 Probe inventory

| Probe | Measures | Transcript |
| --- | --- | --- |
| `probe_1.py` | `ready()` call count and thread during `django.setup()` | `out_1.txt` |
| `probe_2.py` | `asyncio.run()` in `ready()` vs the dedicated loop: which loop the view resolves on, and whether the singleton's task survives (three cases) | `out_2.txt` |
| `probe_3.py` | WSGI vs ASGI, sync vs async views, view thread and loop, `ContextVar` across `sync_to_async`, ambient leak | `out_3.txt` |
| `probe_4.py` | `request_started` / `request_finished` with the test client, streaming order, double close | `out_4.txt` |
| `probe_5.py` | ORM wrapper thread affinity, cross-thread `DatabaseError`, close inside `atomic()` | `out_5.txt` |
| `probe_6.py` | `ready()` idempotence per process, two processes, `override_settings` staleness | `out_6.txt` |
| `probe_7.py` | Signal order under `WSGIHandler` and `ASGIHandler`, middleware exit vs first chunk | `out_7.txt` |
| `probe_8.py` | `Client` vs `AsyncClient` vs a threaded Werkzeug server: view thread, running loop, and the `ContextVar` left behind in the caller | `out_8.txt` |

Environment: Django 6.1.3, CPython 3.13.13, sqlite3 backend, `DEBUG=False`,
`ALLOWED_HOSTS=["*"]`, `MIDDLEWARE` overridden per case with
`django.test.utils.override_settings` and a fresh handler per case. Every probe prints a
single `R<n>|…|…` line, so a transcript is one line plus warnings.

## 7. Error Catalog Impact

`lauren-di`'s error leaves keep their identity; the adapter only chooses *where* each one
surfaces. The rule is that a configuration error must become a Django boot error
(`ImproperlyConfigured`) and a per-request programming error must surface as a 500 with the
`lauren-di` leaf as `__cause__`, never as a silent `None`.

| `DIError` leaf | Django surface | Raised at | Notes |
| --- | --- | --- | --- |
| `MissingProviderError` | `django.core.exceptions.ImproperlyConfigured` | `compile()` in `ready()` | DJ-2; boot fails, the server never binds. |
| `CircularDependencyError` | `ImproperlyConfigured` | `compile()` in `ready()` | Carries the cycle path in the message. |
| `DuplicateBindingError` | `ImproperlyConfigured` | `compile()` in `ready()` | Two modules binding the same token. |
| `ProtocolAmbiguityError` | `ImproperlyConfigured` | `compile()` in `ready()` | Two protocols match one token; Django settings cannot disambiguate it. |
| `InvalidProviderError` | `ImproperlyConfigured` | `compile()` in `ready()` | Bad `CustomProvider` implementation. |
| `UnresolvableParameterError` | `ImproperlyConfigured` | `compile()` in `ready()` | A parameter with no provider and no default. |
| `MetadataInheritanceError` | `ImproperlyConfigured` | import time | Raised by `@injectable` on a subclass that widens metadata; surfaces as a Django start-up crash. |
| `DecoratorUsageError` | `ImproperlyConfigured` | import time | Misapplied `@use_value` / `@injectable`; the traceback already names the module. |
| `LifecycleConfigError` | `ImproperlyConfigured` | `ready()` (before `startup()`) | `post_construct` / `pre_destruct` hooks that cannot be honoured. |
| `LifecycleViolationError` | `ImproperlyConfigured` | `ready()` (from `startup()`) | Raised through the bridge; wrap the `Future` result so the traceback crosses the thread. |
| `DestructError` | logged at `ERROR`, not raised | `scope.close()` in the view wrapper | Teardown must not replace a good response with a 500; log with the request id. |
| `DIScopeViolationError` | propagated as `RuntimeError` (500) | `scope.resolve()` inside the view | Using a `REQUEST` token with no scope is a server bug, not a client error; never map it to 400. |
| `OverrideError` | `ImproperlyConfigured` | test `setUp` / `OverridePlan` | Applying an override after the scope was opened. |
| `DIError` (base) | `ImproperlyConfigured` when caught in `ready()`, otherwise propagated | boot or request | Catch the base class at boot only; per request, catch nothing and let Django's 500 handler run. |

Two Django-side errors the recipe must not swallow:

| Django error | Cause in this integration | Handling |
| --- | --- | --- |
| `DatabaseError` (`worker.captured_wrapper=DatabaseError`) | A container-held connection used from another thread | DJ-15/DJ-16: the connection is a per-request value, never a singleton. |
| `AttributeError: 'coroutine' object has no attribute '_resource_closers'` | A middleware with `async def __call__` and wrong capability flags in the WSGI chain | DJ-14: declare `async_capable = True`, `sync_capable = False`. |

## 8. Phased Implementation Plan

Four phases, each independently shippable and each guarded so the conformance suite skips
cleanly when Django is absent (D-62).

### 8.1 Phase 1 — Startup recipe (DJ-1 … DJ-5)

Deliverable: a documentation recipe for `AppConfig.ready()` plus a conformance test that
builds a container, calls `compile()`, runs `startup()` on a dedicated loop, and asserts
`ready()` ran exactly once.

- Guard: `pytest.importorskip("django")` and `django.setup()` against a fixture settings
  module; skip when `django.VERSION < (6, 1)`.
- Exit criteria: `ready_calls == 1` after two `django.setup()` calls in one process; a
  second process reports its own pid and its own single call.

### 8.2 Phase 2 — Request scope (DJ-6 … DJ-10)

Deliverable: the `with_scope` decorator recipe and a conformance test matrix over
`{WSGIHandler, ASGIHandler} × {def view, async def view, StreamingHttpResponse}`.

- Guard: the ASGI half needs a driver that blocks on the second `receive()`; Django asserts
  `Invalid ASGI message after request body: http.request` otherwise (observed in
  `out_7.txt` while the probe was being written).
- Exit criteria: close counter equals 1 per request in all six cells; for the streaming
  cells the counter increments *after* the last chunk is delivered.

### 8.3 Phase 3 — Bridge (DJ-11 … DJ-14)

Deliverable: the `_Bridge` recipe (dedicated loop thread) with a documented alternative
(`asyncio.run()` per call) and the middleware capability-flag note.

- Guard: the bridge test asserts the same thread name serves successive calls; a per-request
  loop would fail that assertion by design.
- Exit criteria: a `def` view resolves a `REQUEST` token and its `pre_destruct` hook runs
  before the response body is flushed to the client.

### 8.4 Phase 4 — ORM, tests, reloader (DJ-15 … DJ-20)

Deliverable: the connection-as-value recipe, the `OverridePlan` testing note, and the
autoreload note (two processes, two containers, one `ready()` each).

- Guard: ORM tests use `pytest.mark.django_db` and skip when `DATABASES == {}`.
- Exit criteria: a worker-thread resolve of the connection token raises nothing; a
  `TestCase` that overrides a setting builds its own container and sees the new value while
  the app container keeps the old one.

### 8.5 Sequencing notes

| Phase | Depends on | Blocks |
| --- | --- | --- |
| 1 | PRD 02, PRD 05 (compile-time validation) | 2, 3, 4 |
| 2 | Phase 1, PRD 03 (scopes) | 3, 4 |
| 3 | Phase 2 | 4 |
| 4 | Phase 2 | — |

## 9. Documentation & Convention Updates

| Document | Change |
| --- | --- |
| `docs/integrations/django.md` (new) | The three-part recipe from §5.1, the `with_scope` decorator, and the `ready()` checklist. No framework import in the library's own docs pages (D-63); this page is a compatibility page, so its `skip` fences may show Django code. |
| `docs/integrations/index.md` | One row: "Django — WSGI and ASGI, sync and async views, streaming responses". |
| `docs/guides/scopes.md` | Add a paragraph: a scope's lifetime is the *response body*, not the view call; hosts with streaming responses must close after the iterator. |
| `docs/guides/async.md` | Add the bridge table (dedicated loop vs `asyncio.run`) with the D-59 pointer; state that `lauren-di` ships no bridge. |
| `docs/reference/errors.md` | Add the `DIError` → `ImproperlyConfigured` mapping from §7 as a host-integration example. |
| `CONTRIBUTING.md` | Note the convention: a compatibility recipe lives in the host project's docs and in `prds/`, never as a shipped module (D-57). |

Conventions this page fixes:

1. Compatibility recipes are documentation plus skip-guarded tests; they add no public name.
2. Every recipe names the probe that justifies it, and every measured claim names a probe
   file plus a transcript (§6.5).
3. Framework imports appear only inside `skip`/`fragment` fences (D-63).
4. A host that owns a resource owns its teardown; the container only holds a reference
   (D-61).

## 10. Test Plan

Rows are `T13-nn`. Every test is skip-guarded (D-62): `pytest.importorskip("django")`,
pinned `Django==6.1.3` in the compatibility extras, and an absent framework is a skip, not a
failure.

| Id | Fixture | Action | Assertion |
| --- | --- | --- | --- |
| T13-01 | settings module + one app with `ready()` | call `django.setup()` twice in one process | `ready()` ran once; `compile()` raised nothing |
| T13-02 | the same app, two subprocesses | run the probe script twice | two pids, one `ready()` each (reload model) |
| T13-03 | `WSGIHandler`, `def` view | request `/` through the handler | view ran on the calling thread, no loop, scope closed once |
| T13-04 | `ASGIHandler`, `def` view | drive one ASGI scope | view ran on a worker thread, scope closed once |
| T13-05 | `ASGIHandler`, `async def` view | drive one ASGI scope | view ran on the loop thread, `await scope.resolve(...)` succeeded |
| T13-06 | `WSGIHandler`, streaming view | consume the whole body | close counter incremented after the last chunk, not before the first |
| T13-07 | `ASGIHandler`, streaming view | drive with a blocking `receive` | same ordering as T13-06; no `AssertionError` from the ASGI handler |
| T13-08 | streaming view, client abandons the body | raise inside `send` / stop iterating | scope closed exactly once (idempotent closer) |
| T13-09 | `django.test.Client`, non-streaming | `client.get("/")` | `request_started` fired; scope closed once even though `request_finished` may not fire (WSGI path) |
| T13-10 | sqlite3 `DATABASES` | resolve the connection token in two threads | two distinct wrappers; no `DatabaseError`; container closed nothing |
| T13-11 | sqlite3, `transaction.atomic()` | container teardown inside the block | no `ProgrammingError`: teardown never touches the ORM (DJ-17) |
| T13-12 | `TestCase` + `override_settings` | resolve a settings-derived token | the test's own container sees the override; the app container keeps the build-time value |
| T13-13 | middleware with `async def __call__`, WSGI chain | request once | no `AttributeError`; the recipe declares `sync_capable = False` (DJ-14) |
| T13-14 | two sequential WSGI requests | set a `ContextVar` in request 1, read it in request 2 | the second request must not see it *if* the adapter is correct — the probe shows the leak, so the test asserts the adapter never relies on it |
| T13-15 | missing provider in a fixture app | boot the app | `ImproperlyConfigured` raised from `ready()`, chained from `MissingProviderError` |
| T13-16 | `Client` and a threaded Werkzeug server over the same URLconf | request `/sync/` through both | the client's view runs on the test's thread and leaves `CV` dirty; the server's runs on a `worker` thread (DJ-21) |

Exit criteria for the page: T13-01 … T13-16 pass with Django 6.1.3 installed, and the whole
file skips (no failures, no errors) when `django` is absent. The measured-claim set backing
this page is `probe_1.py`'s verbose block, `probe_2.py`'s three-row loop comparison, and the
six single-line transcripts `R3` … `R8` in `out_3.txt` … `out_8.txt`; the negative result is
recorded in §6.3 (no host hook brackets a streamed body, so the scope bracket must be the
view wrapper).
