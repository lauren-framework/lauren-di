# PRD 12 — WSGI Web Frameworks: Flask, Falcon, Pyramid and Bottle

| | |
|---|---|
| Status | Draft for review |
| Depends on | PRD 09 (integration contract), PRD 03 (scopes), PRD 05 (errors), PRD 07 (overrides) |
| Reference | Flask 3.1.3, Falcon 4.4.0, Pyramid 2.1, Bottle 0.13.4, Werkzeug 3.1.9, httpx 0.28.1, CPython 3.13.13 |
| Decisions | D-57 (adapter outside the library), D-58 (no ambient scope), D-59 (dedicated-loop bridge is the default for a sync host), D-60 (`startup()` before serving), D-62 (absent host is SKIP), D-63 (framework imports only in fragment/skip fences) |

## 1. Problem Statement

PRD 10 and PRD 11 found four different close points in six async frameworks. WSGI is the
opposite problem: **one close point works for all four**, and none of the frameworks'
own teardown hooks is it.

Measured in this page, with the same harness and the same three-chunk streamed body:

1. **Every framework hook closes before the first chunk.** Flask `teardown_request`,
   Falcon `process_response`, Pyramid `add_finished_callback` and Bottle `after_request`
   all run before `chunk.0`, and every chunk then observes `closed=True` (FLK-2, FLC-2,
   PYR-2, BTL-2). This is the `BaseHTTPMiddleware` shape from PRD 10 §5.4, arriving
   through four unrelated APIs.
2. **Flask's `teardown_request` fires twice for a streamed response** — once with
   `closed=False`, then again after the body with `closed=True` (FLK-2). A close written
   in that hook closes twice, and the second close is on a scope whose cache is already
   gone.
3. **One WSGI wrapper closes correctly for all four.** Counting body chunks in the
   iterable the server consumes and closing when that iterable ends gives
   `chunks=3 closed=False` then `after_close closed=True` for Flask, Falcon, Pyramid and
   Bottle alike (FLK-3, FLC-3, PYR-3, BTL-3). No framework-specific seam is needed,
   because WSGI already specifies the event: the server calls `close()` on the iterable
   *after* the last byte.
4. **Every handler runs on a `process_request_thread`, not the caller's thread**
   (FLK-1, FLC-1, PYR-1, BTL-1). Combined with §5.2, that is why the bridge is not
   optional: the container's coroutines have to be driven from a thread that has no loop.
5. **None of the four has a startup hook** (FLK-5, FLC-5, PYR-5, BTL-5). Flask 3.1
   removed `before_first_request`; Falcon has no `start`/`add_startup`; Pyramid's
   `Configurator` has no lifecycle; Bottle's `hook()` has no application-start event. So
   D-60 applies literally: the deployer calls `startup()` before the first request is
   served.

The result is the simplest adapter in the compatibility set: one bridge, one wrapper, and
a per-framework paragraph about where to keep the pointer.

## 2. Goals

| # | Goal | Why |
|---|---|---|
| G1 | One WSGI wrapper that owns the scope for all four frameworks | WSGI defines the close event; a per-framework owner would be four chances to get it wrong |
| G2 | The dedicated-loop bridge as the default (D-59), with the loop-affinity failure it prevents | a WSGI thread has no loop and a singleton may own one |
| G3 | `startup()`/`shutdown()` bound to the deployment, not to a framework hook (D-60) | none of the four has a hook to bind to |
| G4 | The scope carried in the host's per-request store, with the copy each framework needs | `g`, `req.context` and `environ` are three different answers |
| G5 | The framework teardown hooks documented as traps with measured orders | each is the hook a maintainer will reach for first |
| G6 | Test-client behaviour recorded, including that it does not reproduce the threaded server | local tests would otherwise pass while production tears down early |

## 3. Non-Goals

- **No WSGI server.** Werkzeug's `make_server` is the probe's harness, not a dependency.
- **No async-WSGI hybrids** (gevent/eventlet monkey-patching). The bridge assumes real
  threads; a greenlet host would need its own measurement.
- **No per-framework middleware base class in the library.** The wrapper is ~20 lines and
  belongs in the application or the examples tree (D-57).
- **No new error leaves.** §7 maps to PRD 09 §5.8.
- **No WebSocket, streaming-to-browser (SSE) or long-poll conformance** beyond the
  generator-bodied response measured here.
- **No claim about Flask's `async` views.** They run on a loop the library does not own;
  the bridge already covers the sync path that matters.

## 4. Prior Art

| Framework | Hook that looks like the close point | Measured position | Verdict |
|---|---|---|---|
| Flask 3.1.3 | `@app.teardown_request` | before `chunk.0`, and a **second** time after the body | rejected — FLK-2 |
| Falcon 4.4.0 | middleware `process_response` | before `chunk.0` | rejected — FLC-2 |
| Pyramid 2.1 | `request.add_finished_callback` | before `chunk.0` | rejected — PYR-2 |
| Bottle 0.13.4 | `@app.hook("after_request")` | before `chunk.0` | rejected — BTL-2 |
| all four | the WSGI iterable's `close()` | after the last chunk | **accepted** — §5.3 |

The reason all four fail is structural, not a bug in any of them: a hook that fires when
the *response object* is built cannot know when its *body* finishes. WSGI's own answer is
the iterable, and it is the only mechanism in the specification that is defined to run
after the last byte.

### 4.1 Where each framework keeps the request

| Framework | Store | Is it the `environ`? | Copy needed |
|---|---|---|---|
| Flask | `flask.g` (app-context object) | no — `in_environ=False, in_g=True` (FLK-4) | yes, one line in `before_request` |
| Falcon | `req.context` (a `Context` namespace) | no (FLC-4) | no — assign directly |
| Pyramid | `request.environ["di"]` | yes (PYR-4) | no |
| Bottle | `bottle.request.environ["di"]` | yes (BTL-4) | no |

## 5. Proposed API

### 5.1 The comparison matrix

| Framework | Thread per request | Framework hook closes | WSGI wrapper closes | Store | Boot hook |
|---|---|---|---|---|---|
| **Flask 3.1.3** | `Thread-N (process_request_thread)` (FLK-1) | before `chunk.0`, twice (FLK-2) | `chunks=3` then closed (FLK-3) | `g` (FLK-4) | none (FLK-5) |
| **Falcon 4.4.0** | `Thread-N (process_request_thread)` (FLC-1) | before `chunk.0` (FLC-2) | `chunks=3` then closed (FLC-3) | `req.context` (FLC-4) | none (FLC-5) |
| **Pyramid 2.1** | `Thread-N (process_request_thread)` (PYR-1) | before `chunk.0` (PYR-2) | `chunks=3` then closed (PYR-3) | `request.environ` (PYR-4) | none (PYR-5) |
| **Bottle 0.13.4** | `Thread-N (process_request_thread)` (BTL-1) | before `chunk.0` (BTL-2) | `chunks=3` then closed (BTL-3) | `bottle.request.environ` (BTL-4) | none (BTL-5) |

The identical wrapper column is the page's whole argument: WSGI is the one host shape
where a single adapter is sufficient.

### 5.2 The bridge is the default (D-59)

A WSGI handler is a plain function on a worker thread with no running loop, while every
container entry point that does work is a coroutine. PRD 09 §5.5 gives the two patterns
and this page uses the first:

```python skip
import asyncio
import threading


class LoopBridge:
    """Run every container call on one long-lived loop, on one thread."""

    def __init__(self):
        self._loop = asyncio.new_event_loop()
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._run, name="lauren-di-loop", daemon=True)
        self._thread.start()
        self._ready.wait()

    def _run(self):
        asyncio.set_event_loop(self._loop)
        self._ready.set()
        self._loop.run_forever()

    def call(self, coro):
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result()

    def stop(self):
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=5)
        self._loop.close()
```

Two measured facts make it the default rather than a preference. It is cheaper —
**0.18–0.22 ms per call** against **0.24–0.33 ms** for `asyncio.run()` (PRD 09 §5.5). And
it is the only option that preserves loop affinity: a singleton built by `asyncio.run()`
on one request's loop is unusable on the next request's loop, because that loop was
closed after the call.

```python skip
bridge = LoopBridge()
bridge.call(container.startup())          # D-60: before the first request is served

scope = container.scope()                 # opening a scope is synchronous
service = bridge.call(scope.resolve(Service))   # every await goes through the bridge
bridge.call(scope.close())
```

The wrapper below is the only place a scope is opened, so there is exactly one bridge
crossing per phase per request — not one per resolved dependency.

### 5.3 One owner: the response iterable

```python skip
class DIMiddleware:
    """WSGI wrapper: open one scope per request, close it when the body ends."""

    def __init__(self, app, container, bridge):
        self.app = app
        self.container = container
        self.bridge = bridge

    def __call__(self, environ, start_response):
        scope = self.container.scope(values={"environ": environ})
        environ["di"] = scope

        def body():
            chunks = 0
            try:
                for chunk in self.app(environ, start_response):
                    chunks += 1
                    yield chunk
            finally:
                self.bridge.call(scope.close())

        return body()


app = DIMiddleware(app, container, bridge)
```

Three properties are worth stating because they are what the measurements show:

- **It is the same object for all four frameworks.** No import of Flask, Falcon,
  Pyramid or Bottle appears in the wrapper — it is a WSGI app wrapping a WSGI app.
- **`close()` runs on exhaustion *and* on abandonment.** A client that disconnects makes
  the server call `close()` on the iterable, which raises `GeneratorExit` inside `body()`
  and runs the `finally`. That is the same guarantee PRD 09 §5.2 asks for, obtained from
  the WSGI server rather than from the framework.
- **The scope is opened before the framework sees the request**, so a `before_request`
  hook can publish it into `g`/`req.context` without owning its lifetime.

### 5.4 Flask

```python skip
from flask import Flask, Response, g, request, stream_with_context

app = Flask(__name__)


@app.before_request
def publish_scope():
    g.di = request.environ["di"]        # the wrapper owns it; Flask only points at it


@app.get("/orders/<order_id>")
def read_order(order_id):
    scope = g.di
    repository = bridge.call(scope.resolve(OrderRepository))
    return {"order": repository.get(order_id)}


@app.get("/export")
def export():
    scope = g.di

    def rows():
        for row in repository_rows(scope):
            yield f"{row}\n"

    return Response(stream_with_context(rows()), mimetype="text/plain")


app.wsgi_app = DIMiddleware(app.wsgi_app, container, bridge)
```

- **`g` is not `environ`.** Measured `in_environ=False, in_g=True` (FLK-4), so the
  one-line copy above is required; the wrapper cannot write `g` itself because `g` does
  not exist until Flask's request context does.
- **`stream_with_context` is required** for a generator body that touches `g`, and it is
  also why `teardown_request` fires a second time (FLK-2). Do not close there.
- **Wrap `app.wsgi_app`, not `app`.** Assigning the wrapper to `wsgi_app` keeps Flask's
  own middleware chain and its `test_client` working.

### 5.5 Falcon

```python skip
import falcon


class ScopeMiddleware:
    def process_request(self, req, resp):
        req.context.di = req.env["di"]          # a Context namespace, not the environ dict

    def process_response(self, req, resp, resource, req_succeeded):
        pass                                    # deliberately empty: not a close point


class Orders:
    def on_get(self, req, resp, order_id):
        scope = req.context.di
        resp.media = {"order": bridge.call(scope.resolve(OrderRepository)).get(order_id)}


class Export:
    def on_get(self, req, resp):
        scope = req.context.di

        def rows():
            yield from (f"{row}\n" for row in repository_rows(scope))

        resp.content_type = "text/plain"
        resp.stream = rows()                    # Falcon iterates it after the handler returns


app = falcon.App(middleware=[ScopeMiddleware()])
app.add_route("/orders/{order_id}", Orders())
app.add_route("/export", Export())
app = DIMiddleware(app, container, bridge)
```

`process_response` runs before `chunk.0` (FLC-2) — measured — and is the hook a Falcon
developer will reach for. Closing there is worse than it looks: the negative control
closes, chunks 0–2 then read `closed=True`, and a non-streamed route logs
`req.pre_destruct` before the server writes a byte, so the response is built after the
scope is gone. It stays in the adapter only to publish, never to close.

### 5.6 Pyramid

```python skip
from pyramid.events import NewRequest
from pyramid.response import Response


def publish_scope(event):
    event.request.environ.setdefault("di_from_wrapper", True)


def stream_view(request):
    scope = request.environ["di"]

    def gen():
        yield from (f"{row}\n".encode() for row in repository_rows(scope))

    return Response(app_iter=gen(), content_type="text/plain")


def includeme(config):
    config.add_subscriber(publish_scope, NewRequest)
    config.add_route("export", "/export")
    config.add_view(stream_view, route_name="export")
```

Pyramid stores the scope in `environ` already (PYR-4), so the subscriber adds nothing to
the lifetime story. `request.add_finished_callback` is what a Pyramid developer will use;
it fires before `chunk.0` (PYR-2) and must not close. `Configurator` has no lifecycle hook
(PYR-5), so `bridge.call(container.startup())` belongs in the entry point that builds the
WSGI app.

### 5.7 Bottle

```python skip
import bottle

app = bottle.Bottle()


@app.hook("before_request")
def publish_scope():
    bottle.request.environ.setdefault("di_ready", True)


@app.get("/export")
def export():
    scope = bottle.request.environ["di"]

    def rows():
        yield from (f"{row}\n" for row in repository_rows(scope))

    return rows()                              # Bottle iterates a generator body


app = DIMiddleware(app, container, bridge)
```

Bottle's `after_request` hook runs before the body (BTL-2) and is the trap. Its
`bottle.request` is a thread-local proxy, so `bottle.request.environ["di"]` is the same
dict the wrapper wrote (BTL-4) — the only framework here where the store is exactly the
environ.

### 5.8 Test clients

| Framework | Client | Thread | Serving hooks |
|---|---|---|---|
| Flask | `app.test_client()` | `MainThread` (FLK-1) | none exists (FLK-5) |
| Falcon | `falcon.testing.TestClient(app).simulate_get()` | `MainThread` (FLC-1) | none (FLC-5) |
| Pyramid | any WSGI client, e.g. `werkzeug.test.Client(app)` | `MainThread` (PYR-1) | none (PYR-5) |
| Bottle | any WSGI client (`webtest` is not installed) | `MainThread` (BTL-1) | none (BTL-5) |

The consequence is the same in all four: **an in-process test does not reproduce the
threaded server**, so it cannot catch a `g`/thread-local mistake or a close ordered
against the wrong thread. §10 therefore runs the streaming cases through a real threaded
server, not through the client.

### 5.9 Overrides

Overrides are PRD 07's `OverridePlan`, activated around the build and independent of the
host. What is WSGI-specific is only *where* the plan is entered: once per test or once
per process, never per request, because the container is process-wide and the bridge
serialises access to it anyway (PRD 09 §5.7).

## 6. Architecture Fit & Feasibility

### 6.1 Why one wrapper is enough here

WSGI's `close()` is part of the specification, not a framework courtesy: the server must
call it on the iterable after the last byte, including when the client disconnects. In
ASGI the equivalent event exists too — the last `http.response.body` — but each framework
wraps it differently, which is why PRD 11 needed four seams. Here the seam is the
protocol.

### 6.2 What the library must not learn

Nothing in the adapter imports a framework. The wrapper takes `(app, container, bridge)`,
which means PRD 09's conformance suite can drive all four frameworks through one module
and the library never learns that WSGI exists.

### 6.3 Negative results worth publishing

- **Flask's double teardown** (FLK-2) is the sharpest one: a close written there would run
  twice, and `ResolutionScope.close()` is idempotent only because PRD 03 says so.
- **Falcon's `process_response` closes too early to be useful** (FLC-2). It *can* close —
  the negative control does, and chunks 0–2 then observe `closed=True`, while a
  non-streamed route logs `req.pre_destruct` before the server writes a single byte. A
  streamed body produced after that point resolves against a disposed scope; a page that
  merely looks fine is luck. The adapter keeps the hook for the publish only, and says so
  in a comment, because deleting it invites a maintainer to "fix" the omission.
- **The bridge serialises container work** (PRD 09 §5.5). On a WSGI host that is more
  visible than on an ASGI host: a slow constructor blocks every worker thread's
  container call, though not their non-container work.
- **Test clients run on `MainThread`** (§5.8), so a green suite is not evidence about
  production threading.

### 6.4 Cost

Per request the adapter adds one `scope()` call, one `environ` write and one bridge
crossing for `close()` plus one per `resolve()` — each crossing **0.18–0.22 ms** (PRD 09
§5.5). Nothing else: no task, no queue, no framework import.

### 6.5 Evidence index

Every claim above is asserted by a test in `examples/apps/`, which `nox -s examples` runs on
every push. The authoring transcript was a throwaway script — one process, four frameworks,
one after another, through a real threaded server — that was never committed, so citing it
by filename named a file no reader could open. The rows below name the test that keeps each
`FLK`/`FLC`/`PYR`/`BTL` id true; where a row has no test it says so.

| Framework | Claims and the tests that assert them | Versions measured |
|---|---|---|
| Flask | FLK-1 (`tests/test_flask.py::test_a_real_server_serves_the_handler_off_the_calling_thread`), FLK-2 (`::test_flask_teardown_request_fires_before_the_body_is_streamed`), FLK-3 (`::test_three_chunks_are_served_with_the_scope_open_then_closed`), FLK-4 (`::test_the_scope_is_reachable_from_the_host_store`), FLK-5 (`::test_startup_runs_once_before_the_first_request`) | flask 3.1.3, werkzeug 3.1.9 |
| Falcon | FLC-1 (`::test_a_real_server_serves_the_responder_off_the_calling_thread`), FLC-2 (`::test_falcon_process_response_fires_before_the_body_is_streamed`), FLC-3 (`::test_three_chunks_are_served_with_the_scope_open_then_closed`), FLC-4 (`::test_the_scope_is_reachable_from_the_host_store`), FLC-5 (`::test_startup_runs_once_before_the_first_request`) | falcon 4.4.0 |
| Pyramid | PYR-1 (`::test_a_real_server_serves_the_view_off_the_calling_thread`), PYR-2 (`::test_new_response_is_not_the_owner`), PYR-3 (`::test_teardown_follows_the_last_chunk`), PYR-4 (`::test_the_scope_is_reachable_through_the_environ`), PYR-5 (`::test_startup_precedes_the_first_request`) | pyramid 2.1, webtest 3.0.7 |
| Bottle | BTL-1 (`::test_a_real_server_serves_the_route_off_the_calling_thread`), BTL-2 (`::test_after_request_is_not_the_owner`), BTL-3 (`::test_teardown_follows_the_last_chunk`), BTL-4 (`::test_the_scope_is_reachable_through_the_environ`), BTL-5 (`::test_startup_precedes_the_first_request`) | bottle 0.13.4 |

Every path in the table is relative to its app — `::test_a_real_server_serves_the_handler_off_the_calling_thread`
means `examples/apps/flask/tests/test_flask.py::test_a_real_server_serves_the_handler_off_the_calling_thread`.
The framework pins live in `examples/apps/{flask,falcon,pyramid,bottle}/requirements.txt`:
**Flask 3.1.3**, **Falcon 4.4.0**, **Pyramid 2.1** (with `webtest 3.0.7`), **Bottle 0.13.4**.

One pin is missing and the omission is load-bearing: `werkzeug` is the object whose
`close()` §5.3 wraps and T12-05/T12-10 measure, but only Flask is pinned, so the Werkzeug
that runs is whatever the resolver chose (3.1.9 in the authoring transcript). That gap is
tracked as its own goal; until it is closed, the Werkzeug version beside the Flask row is
the *measured* one, not a `nox -s examples` guarantee.

## 7. Error Catalog Impact

No new leaf. The mapping is PRD 09 §5.8 applied to a sync host:

| Host | Error channel | `DIError` behaviour |
|---|---|---|
| Flask | `abort()` / `HTTPException`; unhandled → 500 | raised from the view; WSGI's `close()` still runs once, so the scope closes once |
| Falcon | `falcon.HTTPError`; unhandled → 500 | same |
| Pyramid | `pyramid.httpexceptions`; unhandled → 500 | same |
| Bottle | `bottle.HTTPError`; unhandled → 500 | same |

One difference from PRD 11 matters: on WSGI the 500 body is a *new* iterable returned by
the framework, and the wrapper's `body()` closes after that iterable ends. So an error
handler that resolves from `environ["di"]` still sees an open scope — the opposite of the
aiohttp case in PRD 11 §7, and worth stating because it is the same code path in an
adapter that supports both.

## 8. Phased Implementation Plan

| Phase | Deliverable | Gate | Version |
|---|---|---|---|
| P12-1 | `examples/wsgi_bridge/` — §5.2 bridge + §5.3 wrapper + one Flask route | runs under `werkzeug.serving`; the streaming order asserts `chunks=3` before the close | 0.1.0 |
| P12-2 | `tests/integration/hosts/test_wsgi_flask.py` — C-1…C-12, threaded server | PASS with Flask installed, SKIP without (D-62) | 0.2.0 |
| P12-3 | the Falcon variant, including the empty `process_response` | PASS with Falcon installed | 0.2.0 |
| P12-4 | the Pyramid and Bottle variants, one module each | PASS with each installed | 0.2.0 |
| P12-5 | `docs/guides/hosting.md` gains the WSGI section: the bridge, the wrapper, the four trap hooks | docs build; corpus tests | 0.3.0 |

## 9. Documentation & Convention Updates

| Document | Change | Rule |
|---|---|---|
| `docs/guides/hosting.md` | a WSGI section: bridge first, then the wrapper, then the four traps | one page owns the contract (PRD 09 §9) |
| `examples/README.md` | four WSGI rows | examples are executed, so the rows cannot rot |
| `docs/guides/testing.md` | "an in-process WSGI client runs on the main thread and no framework here has a boot hook" | the failure mode, not just the fix |
| `llms.txt`, `llms-full.txt` | regenerate with the new page | never hand-edited |
| this page | `fragment`/`skip` fences only (D-63) | a `run` fence must execute with no framework installed |

## 10. Test Plan

### 10.1 Enumerated test cases

`T12-nn` are parameterised over the four frameworks unless a row names one, and run
through a real threaded WSGI server. They live in `tests/integration/hosts/test_wsgi_*.py`
and are skip-guarded per framework (D-62).

| # | Invariant | Fixture | Action | Expected |
|---|---|---|---|---|
| T12-01 | the handler runs off the caller's thread | a threaded server | one request | the handler's thread is not `MainThread` (FLK-1, FLC-1, PYR-1, BTL-1) |
| T12-02 | `startup()` precedes the first request | a `@post_construct` counter | request immediately after boot | counter 1 before the response (FLK-5, FLC-5, PYR-5, BTL-5) |
| T12-03 | one scope per request | two requests | compare scope ids | different |
| T12-04 | the scope is reachable from the host store | a view reading `g.di` / `req.context.di` / `environ["di"]` | one request | a `ResolutionScope` (FLK-4, FLC-4, PYR-4, BTL-4) |
| T12-05 | the wrapper closes after the body | a three-chunk generator body | read the whole body | `chunks=3 closed=False` then closed (FLK-3, FLC-3, PYR-3, BTL-3) |
| T12-06 | Flask's teardown is not an owner | the same route, close in `teardown_request` (negative control) | read the body | chunks observe `closed=True` and teardown fires twice (FLK-2) |
| T12-07 | Falcon's `process_response` is not an owner | the same route (negative control) | read the body | it fires before `chunk.0` (FLC-2) |
| T12-08 | Pyramid's finished callback is not an owner | the same route (negative control) | read the body | it fires before `chunk.0` (PYR-2) |
| T12-09 | Bottle's `after_request` is not an owner | the same route (negative control) | read the body | it fires before `chunk.0` (BTL-2) |
| T12-10 | a client disconnect still closes | a body generator the client abandons after one chunk | close the connection | exactly one `close()`; `errors == ()` |
| T12-11 | the 500 path closes once | a view raising `DIError` | one request | 500; one close; the error handler still sees an open scope (§7) |
| T12-12 | a resource resolved in the view is torn down after the body | a `@pre_destruct` marker | read the body | the marker appears after the last chunk |
| T12-13 | the bridge preserves loop affinity | an async singleton built in one request | a second request | it is reused; no `RuntimeError: Event loop is closed` (D-59) |
| T12-14 | the in-process client is not the production thread | `app.test_client()` and a WSGI client | one request each | `MainThread` (§5.8), so T12-01 is not covered by them |

### 10.2 Acceptance traceability

| Goal | Tests |
|---|---|
| G1 (one wrapper, four frameworks) | T12-03, T12-05 |
| G2 (bridge is the default) | T12-13 |
| G3 (`startup()` before serving) | T12-02 |
| G4 (host store, copy where needed) | T12-04 |
| G5 (framework hooks are traps) | T12-06 … T12-09 |
| G6 (test-client behaviour recorded) | T12-14 |
| D-62 (absent host is SKIP) | every module, per framework |
