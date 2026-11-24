# PRD 11 — Async Web Frameworks: Litestar, Quart, Sanic and aiohttp

| | |
|---|---|
| Status | Draft for review |
| Depends on | PRD 09 (integration contract), PRD 03 (scopes), PRD 05 (errors), PRD 07 (overrides), PRD 10 (the ASGI close point) |
| Reference | Litestar 2.25.0, Quart 0.23.1, Sanic 25.12.1, aiohttp 3.14.5, Hypercorn 0.18.0, httpx 0.28.1, CPython 3.13.13 |
| Decisions | D-57 (adapter outside the library), D-58 (no ambient scope), D-60 (`startup()` before serving), D-62 (absent host is SKIP), D-63 (framework imports only in fragment/skip fences) |

## 1. Problem Statement

Four async frameworks, one contract, four different answers to the only question that
matters: *when is this request over*. PRD 10 established the rule for Starlette and
FastAPI — own the scope in a pure-ASGI wrapper, because the framework's own middleware
returns before the body does. This page tests that rule against four more hosts, and
three of the four break in a *different* place.

Measured, in this page:

1. **Quart's `teardown_request` closes before a single chunk is produced.** With a
   three-chunk streamed body the order is `teardown_request`, `teardown_closed`,
   `chunk.0` — and every chunk then observes `closed=True` (QRT-2). Quart's teardown
   runs when the response *object* is finished, not when its body is consumed. A
   request-scoped repository closed there is dead for the whole stream.
2. **Sanic's response middleware fires before the body starts.** `response_mw` is
   measured *before* `chunk.0`, and it is the last framework-level hook before the body
   (SNC-2). Sanic streams through `request.respond()`, which the handler owns; nothing
   in the middleware chain runs after `eof`.
3. **aiohttp's middleware is safe only if the handler writes the body itself.** When the
   handler drives `prepare`/`write`/`write_eof`, the middleware's close lands after the
   last chunk (AIO-2). When the handler *returns* a response object, the close lands
   before aiohttp writes it — measured as `mw.closed_early closed=True` preceding a
   client that still receives `late-body` (AIO-3).
4. **The "sync handler" story is four stories.** Litestar runs a `def` handler in an
   `asyncio-portal-<id>` worker thread with the caller's contextvars copied in (LIT-5);
   Quart runs it in an `asyncio_0` thread (QRT-5); Sanic and aiohttp run it **on the
   event-loop thread** (SNC-4, AIO-5). Any adapter that assumes "sync means thread" is
   wrong twice out of four.

The fix is the same in all four cases and it is already the PRD 09 contract: the host
opens the scope, the adapter closes it where the body ends, and the framework's own
teardown hook is used only for framework-owned cleanup. What changes per host is *which*
hook that is — and for aiohttp it changes per handler style, which is why the page
specifies the handler style rather than leaving it to taste.

## 2. Goals

| # | Goal | Why |
|---|---|---|
| G1 | One adapter shape per framework, four implementations, one contract | the contract is PRD 09's; nothing here is a fifth design |
| G2 | The close point stated per framework, with a measured negative control | a rule without a failing counter-example is a preference |
| G3 | The scope carried in the host's own per-request store (`scope["state"]`, `g`, `request.ctx`, the request mapping) | D-58: no ambient ContextVar, and no new global |
| G4 | `startup()`/`shutdown()` bound to each framework's lifecycle hook, with the measured order | D-60 |
| G5 | The sync-handler model recorded per framework, including the two that block the loop | the adapter's advice differs when the handler runs on the loop |
| G6 | Streaming and background behaviour measured, not assumed | the four hosts disagree about background tasks |

## 3. Non-Goals

- **No new public API.** Every symbol used here exists in PRD 01–04; the page adds
  adapters, not exports.
- **No framework detection or auto-wiring.** An adapter module is chosen by the
  application, not guessed from what is importable (D-57).
- **No ASGI server of our own.** Hypercorn and uvicorn are the reference servers; the
  library never learns what a socket is.
- **No support for `BaseHTTPMiddleware`-shaped ownership in any of the four** — the
  analogue of PRD 10's rejected mode. Where a framework has no post-body hook, the
  adapter wraps the ASGI app or refuses.
- **No WebSocket conformance here.** Connections are PRD 10 §5.6; the three frameworks
  with WebSocket support inherit that rule unchanged.
- **No performance claims beyond the one cost figure in §6.4.** The four hosts are
  measured for *order*, not for throughput.

## 4. Prior Art

Every one of these frameworks has a documented "request finished" hook, and in three of
the four the documentation is describing a different event than the one a scope needs.

| Framework | Hook that looks like the close point | What it actually brackets | Verdict |
|---|---|---|---|
| Litestar 2.25.0 | `after_request(response)` | the handler's return, **before** the body | rejected — LIT-3 |
| Litestar 2.25.0 | `after_response(response)` | after the body **and** after background tasks | usable second owner — LIT-3 |
| Quart 0.23.1 | `teardown_request(exc)` | after the response object, **before** the body | rejected — QRT-2 |
| Sanic 25.12.1 | `@app.middleware("response")` | after the handler returns `response`, before `send` | rejected — SNC-2 |
| aiohttp 3.14.5 | `@web.middleware` returning `StreamResponse` | after `await handler(request)`, before aiohttp writes a returned response | conditional — AIO-2 / AIO-3 |

The pattern is the same one PRD 10 found in `BaseHTTPMiddleware`: a hook named after the
*response* is a hook on the response *object*. A streamed body outlives its object. Only
a wrapper that awaits the body — an ASGI middleware for the three ASGI frameworks, the
handler's own write loop for aiohttp — sees the real end.

### 4.1 What each framework gives that the contract can use

| Need | Litestar | Quart | Sanic | aiohttp |
|---|---|---|---|---|
| boot hook | `on_startup=[...]` | `@before_serving` | `@before_server_start` | `app.on_startup.append` |
| stop hook | `on_shutdown=[...]` | `@after_serving` | `@after_server_stop` | `app.on_shutdown.append` |
| per-request store | `scope["state"]` → `request.state` | `g` (app-context ContextVar) | `request.ctx` | the request mapping |
| ASGI wrapper seam | `ASGIMiddleware` | `app.asgi_app` | not exposed | not applicable |
| body ownership | framework | framework | the handler | the handler |

## 5. Proposed API

### 5.1 The comparison matrix

One row per framework, every cell measured (§6.5). This is the table the rest of the
page defends.

| Framework | Lifecycle hook | Correct close point | Scope store | Sync handler runs on | Test client runs the hooks |
|---|---|---|---|---|---|
| **Litestar 2.25.0** | `on_startup` / `on_shutdown` | `ASGIMiddleware` or `after_response` (LIT-2, LIT-3) | `scope["state"]` → `request.state` (LIT-4) | `asyncio-portal-<id>` thread (LIT-5) | only inside `with` (LIT-1) |
| **Quart 0.23.1** | `before_serving` / `after_serving` | pure-ASGI wrapper on `app.asgi_app` (QRT-3) | `g` (QRT-4) | `asyncio_0` thread (QRT-5) | never (QRT-5) |
| **Sanic 25.12.1** | `before_server_start` / `after_server_stop` | server-level ASGI wrapper, since no hook follows the body (SNC-2) | `request.ctx` (SNC-3) | the loop thread, `MainThread` (SNC-4) | no client ships with Sanic (SNC-5) |
| **aiohttp 3.14.5** | `on_startup` / `on_shutdown` | the handler's write loop, closed in middleware (AIO-2) | `request["di"]` (AIO-4) | the loop thread, `MainThread` (AIO-5) | yes, `TestClient(TestServer(...))` (AIO-1) |

The same measurements read the other way round, dimension by dimension:

| | Litestar 2.25.0 | Quart 0.23.1 | Sanic 25.12.1 | aiohttp 3.14.5 |
|---|---|---|---|---|
| **startup / shutdown** | `on_startup` / `on_shutdown` | `before_serving` / `after_serving` | `before_server_start` / `after_server_stop` | `on_startup` / `on_shutdown` |
| **measured boot order** | `on_startup` → `post_construct` → `on_startup.done` (LIT-1) | `before_serving` → `post_construct` → done (QRT-1) | `before_server_start` → `post_construct` → done (SNC-1) | `on_startup` → `post_construct` → done (AIO-1) |
| **correct close point** | `ASGIMiddleware` (LIT-2) or `after_response` (LIT-3) | pure-ASGI wrapper on `app.asgi_app` (QRT-3) | none in-framework: ASGI wrapper only | the handler's write loop, closed in middleware (AIO-2) |
| **wrong close point** | `after_request` (LIT-3) | `teardown_request` (QRT-2) | `@app.middleware("response")` (SNC-2) | middleware + a returned response (AIO-3) |
| **scope store** | `scope["state"]` / `request.state` — one dict (LIT-4) | `g` (QRT-4) | `request.ctx` (SNC-3) | `request["di"]` (AIO-4) |
| **sync handler** | portal thread, contextvars copied (LIT-5) | `asyncio_0` thread (QRT-5) | **loop thread** (SNC-4) | **loop thread** (AIO-5) |
| **streaming API** | `Stream(gen(), background=...)` | `return gen()` | `request.respond()` + `send`/`eof` | `StreamResponse.prepare` + `write`/`write_eof` |
| **background** | `BackgroundTask` — before `after_response`, before the ASGI close (LIT-2, LIT-3) | `app.add_background_task` — runs inside the body window (QRT-2) | `app.add_task` — after `chunk.0`, before `gen.done` (SNC-2) | `asyncio.create_task` — after the middleware's close (AIO-2) |
| **test client** | `TestClient` runs hooks only inside `with` (LIT-1) | `test_client()` runs **no** serving hook, and is `async with` only (QRT-5) | **none built in** (SNC-5) | `TestClient(TestServer(app))` runs both (AIO-1) |
| **adapter verdict** | ASGI middleware | ASGI wrapper | ASGI wrapper | write-loop contract |

### 5.2 Litestar: own the scope in `ASGIMiddleware`

Litestar's `ASGIMiddleware` is the closest thing in this set to the PRD 09 reference
adapter: `handle(scope, receive, send, next_app)` receives the raw ASGI triple, so the
adapter wraps `send`, counts body messages and closes in `finally`.

```python skip
from litestar.middleware import ASGIMiddleware


class DIMiddleware(ASGIMiddleware):
    """Open the scope per request, close it after the last body message (LIT-2)."""

    def __init__(self, container):
        self.container = container

    async def handle(self, scope, receive, send, next_app):
        if scope["type"] != "http":
            return await next_app(scope, receive, send)
        resolution = self.container.scope(values={"scope": scope})
        scope.setdefault("state", {})["di"] = resolution
        try:
            await next_app(scope, receive, send)
        finally:
            await resolution.close()


app = Litestar(route_handlers=[read_order], middleware=[DIMiddleware(container)])
```

Two details are load-bearing and both were found the hard way:

- **Do not override `scopes`.** The base class defaults to HTTP, WebSocket and ASGI; a
  subclass that sets `scopes = ()` is silently built as a bypass and the middleware never
  runs.
- **The middleware instance is constructed once and shared by every request.** Litestar
  instantiates the subclass directly, so all per-request state must live in `handle`'s
  locals — the same "no state on the middleware" rule PRD 09 §5.2 states for every host.

`after_response` is the second correct owner (LIT-3), and is the one to use when the
application already has Litestar hooks and cannot add ASGI middleware. It is a pair: a
`before_request` hook that opens the scope, and one `after_response` callable that closes it.

```python skip
from litestar import Request


async def open_hook(request: Request) -> None:
    request.scope.setdefault("state", {})["di"] = container.scope(
        values={"scope": request.scope}
    )


async def close_scope(request: Request) -> None:
    await request.state.di.close()


app = Litestar(
    route_handlers=[read_order],
    before_request=open_hook,
    after_response=close_scope,
)
```

Two details there are corrections measured on litestar 2.25.0, not style. The app-level
hooks take **one callable**, never a list: `after_response=[close_scope]` builds without
complaint and then fails *every* request with `TypeError: 'list' object is not callable`,
which the exception middleware surfaces as `LitestarException: Exception caught after
response started` (the parameter is annotated `AfterResponseHookHandler | None`). And the
hook receives the **request**, not the response, so `response.request.state.di` raises
`AttributeError: 'Request' object has no attribute 'request'`; `request.state` is the
proxy for the same `scope["state"]` map both installs write. Re-run against the
three-chunk route, the order is `scope.opened`, `chunk.0`, `chunk.1`, `chunk.2` — every
chunk observing `closed=False` — then `scope.closed`.

`after_request` must not be used: measured order `after_request`, `chunk.0`, `chunk.1`,
`chunk.2`, `gen.done` (LIT-3) — the same failure PRD 10 measured for
`BaseHTTPMiddleware`.

### 5.3 Quart: wrap `app.asgi_app`, never `teardown_request`

Quart's teardown closes before the first chunk (QRT-2), so the adapter asserts the ASGI
seam instead. Quart exposes `app.asgi_app` as a plain ASGI callable, which makes the
adapter smaller than Litestar's:

```python skip
from quart import Quart, g


class DIWrapper:
    def __init__(self, app, container):
        self.app = app
        self.container = container

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        resolution = self.container.scope(values={"scope": scope})
        scope.setdefault("state", {})["di"] = resolution
        try:
            await self.app(scope, receive, send)
        finally:
            await resolution.close()


app = Quart(__name__)
app.asgi_app = DIWrapper(app.asgi_app, container)


@app.get("/orders/<order_id>")
async def read_order(order_id: str):
    scope = g.di  # set from the ASGI layer below; see the note
    return {"order": await (await scope.resolve(OrderRepository)).get(order_id)}
```

The note matters: `g` is an app-context variable, so the handler cannot read what the ASGI
wrapper wrote into `scope["state"]` unless a `before_request` hook copies it across. The
adapter therefore does both — the ASGI wrapper *owns* the scope, and one `before_request`
hook *publishes* it:

```python skip
from quart import g, request


@app.before_request
async def publish_scope():
    g.di = request.scope["state"]["di"]
```

That is two moving parts instead of one, and it is the price of Quart storing request
state in a ContextVar (`g`) rather than in the request object. The alternative — writing
a ContextVar from the ASGI layer — is exactly the ambient-scope design D-58 rejects,
because the value would be visible to code that is not serving that request.

### 5.4 Sanic: the response middleware is not a close point

Sanic has no ASGI wrapper seam in the application object, so the adapter has a choice: a
Sanic middleware (wrong, SNC-2) or a server-level ASGI wrapper. The page specifies the
second, and states the consequence plainly — **on Sanic the adapter must be installed at
the server, not only at the app**:

```python skip
from sanic import Sanic, response


app = Sanic("orders")


@app.before_server_start
async def start(app, _loop):
    await container.startup()


@app.after_server_stop
async def stop(app, _loop):
    await container.shutdown()


@app.middleware("request")
async def open_scope(request):
    resolution = container.scope(values={"request": request})
    request.ctx.di = resolution
    request.ctx.di_owner = resolution


@app.get("/orders/<order_id>")
async def read_order(request, order_id: str):
    repo = await (await request.ctx.di.resolve(OrderRepository)).get(order_id)
    return response.json({"order": repo})
```

Because the handler owns the body (`await request.respond(...)`, `await resp.send(...)`,
`await resp.eof()`), a plain request middleware can close the scope *after* the handler
returns and be correct — measured: `chunk.0`, `background.run`, `chunk.1`, `chunk.2`,
`gen.done`, `eof`, all `closed=False` (SNC-2). That is the aiohttp shape from §5.5, and it
is the only reason Sanic gets away without an ASGI wrapper. Two rules come with it:

- **Return `None` from a handler that called `respond()`.** Returning the response object
  after `eof()` makes Sanic log `The response object returned by the route handler will
  not be sent to client` — the request still succeeds, so the mistake is invisible in
  tests that only assert the status.
- **A sync handler blocks the loop** (SNC-4: measured on `MainThread`). The adapter must
  not recommend `def` handlers for anything that touches the container, because the
  `async` path and the `def` path do not share a thread model.

### 5.5 aiohttp: the handler owns the body, the middleware closes

aiohttp's `@web.middleware` receives `(request, handler)` and returns a
`StreamResponse`. When the handler writes the body itself, everything after
`await handler(request)` is after the last chunk (AIO-2); when the handler returns an
unwritten response, aiohttp writes it after the middleware has already closed (AIO-3).

```python skip
from aiohttp import web


@web.middleware
async def di_middleware(request, handler):
    resolution = container.scope(values={"request": request})
    request["di"] = resolution
    response = await handler(request)
    if not isinstance(response, web.StreamResponse) or response.prepared:
        await resolution.close()
    else:  # the handler returned an unwritten response: closing now would precede the body
        request["di_pending"] = resolution
    return response


async def read_order(request):
    scope = request["di"]
    repository = await scope.resolve(OrderRepository)
    return web.json_response({"order": await repository.get(request.match_info["order_id"])})


app = web.Application(middlewares=[di_middleware])
app.on_startup.append(lambda app: container.startup())
app.on_shutdown.append(lambda app: container.shutdown())
```

The `else` branch is the honest part: aiohttp offers no hook after an unwritten response
is flushed, so the adapter cannot close there. The page's rule is therefore a contract on
the handler style — **a route that returns an unprepared response must write it before
returning, or accept that the scope outlives it** — and §10 tests both branches.

### 5.6 One adapter, four installs

The four installs differ only in where the wrapper is attached:

```text
Litestar   middleware=[DIMiddleware(container)]        ASGI handle()
Quart      app.asgi_app = DIWrapper(app.asgi_app, c)   ASGI __call__
Sanic      server-level ASGI wrapper around app         ASGI __call__
aiohttp    web.middleware + handler-writes contract     no ASGI seam
```

Adapters are separate modules, imported by the application, never by `lauren_di`
(D-57). A per-framework module is what makes D-62 possible: with the framework absent the
module is skipped, not emulated.

## 6. Architecture Fit & Feasibility

### 6.1 What each framework forces on the adapter

| Framework | Forces | Acceptable? |
|---|---|---|
| Litestar | scope must be published into `scope["state"]` for `request.state` to see it | yes — the store is the ASGI scope |
| Quart | two-part publication: ASGI wrapper owns, `before_request` copies into `g` | yes, with a caveat in §6.3 |
| Sanic | no ASGI seam on the app object; adapter must wrap the server | yes, but documented as a deployment requirement |
| aiohttp | no post-body hook at all; the handler's write loop is the contract | yes, because aiohttp has no ASGI layer to wrap |

### 6.2 The universal rule, stated once

> Open the scope where the host tells you a request began. Close it where the body ends —
> after the last `http.response.body` for ASGI hosts, after `write_eof` for aiohttp.
> Never close it in a hook whose name refers to the response object.

Every one of the eight measured orders in §6.5 is an instance of that sentence, and the
negative controls (LIT-3, QRT-2, SNC-2, AIO-3) are what it costs to get it wrong.

### 6.3 Negative results worth publishing

- **Quart's `g` is a ContextVar, so the ASGI layer cannot write it directly.** The
  two-part publication in §5.3 is the only way to keep both "scope lives in the request"
  and "the ASGI wrapper owns the lifetime". An adapter that writes `g` from the wrapper
  would leak the scope into any task spawned during the request.
- **Sanic's response middleware runs before the first chunk** (SNC-2), so a Sanic-only
  application cannot use the library's recommended close point without a server-level
  wrapper. That is a deployment constraint, not a code change.
- **aiohttp cannot close an unwritten response** (AIO-3). Rather than pretend, the
  contract states the restriction.
- **Two of four hosts run sync handlers on the loop** (SNC-4, AIO-5). The adapter's
  documentation must say so, because a blocking handler there stalls every other request.

### 6.4 Cost

The adapter adds one scope per request, one dictionary write and two `await`s — the same
budget PRD 09 §6.4 measured at **1.1 µs** to open and close an empty scope and **0.27 ms**
to open, resolve one request-scoped class and close. None of the four frameworks was
measured for throughput here; the probes time order, not speed, and the only timing in
this page is the 20 ms chunk interval that makes the order legible.

### 6.5 Evidence index

Every claim is asserted by a test in `examples/apps/`, which `nox -s examples` runs on every
push. The authoring measurements came from throwaway scripts under `/tmp` that were never
committed — one per framework, plus a second Quart run through Hypercorn over a real socket
and a third Litestar configuration for the portal-thread row — so a citation of the form
"see probe_11a_litestar.py" named a file no reader could open. The table names the test
that keeps each id true.

| Framework | Claims and the tests that assert them | Versions measured |
|---|---|---|
| Litestar | LIT-1 (`tests/test_litestar.py::test_boot_hook_starts_the_container_before_the_first_request`, `::test_test_client_runs_the_hooks_only_inside_the_context`), LIT-2 (`::test_streaming_closes_after_the_body`, `::test_background_task_sees_an_open_scope`), LIT-3 (`::test_after_response_is_a_valid_second_owner`, `::test_after_response_hook_is_a_single_callable`), LIT-4 (`::test_scope_is_in_the_host_store`), LIT-5 (`::test_sync_handler_runs_off_the_event_loop`) | litestar 2.25.0, httpx 0.28.1 |
| Quart | QRT-1 (`::test_serving_hook_starts_the_container_before_the_first_request`), QRT-2 (`::test_teardown_request_is_not_an_owner`), QRT-3 (`::test_streaming_closes_after_the_body`), QRT-4 (`::test_scope_is_in_the_host_store`), QRT-5 (`::test_sync_view_runs_off_the_event_loop`, `::test_test_client_runs_no_serving_hook`) | quart 0.23.1 |
| Sanic | SNC-1 (`::test_container_starts_before_the_host_serves_and_stops_after`, `::test_streaming_handler_may_return_none`), SNC-2 (`::test_response_middleware_runs_before_the_body_so_it_cannot_close`, `::test_teardown_owner_is_not_the_response_middleware`), SNC-3 (`::test_scope_is_in_the_host_store`), SNC-4 (`::test_sync_handler_runs_on_the_main_thread`), SNC-5 (`::test_the_lifespan_must_be_entered_explicitly`) | sanic 25.12.1 |
| aiohttp | AIO-1 (`::test_test_client_runs_both_lifecycle_hooks`), AIO-2 (`::test_close_happens_after_the_final_body_chunk`, `::test_work_after_the_body_sees_an_open_unit`), AIO-3 (`::test_unwritten_response_closes_before_the_body`), AIO-4 (`::test_scope_is_in_the_host_store`, `::test_value_token_carries_the_aiohttp_request`), AIO-5 (`::test_sync_handler_runs_on_the_main_thread`) | aiohttp 3.14.5 |

Paths are relative to the app: `::test_streaming_closes_after_the_body` in the Litestar row
is `examples/apps/litestar/tests/test_litestar.py::test_streaming_closes_after_the_body`.
Framework pins live in `examples/apps/{litestar,quart,sanic,aiohttp}/requirements.txt`:
**Litestar 2.25.0** (with `httpx 0.28.1`), **Quart 0.23.1**, **Sanic 25.12.1** (with
`sanic-testing 24.6.0`), **aiohttp 3.14.5**.

Two rows deserve a caveat rather than a pin. **Hypercorn 0.18.0** appears in the authoring
transcripts (QRT-1/QRT-2 over a real socket, Sanic's server listeners) and the examples do
not install it, so the in-process cases are what `nox -s examples` re-runs; the socket-level
observation stands on the authoring transcript alone. And **Werkzeug**, which QRT-5's
`async with`-only client row depends on, is not pinned beside Quart — the version above is
the measured one, and that gap is tracked as its own goal.

## 7. Error Catalog Impact

No new leaf. The mapping is PRD 09 §5.8 applied to four hosts:

| Host | Error channel | `DIError` behaviour |
|---|---|---|
| Litestar | `HTTPException` subclasses; unhandled → 500 | raised from the handler propagates; the middleware's `finally` still closes once |
| Quart | `abort()` / `HTTPException`; unhandled → 500 | propagates out of the ASGI wrapper; `finally` closes once |
| Sanic | `SanicException` subclasses; unhandled → 500 | propagates; the request middleware's close runs once |
| aiohttp | `web.HTTPException`; unhandled → 500 | propagates; the middleware closes once, before the 500 body is written |

The last row is the one to watch: for aiohttp the error body is written by the server
after the middleware has closed the scope, so an error handler that resolves from
`request["di"]` sees a closed scope. The adapter's error mapping therefore resolves
nothing after the close — it formats from data already captured, which is the same
containment rule as PRD 03's teardown.

## 8. Phased Implementation Plan

| Phase | Deliverable | Gate | Version |
|---|---|---|---|
| P11-1 | `examples/asgi_litestar/` — §5.2 adapter, one streaming route | runs under uvicorn; `body_msgs=4` before the close | 0.1.0 |
| P11-2 | `tests/integration/hosts/test_asgi_quart.py` — C-1…C-12 plus QRT-2's negative control | PASS with Quart installed, SKIP without (D-62) | 0.2.0 |
| P11-3 | `tests/integration/hosts/test_asgi_sanic.py` — server-level wrapper, including the `respond()`/`return None` rule | PASS with Sanic installed | 0.2.0 |
| P11-4 | `tests/integration/hosts/test_asgi_aiohttp.py` — both branches of §5.5 | PASS with aiohttp installed | 0.2.0 |
| P11-5 | `docs/guides/hosting.md` gains the four-row matrix and the sync-handler warning | docs build; corpus tests | 0.3.0 |

## 9. Documentation & Convention Updates

| Document | Change | Rule |
|---|---|---|
| `docs/guides/hosting.md` | an "async frameworks" section: the matrix from §5.1 and the universal rule from §6.2 | one page owns the contract (PRD 09 §9) |
| `examples/README.md` | four new rows, one per framework | examples are executed, so the rows cannot rot |
| `docs/guides/testing.md` | "Quart's test client runs no serving hook; Sanic ships no client" | the failure mode, not just the fix |
| `llms.txt`, `llms-full.txt` | regenerate with the new page | never hand-edited |
| this page | `fragment`/`skip` fences only (D-63) — every sample imports a framework | a `run` fence must execute with no framework installed |

## 10. Test Plan

### 10.1 Enumerated test cases

`T11-nn` are parameterised over the four frameworks unless a row names one. They live in
`tests/integration/hosts/test_asgi_*.py` and are skip-guarded per framework (D-62).

| # | Invariant | Fixture | Action | Expected |
|---|---|---|---|---|
| T11-01 | the boot hook starts the container | an app with a `@post_construct` counter | start the host | counter 1 before the first request (LIT-1, QRT-1, SNC-1, AIO-1) |
| T11-02 | the stop hook shuts it down | the same app | stop the host | one `@pre_destruct`; `shutdown()` returned `()` |
| T11-03 | one scope per request | two requests | compare scope ids | different |
| T11-04 | the scope is in the host store | a handler reading `request.state.di` / `g.di` / `request.ctx.di` / `request["di"]` | one request | a `ResolutionScope`, not `None` (LIT-4, QRT-4, SNC-3, AIO-4) |
| T11-05 | streaming closes after the body | a three-chunk body, ASGI owner | read the whole body | `body_msgs=4` before the close (LIT-2, QRT-3) |
| T11-06 | Quart's teardown is not an owner | the same route, owned by `teardown_request` (negative control) | read the body | every chunk observes `closed=True` (QRT-2) — the test asserts the trap |
| T11-07 | Sanic's response middleware is not an owner | the same route, owned by `@app.middleware("response")` | read the body | the middleware fires before `chunk.0` (SNC-2) |
| T11-08 | aiohttp closes after a written body | handler-driven `prepare`/`write`/`write_eof` | read the body | middleware close after `eof`, all chunks `closed=False` (AIO-2) |
| T11-09 | aiohttp cannot close an unwritten response | a handler returning `web.Response` | one request | the close precedes the body (AIO-3); the branch is documented, not hidden |
| T11-10 | background tasks see an open scope | one route with each host's background API | one request | `background.run closed=False` (LIT-2, SNC-2, AIO-2) |
| T11-11 | Litestar's `after_response` is a valid second owner | hooks-only app | read the body | close after `background.run` (LIT-3) |
| T11-12 | the sync-handler model is the measured one | one `def` handler per host | one request | Litestar portal thread, Quart `asyncio_0`, Sanic and aiohttp `MainThread` (LIT-5, QRT-5, SNC-4, AIO-5) |
| T11-13 | test-client lifecycle matches the host | each host's client | request inside and outside the context | Litestar runs hooks only inside; Quart runs none; aiohttp runs both; Sanic has no built-in client (LIT-1, QRT-5, AIO-1, SNC-5) |
| T11-14 | errors close exactly once | a handler raising `DIError` | one request | 500; exactly one close |
| T11-15 | Sanic handlers that stream return `None` | `request.respond()` + `return None` | one request | no `will not be sent to client` error in the log |
| T11-16 | the adapter modules import nothing when the framework is absent | a clean venv | import each adapter | `ImportError`/`ModuleNotFoundError` → SKIP, never a fake (D-62) |

One client-level detail decides how T11-13 is written for Quart, and it belongs beside
QRT-5's "no serving hook" row: `Quart.test_client()` supports `async with` but **not** sync
`with`. The sync form raises `TypeError: 'QuartClient' object does not support the context
manager protocol` before a single request is made, so a Quart case must be an `async def`
test that `await`s inside `async with app.test_client()`. It also cannot lift
`startup()`/`shutdown()` from the client at all — QRT-5 is why `§5.3` binds those to
`before_serving`/`after_serving` instead.

### 10.2 Acceptance traceability

| Goal | Tests |
|---|---|
| G1 (one adapter shape, four installs) | T11-01 … T11-04 |
| G2 (close point per framework, with negatives) | T11-05 … T11-09 |
| G3 (host store, no ambient scope) | T11-04, T11-12 |
| G4 (`startup()`/`shutdown()` bound to the host) | T11-01, T11-02, T11-13 |
| G5 (sync-handler model recorded) | T11-12 |
| G6 (streaming and background measured) | T11-05, T11-10, T11-11 |
| D-62 (absent host is SKIP) | T11-16 |
