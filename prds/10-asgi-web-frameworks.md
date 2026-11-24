# PRD 10 — ASGI Web Frameworks: Starlette and FastAPI

| | |
|---|---|
| Status | Draft for review |
| Depends on | PRD 09 (integration contract), PRD 03 (scopes), PRD 05 (errors), PRD 07 (overrides) |
| Reference | Starlette 1.10.0, FastAPI 0.165.0, uvicorn 0.54.0, httpx 0.28.1, CPython 3.13.13 |
| Decisions | D-57 (adapter outside the library), D-58 (no ambient scope), D-60 (`startup()` before serving), D-62 (absent host is SKIP), D-63 (framework imports only in fragment/skip fences) |

## 1. Problem Statement

Starlette and FastAPI are the two ASGI frameworks a user is most likely to meet first, and
they are the two that make the integration look easier than it is. Both offer a lifespan
hook that plainly maps onto `startup()`/`shutdown()`. Both offer a middleware base class
that plainly maps onto "open the scope, close the scope". Both offer a dependency system
that plainly duplicates `Inject`. Every one of those "plainly" is wrong in a way that a
test catches and a demo does not.

The three that matter most, measured in this page:

1. **`BaseHTTPMiddleware` closes the scope before the body finishes.** With a
   three-chunk `StreamingResponse`, a scope closed in `dispatch`'s `finally` is closed
   before chunk 1 and chunk 2 are produced, and those chunks observe
   `scope.closed == True` (A-2). A handler that touches a request-scoped resource while
   streaming gets a torn-down object, and nothing raises at the point of the mistake.
2. **`di.Inject(T)` is not `Depends`.** FastAPI hands the *marker object* to the handler —
   `{"type": "_InjectMarker"}` in the measured response — and the annotated form
   `svc: T = Inject(T)` aborts route registration with
   `FastAPIError: Invalid args for response field` (A-5). An adapter that "just uses the
   existing injection" injects nothing and reports success.
3. **A test client without its context manager silently skips the whole lifecycle.** The
   lifespan never runs, so `startup()` never runs, so a test that asserts "the container
   was started once" passes for the wrong reason — or the first request lazily constructs
   a singleton that `shutdown()` will never tear down (A-9).

Each of these has a correct shape in this page, and each correct shape is one assertion in
§10. None of them needs a change to `lauren-di`.

## 2. Goals

| # | Goal |
|---|---|
| G1 | One adapter for both frameworks, in the user's project, that owns the scope for HTTP and WebSocket connections. |
| G2 | A stated close point per middleware style, with the measured order that makes it non-negotiable. |
| G3 | Host objects supplied as declared values, stored in the ASGI state, never discovered by scanning. |
| G4 | FastAPI's own `Depends` keeps working beside `Inject`; `dependency_overrides` and `OverridePlan` stay separate mechanisms with separate rules. |
| G5 | Streaming responses, background tasks and WebSockets each have a measured teardown order, and the adapter's choice is justified against it. |
| G6 | A conformance test module (PRD 09 §5.10) that passes for Starlette and FastAPI and fails when the adapter closes too early. |

## 3. Non-Goals

- **A packaged integration.** No `lauren_di.integrations.starlette`; D-57 keeps the adapter
  in user code, and PRD 09 §6.2 lists why.
- **Replacing FastAPI's dependency injection.** `Depends` keeps its own graph and its own
  overrides; `Inject` is for the container's graph. §5.8 states the seam and A-5 records
  why merging them is not available.
- **Request validation, routing, response models.** The adapter owns one thing: the
  lifetime of a `ResolutionScope`.
- **Per-request overrides.** Overrides are applied when the container is built (PRD 07
  §5.5); a request-time override is a second resolution path.
- **Serving HTTP from the container.** `lauren-di` has no transport vocabulary (PRD 01
  §5.3).

## 4. Prior Art

| Source | What it establishes | What this page adds |
|---|---|---|
| ASGI specification | `scope`, `receive`, `send`; the `http.response.body` message with `more_body` | the exact point at which the last body message is observable to a middleware |
| Starlette lifespan | `async with lifespan(app)` around the server's accept loop | that this is the only hook that can run `startup()` before binding (D-60) |
| Starlette `Middleware` / `BaseHTTPMiddleware` | two ways to write middleware, one wrapping the body and one not | the measured close point of each (§5.4) |
| Starlette `request.state` | per-request storage backed by `scope["state"]` | that the middleware and the handler share one dict, so the scope can be handed over without a ContextVar (D-58) |
| FastAPI `Depends` / `yield` dependencies | per-request setup/teardown, and `dependency_overrides` for tests | that a `yield` dependency's teardown runs after the body **and** after background tasks (§5.5) |
| PRD 09 | IC-1…IC-12, the C-1…C-12 conformance suite, the reference adapter | the Starlette/FastAPI specialisation of every row |

The reference libraries were read, not vendored: the API surface cited here is the
documented one, and every behavioural claim is a transcript in §6.5.

## 5. Proposed API

### 5.1 The adapter: one middleware, one lifespan

```python skip
# myproject/di.py — the whole adapter
import logging

import lauren_di as di
from starlette.types import ASGIApp, Receive, Scope, Send

log = logging.getLogger(__name__)


class DIScopeMiddleware:
    """Owns one ResolutionScope per HTTP request or WebSocket connection."""

    def __init__(self, app: ASGIApp, container: di.DIContainer) -> None:
        self.app = app
        self.container = container

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        resolution = self.container.scope()
        scope.setdefault("state", {})["di"] = resolution      # hand-over point
        try:
            await self.app(scope, receive, send)
        finally:
            for error in await resolution.close():            # IC-8: report, never raise
                log.error("di teardown failed: %r", error)
```

```python skip
# myproject/main.py — composition
from contextlib import asynccontextmanager

import lauren_di as di
from starlette.applications import Starlette

from myproject.di import DIScopeMiddleware


@asynccontextmanager
async def lifespan(app):
    await container.startup()          # D-60: before the server binds
    try:
        yield
    finally:
        for error in await container.shutdown():   # IC-9
            log.error("di shutdown failed: %r", error)


app = Starlette(routes=routes, lifespan=lifespan)
app = DIScopeMiddleware(app, container)            # pure ASGI, outside the router
```

Two functions and one class: that is the integration. The rest of this section is the
reason each line is where it is.

### 5.2 Lifespan maps onto `startup()`/`shutdown()` exactly

Starlette's lifespan is the one hook that runs before the server accepts a connection, so
it is where D-60's eager construction belongs. Measured (A-1):

| Observation | Result |
|---|---|
| order at boot | `lifespan.enter` → `sing.post_construct` → `lifespan.started` |
| order at shutdown | `lifespan.exiting` → `sing.pre_destruct` → `lifespan.exited errors=0` |
| `startup()` called a second time | `no-raise` (idempotent) |
| `shutdown()` called after the lifespan already shut down | `()` — one `pre_destruct`, no error |
| restart (`startup()` after a full `shutdown()`) | `post_construct n=2` |
| a `@post_construct` that raises during lifespan | `RuntimeError` propagates out of the lifespan; **1** instance stays cached |

The last row is the one to encode: a failed `startup()` inside `lifespan` aborts the ASGI
startup — correct — but it leaves a half-built cache behind, so the process must exit
rather than retry (PRD 09 §5.6). A lifespan that catches the exception and yields anyway
turns a boot failure into a stream of 500s.

### 5.3 Where the scope lives: `scope["state"]`, not a ContextVar

Starlette gives every request a `request.state` object backed by `scope["state"]`. It is
created by the framework, shared by everything downstream, and destroyed with the request.
That is exactly the hand-over point the contract needs, and it is why D-58 can be honoured
without a `ContextVar`.

Measured (A-3):

| Observation | Result |
|---|---|
| middleware writes, handler reads | `same_dict=True`, `from_mw=mw` visible in the handler |
| handler writes, middleware reads after `call_next` | `from_handler=handler` visible in the middleware |
| two sequential requests | different `state_id` values — no leakage between requests |
| a bare ASGI scope before any middleware runs | no `state` key until something writes it (`setdefault` is required) |

The consequences for the adapter:

1. **`scope.setdefault("state", {})["di"] = resolution`** — `setdefault`, because a bare
   scope may not have the dict yet (A-3, last row).
2. **The handler resolves through `request.state.di`**, a `ResolutionScope`, never through
   `container.resolve` (IC-5, PRD 09 M-19).
3. **One scope per connection, not per task.** A WebSocket keeps its scope for the life of
   the connection (§5.6); an HTTP request for the life of the body (§5.4).

FastAPI handlers use the same object:

```python skip
from fastapi import Request


@app.get("/orders/{order_id}")
async def read_order(order_id: str, request: Request):
    scope = request.state.di
    orders = await scope.resolve(OrderRepository)     # IC-5
    return {"order": await orders.get(order_id)}
```

### 5.4 The close point: three middleware styles, three orders

This is the centrepiece of the page. The same app, the same three-chunk
`StreamingResponse`, three middleware styles, one scope each (A-2):

| Style | Measured order | Scope closed |
|---|---|---|
| **pure ASGI** (`__call__` wrapping `send`) | `handler.enter, chunk.0, chunk.1, chunk.2, gen.done, pure.before_close, pure.after_close` | **after the last chunk** — correct |
| **`BaseHTTPMiddleware`** (`dispatch` + `call_next`) | `handler.enter, chunk.0, base.dispatch_return, base.finally_before_close, base.finally_after_close, chunk.1 (closed=True), chunk.2 (closed=True), gen.done (closed=True)` | **after chunk 0** — wrong |
| **`BaseHTTPMiddleware` wrapping a pure-ASGI inner app** | `handler.enter, chunk.0, chunk.1, chunk.2, gen.done, base.wrapped_before_close, base.wrapped_after_close` | after the last chunk — correct, because the inner app owns the scope |

The middle row is the trap. `BaseHTTPMiddleware` runs the downstream app in a task and
returns when the *response object* is available, not when the body is finished; its
`finally` therefore executes while the body iterator is still producing. The measured
`closed=True` on chunks 1 and 2 is the proof that a request-scoped resource is already gone
when the framework is still writing.

Three rules follow, and the adapter in §5.1 is the first one:

1. **Own the scope in pure ASGI.** Wrap `send`; close in the `finally` that runs after the
   final `http.response.body` with `more_body` false. This is the only style where the
   close point is the end of the body for every response type.
2. **Never close in `BaseHTTPMiddleware.dispatch`.** If a project already uses it, the
   scope must be owned by an inner pure-ASGI app (row 3), not by the dispatch method.
3. **Never close in a route handler's `finally`.** FastAPI's `yield` dependency is the
   exception, and it is a better close point than a handler `finally` — §5.5 measures why.

### 5.5 Streaming responses and background tasks

FastAPI's `Depends` with `yield` runs its teardown after the response body **and** after the
background task. Measured with a three-chunk stream plus a `BackgroundTask`
(A-6):

```text
YIELD_ORDER ['yielddep.enter', 'chunk.0', 'chunk.1', 'chunk.2', 'gen.done',
             'background.run', 'yielddep.teardown_before_close',
             'yielddep.teardown_after_close', 'outer_mw.exit']
```

Read it as a timeline:

| Event | Meaning for the scope |
|---|---|
| `chunk.0`…`chunk.2`, `gen.done` | the body is produced; `closed=False` throughout |
| `background.run` | the background task runs **before** the dependency's teardown, still with an open scope |
| `yielddep.teardown_before_close` → `teardown_after_close` | the dependency closes the scope last |
| `outer_mw.exit` | the pure-ASGI middleware finishes after that (`body_msgs=4`) |

So there are two correct owners, and they disagree about background tasks:

- **A pure-ASGI middleware (recommended).** It closes after the last body message and
  before the connection's response finishes — background tasks have already been scheduled
  by then in Starlette's `Response.__call__`, and their execution is *inside* the response
  await, so a middleware that awaits the app sees them complete before its `finally`.
- **A `yield` dependency (acceptable for a single scope per route).** It closes after the
  body **and after** background tasks — later than the middleware, and correct for
  resources a background task still needs.

The rule the adapter states: **pick one owner per application.** Two owners means two
scopes or a double close, and the measured orders show exactly how far apart they are.

### 5.6 WebSockets: one scope per connection

A WebSocket is a long-lived "unit" whose close point is the connection, not a message.
Measured (A-7):

| Observation | Result |
|---|---|
| scope identity | one `di_scope` id for the whole first connection (`ws.accepted`, all three messages) |
| service identity across messages | `same_service=True` on every message |
| teardown order | `ws.closed_by_handler` → `conn.before_close (closed=False)` → `req.pre_destruct` → `conn.after_close (closed=True)` |
| second connection | different `di_scope` id, `sing.post_construct n=2` — a new request-scoped instance |

The adapter's gotcha is that `DIScopeMiddleware` must let `scope["type"] == "websocket"`
through to the scope-owning branch (as §5.1 does) and must not assume an
`http.response.body` message will ever arrive: the `finally` is the close point, and it
runs when the connection ends. Because a WebSocket's scope outlives any single handler
call, the "resolve once per unit" rule from PRD 09 §5.7 applies per connection: resolve
what the connection needs once, not in every message loop.

### 5.7 Exceptions: three paths, one close

Measured with a pure-ASGI scope owner (A-8):

| Path | Measured order | Status |
|---|---|---|
| handler returns normally | `mw.normal_exit(closed=False)` → `mw.closed(closed=True)` | 200 |
| handler raises, registered exception handler converts it | `handler.value_error` → `mw.normal_exit(status=418)` → `mw.closed` | 418 — the adapter sees a normal response |
| handler raises, nothing handles it | `mw.exc_propagated(err=RuntimeError, status=None)` → `mw.closed(status=None, closed=True)` | 500 from Starlette's `ServerErrorMiddleware` |

Two consequences:

- **Close in `finally`, not after `await self.app(...)`.** The third row is why: when the
  exception propagates, there is no "after" — only a `finally`.
- **A handled exception is not an adapter event.** Once an exception handler converts the
  error to a 418, the adapter sees an ordinary response and closes ordinarily. The
  contract is unchanged: `scope.close()` runs exactly once per unit, in every path
  (C-7/C-8).

Error mapping (PRD 09 §7): a `DIError` raised inside a handler follows the host's own
exception handlers first. If none is registered, Starlette's `ServerErrorMiddleware` turns
it into a 500 — the correct outcome for a wiring bug, and the reason PRD 09 §7 maps
`MissingProviderError` to 500 rather than 404.

### 5.8 FastAPI: `Depends` beside `Inject`, and two override systems

`Depends` and `Inject` are different injection systems and must stay separate. The
measured evidence is blunt (A-5):

| Attempt | Result |
|---|---|
| `Depends(provider)` where `provider` resolves from the container | works: `{"n": 1, "type": "Singleton"}` |
| `svc = di.Inject(RequestScoped)` as a parameter default | the handler receives `_InjectMarker`: `{"type": "_InjectMarker", "is_marker": true}` |
| `svc: RequestScoped = di.Inject(RequestScoped)` | route registration fails: `FastAPIError: Invalid args for response field` |
| `app.dependency_overrides[provider] = lambda: fake` | the route returns the fake (`n=2`) while `container.resolve(Singleton)` still returns the real one (`n=1`) |

So:

1. **`Inject` is not a FastAPI dependency marker.** FastAPI inspects parameter defaults,
   does not recognise the marker, and either passes it through (unannotated) or refuses to
   build the route (annotated). The adapter therefore resolves from `request.state.di`
   (§5.3) — a plain lookup — rather than trying to smuggle `Inject` through FastAPI's
   signature analysis.
2. **The two override systems are disjoint and both legitimate.** `dependency_overrides`
   replaces a FastAPI provider for the lifetime of a test; `OverridePlan` replaces a
   container row for the lifetime of a container (PRD 07 §5.5). A test that uses both must
   assert both, and never assume one implies the other.
3. **`Depends` may still be used for FastAPI-native things** (auth, request bodies,
   `yield` resources). The seam is: `Depends` builds what FastAPI knows about; `scope`
   builds what the container knows about.

### 5.9 Testing: `ASGITransport` versus `TestClient`

Measured (A-9):

| Harness | Lifespan | `startup()` | Observation |
|---|---|---|---|
| `httpx.ASGITransport(app=app)` | **not run** | not run | requests work, but the first lazy resolve constructs the singleton; a strict-mode `REQUEST` resolve answers 500 `DIScopeViolationError` |
| `TestClient(app)` used **without** `with` | **not run** | not run | same trap; the test asserts nothing about lifecycle |
| `TestClient(app)` used **with** `with` | run | run | `lifespan.startup` before the first request, `lifespan.shutdown errors=0` at exit |

Two rules for a host test suite:

1. **A test client is only honest inside its context manager.** The measured difference is
   not cosmetic: outside it, `startup()` never ran, so a "started once" assertion is about
   a default, not about the adapter.
2. **If the harness has no lifespan, start the container explicitly.** `httpx.ASGITransport`
   does not drive lifespan; a test using it must call `await container.startup()` (and
   `shutdown()`) around its requests, which is exactly the adapter's `start()`/`stop()`
   pair from PRD 09 §5.11.

The strict-mode 500 is worth stating precisely, because it is the message a user will
paste into an issue:

```text
DIScopeViolationError: RequestScoped is request-scoped but was resolved outside a
request or app.scope(); resolve it inside
```

It is the container telling the truth about a test that skipped the lifecycle (C-2).

### 5.10 uvicorn: one container per worker process

`--workers N` forks N processes; each imports the app and runs its own lifespan. Measured
with the app in §5.1 (A-10, A-11):

| Configuration | `startup` pids | `shutdown` pids | `post_construct` / `pre_destruct` | errors |
|---|---|---|---|---|
| `--workers 1` | `[2104500]` | `[2104500]` | 1 / 1 | `[0]` |
| `--workers 2` | `[2104501, 2104502]` | `[2104501, 2104502]` | 2 / 2 | `[0, 0]` |

Over a real socket, the streaming order holds: `chunk i=0,1,2` (all `closed=False`), then
`gen.end (closed=False)`, then `pure.close (body_msgs=4)` — the pure-ASGI owner closes once
per response, after the last body message. That is PRD 09 M-18 reproduced without a test
client.

Two operational notes:

- **Count singletons per process, not per service.** A singleton is per container, and a
  container is per worker; `--workers 4` means four of every singleton. Anything that
  needs to be once per host (a lock in a file, a leader election, an outbound connection
  pool with a global budget) belongs to the host, not to a `SINGLETON`.
- **A keep-alive client sees one worker.** The eight-request burst above landed on a single
  pid in both runs — not because uvicorn balances poorly, but because one HTTP/1.1
  connection is served by one worker. "Every worker serves" is a test to write with
  separate connections, not an assumption to make from a burst.

## 6. Architecture Fit & Feasibility

### 6.1 Layering

```text
uvicorn  ->  Starlette/FastAPI  ->  DIScopeMiddleware (user code)  ->  lauren-di
----------   ------------------     -----------------------------      ---------
lifespan     lifespan hook           container.startup()/shutdown()      startup()/shutdown()
connection   scope["state"]           resolution = container.scope()      scope()
handler      request.state.di         await scope.resolve(T)              ResolutionScope.resolve()
end of body  final send message       await scope.close()                 tuple[DestructError, ...]
```

The adapter imports both; `lauren-di` imports neither (D-57). Every arrow is a method the
library already exports — nothing in this page needs a new public name (PRD 09 §6.2).

### 6.2 What the adapter needs from the host

| Need | Starlette/FastAPI mechanism | If it were missing |
|---|---|---|
| a start hook before binding | `lifespan` | `startup()` would have to run on first request, reintroducing the lazy race (PRD 09 M-1) |
| per-request storage | `scope["state"]` / `request.state` | a `ContextVar` would be needed, which D-58 forbids |
| a body-end signal | the final `http.response.body` | the scope would close at response-object creation (§5.4, middle row) |
| a connection-end signal | the ASGI app returning | a WebSocket scope could not be closed |
| a place to put middleware around the router | `Middleware(...)` / `app.add_middleware` | route handlers would each own a scope — the double-close trap |

All five exist, so no library change is proposed. The one absent capability — a hook that
brackets a streamed body *inside* `BaseHTTPMiddleware` — is the negative result in §6.3.

### 6.3 The negative result

**No hook in Starlette or FastAPI brackets a streamed body from inside
`BaseHTTPMiddleware`.** The measured orders in §5.4 show why: `dispatch`'s `finally` runs
when the downstream app returns the response object, and for a streaming response the body
iterator is produced afterwards. There is no "after body" callback in the middleware
contract, and `BackgroundTask` does not help: it runs after the body but is attached to the
response, not to the middleware.

Three things do bracket the body, in increasing order of intrusiveness:

1. a pure-ASGI middleware wrapping `send` (§5.1) — the recommended one;
2. a FastAPI `yield` dependency, which closes after the body *and* background tasks
   (§5.5) — acceptable when the scope is needed only by one route's subtree;
3. an explicit wrapper around `response.body_iterator` — rejected, because it duplicates
   what the ASGI layer already reports and every wrapper must handle `more_body` itself.

The consequence is stated as a rule rather than a caveat: an adapter must not offer a
`BaseHTTPMiddleware` mode at all, because the mode cannot be made correct.

### 6.4 Cost

The adapter itself adds one scope per request and one dictionary write. Measured on the
container side (PRD 09 §6.4): opening and closing an empty scope costs **1.1 µs**;
open + resolve one request-scoped class + close costs **0.27 ms** idle. The ASGI-specific
overhead is not separately measured here — a pure-ASGI middleware that calls `send` once
per message adds a function call per body chunk, which is below the measurement noise of
the streaming probe (chunks arrive 20 ms apart by construction, so timing them would
measure the fixture).

What is worth publishing: the adapter does **not** add a task, a thread or a queue. It adds
two `await`s and a dict write. Any design that adds more has misunderstood the contract.

### 6.5 Evidence index

Every claim above is asserted by a test in this repository. The measurements were first
taken during authoring, on CPython 3.13.13, with throwaway scripts that were never
committed — so a citation of the form "see probe_01_lifespan.py" named a file no reader
could open. The artifact that keeps each row true now is the example app under
`examples/apps/`, which `nox -s examples` (and the `examples` CI job) runs on every push.
Rows whose only witness was an authoring transcript say so instead of pointing at one.

| Claim | Asserted by | Versions measured |
|---|---|---|
| A-1 (lifespan order, idempotency, restart, failed startup) | `examples/apps/starlette/tests/test_starlette.py::test_lifespan_starts_the_container_before_the_first_request`, `::test_lifespan_shutdown_reports_its_tuple`, `::test_failed_startup_aborts_the_app`; the same three in `examples/apps/fastapi/tests/test_fastapi.py` | starlette 1.10.0, httpx 0.28.1 |
| A-2 (close point for three middleware styles) | `examples/apps/starlette/tests/test_starlette.py::test_streaming_teardown_runs_after_the_last_chunk` (pure ASGI) and `::test_base_http_middleware_closes_before_the_last_chunk` (the negative control); `examples/apps/fastapi/tests/test_fastapi.py::test_scope_owner_is_pure_asgi` | starlette 1.10.0 |
| A-3 (`scope["state"]` sharing and isolation) | `examples/apps/starlette/tests/test_starlette.py::test_scope_is_in_the_request_state`, `::test_one_scope_per_request` | starlette 1.10.0 |
| A-4 (`Depends` + `dependency_overrides`) | `examples/apps/fastapi/tests/test_fastapi.py::test_dependency_overrides_is_container_free` | fastapi 0.165.0, starlette 1.10.0 |
| A-5 (`Inject` is not `Depends`) | `examples/apps/fastapi/tests/test_fastapi.py::test_depends_and_inject_coexist` | fastapi 0.165.0, starlette 1.10.0 |
| A-6 (yield-dependency and background order) | `examples/apps/starlette/tests/test_starlette.py::test_background_task_sees_an_open_scope`; `examples/apps/fastapi/tests/test_fastapi.py::test_background_task_sees_an_open_scope` | fastapi 0.165.0, starlette 1.10.0 |
| A-7 (one scope per connection) | `examples/apps/starlette/tests/test_starlette.py::test_websocket_keeps_one_scope_per_connection`, `::test_a_second_connection_gets_a_new_scope` | starlette 1.10.0 |
| A-8 (normal, handled and unhandled paths close once) | `examples/apps/starlette/tests/test_starlette.py::test_handled_exception_closes_once`, `::test_unhandled_exception_closes_once`, `::test_teardown_is_the_last_event_of_a_request` | starlette 1.10.0 |
| A-9 (lifespan in `ASGITransport` and `TestClient`) | `examples/apps/starlette/tests/test_starlette.py::test_asgi_transport_does_not_run_the_lifespan`; `examples/apps/fastapi/tests/test_fastapi.py::test_asgi_transport_does_not_run_the_lifespan` | starlette 1.10.0, httpx 0.28.1 |
| A-10 (one container per worker; wire-level close point) | `examples/apps/starlette/tests/test_starlette.py::test_one_container_per_worker` (two spawned processes, two pids, two startups) | starlette 1.10.0 |
| A-11 (a keep-alive client sees one worker) | **no shipped assertion** — an eight-request burst landed on one pid during authoring, and §10 does not test it; treat it as an operational note, not a measured invariant | starlette 1.10.0, uvicorn 0.54.0 |

Pinned versions are the ones the examples install: **Starlette 1.10.0**, **FastAPI
0.165.0**, **httpx 0.28.1** (`examples/apps/starlette/requirements.txt`,
`examples/apps/fastapi/requirements.txt`). `uvicorn 0.54.0` appears only in the A-11 row,
which no test covers. A run against a different minor version is a different measurement;
§10's tests fail loudly rather than silently change meaning, and `nox -s examples`
installs exactly the pins above.

## 7. Error Catalog Impact

No new leaf, and no new detail key. The mapping is PRD 09 §7 applied to this host:

| Leaf | Where it surfaces here | Host outcome |
|---|---|---|
| `DIScopeViolationError` | a strict-mode resolve outside a scope — the classic "test client without lifespan" (A-9) | 500; the adapter failed to open a scope |
| `MissingProviderError` | a handler resolves an unregistered token, or a value token with no value | 500 |
| `LifecycleViolationError` | a handler resolves after the body began streaming (the §5.4 middle row) | 500 — and it is the symptom of closing too early |
| `DestructError` | returned by `scope.close()` in the middleware's `finally` | logged, never raised (IC-8) |
| the nine declaration-time leaves | at import or `compile()`, inside the lifespan | the process exits; the server never binds |

Two host-specific notes:

- **Exception handlers are not an error channel for DI.** A `DIError` caught by a FastAPI
  exception handler is converted like any other exception; the adapter does not intercept
  it. What the adapter must not do is convert a teardown `DestructError` into an exception
  handler — teardown happens after the response is committed (§5.5).
- **The 500 from Starlette's `ServerErrorMiddleware` is correct.** An unhandled `DIError`
  is a wiring bug; a 400-family status would tell the client to change its request.

## 8. Phased Implementation Plan

| Phase | Deliverable | Gate | Version |
|---|---|---|---|
| P10-1 | `examples/asgi_starlette/` — the §5.1 adapter, two routes, one streaming route | runs under uvicorn; the streaming route's teardown order is asserted | 0.1.0 |
| P10-2 | `tests/integration/hosts/test_asgi_starlette.py` — C-1…C-12 with `httpx.ASGITransport` | PASS with Starlette installed, SKIP without (D-62) | 0.2.0 |
| P10-3 | the FastAPI variant of the same module, including `Depends` coexistence and `dependency_overrides` | PASS with FastAPI installed | 0.2.0 |
| P10-4 | a WebSocket conformance case (C-3/C-7 for connections) | PASS | 0.2.0 |
| P10-5 | `docs/guides/hosting.md` gains this page's three rules (close in pure ASGI; no `BaseHTTPMiddleware` owner; `Inject` is not `Depends`) | docs build; corpus tests | 0.3.0 |

## 9. Documentation & Convention Updates

| Document | Change | Rule |
|---|---|---|
| `docs/guides/hosting.md` | an ASGI section: the adapter, the three close-point rules, the `Inject`/`Depends` seam | one page owns the contract (PRD 09 §9) |
| `examples/README.md` | the Starlette and FastAPI rows | examples are executed, so the rows cannot rot |
| `docs/guides/testing.md` | "a test client outside its context manager runs no lifespan" | the failure mode, not just the fix |
| `llms.txt`, `llms-full.txt` | regenerate with the new page | never hand-edited |
| this page | `fragment`/`skip` fences only (D-63) — every framework sample is unexecutable by design | a `run` fence must execute with no framework installed |

## 10. Test Plan

### 10.1 Enumerated test cases

`T10-nn` run against both frameworks unless a row names one. They live in
`tests/integration/hosts/test_asgi_*.py` and are skip-guarded per framework (D-62).

| # | Invariant | Fixture | Action | Expected |
|---|---|---|---|---|
| T10-01 | lifespan starts the container | an app with a `@post_construct` counter | start the client inside `with` | counter 1 before the first request |
| T10-02 | lifespan stops it | the same app | exit the `with` | one `@pre_destruct`; `shutdown()` returned `()` |
| T10-03 | a failed startup aborts the app | a `@post_construct` that raises | enter the client | the error propagates; no request is served |
| T10-04 | one scope per request | two requests | compare `request.state.di` ids | different |
| T10-05 | the scope is in the state dict | a handler reading `request.state.di` | one request | not `None`; `isinstance(..., ResolutionScope)` |
| T10-06 | streaming closes after the body | a three-chunk `StreamingResponse`, pure-ASGI owner | read the whole body | the `@pre_destruct` marker appears after the last chunk (A-2, row 1) |
| T10-07 | `BaseHTTPMiddleware` is not an owner | the same route with a dispatch-owned scope (a negative control) | read the body | chunks 1–2 observe `closed=True` — the test asserts the trap, so no adapter reintroduces it |
| T10-08 | background tasks see an open scope | a route with `BackgroundTask` | one request | `background.run closed=False` (A-6) |
| T10-09 | WebSocket keeps one scope | three messages on one connection | compare service ids | identical; teardown after close (A-7) |
| T10-10 | a second connection gets a new scope | reconnect | compare ids | different; `post_construct` count 2 |
| T10-11 | handled exceptions close once | a route raising a handled error | one request | 418; `closed=True` once (A-8) |
| T10-12 | unhandled exceptions close once | a route raising `RuntimeError` | one request | 500; `closed=True` once (A-8) |
| T10-13 | `Depends` and `Inject` coexist | one route using each | one request | the `Depends` route resolves from the container; the `Inject` route returns the marker (documenting A-5) |
| T10-14 | `dependency_overrides` is container-free | a fake provider in `dependency_overrides` | one request | route sees the fake; `container.resolve` sees the real one (A-4) |
| T10-15 | `ASGITransport` needs manual startup | no `with` block | a strict-mode `REQUEST` resolve | 500 `DIScopeViolationError`; after `startup()` it succeeds (A-9) |
| T10-16 | one container per worker | `uvicorn --workers 2` | two `startup` log lines | two pids; `post_construct` count 2 (A-10) |

### 10.2 Acceptance traceability

| Goal | Tests |
|---|---|
| G1 (one adapter, both frameworks) | T10-01 … T10-05 |
| G2 (stated close point per style) | T10-06, T10-07 |
| G3 (host objects as values in state) | T10-04, T10-05 |
| G4 (`Depends` beside `Inject`) | T10-13, T10-14 |
| G5 (streaming, background, websockets) | T10-06, T10-08, T10-09, T10-10 |
| G6 (the conformance module) | T10-01 … T10-16 under D-62 |
