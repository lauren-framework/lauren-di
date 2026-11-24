# `sanic` — `lauren_di` with Sanic

A single-file example of hosting `lauren_di` inside Sanic. It is the async page of
PRD 11, reduced to one app, one adapter module and one test module — and on Sanic the
adapter has to sit at the *server* seam, not at the app (§5.4).

## The adapter, in three lines

| Step | Where | What it does |
| --- | --- | --- |
| start | `before_server_start` / `after_server_stop` | builds the container, registers providers, awaits `startup()` before the first request and `shutdown()` after the last one |
| open | `sanic_di.DIASGIApp.__call__` | opens one `HostUnit` per HTTP request, hands the ASGI scope to the container as the one value token, publishes the scope on `request.ctx.di` from the request middleware |
| close | the same `__call__`, after `await self.app(...)` | closes the unit exactly once, after the final `http.response.body` message, and reports teardown failures instead of raising them |

Handlers never see the container:

```python
@app.get("/hello")
async def hello(request: Request) -> HTTPResponse:
    scope = sanic_di.current_scope(request)
    greeting = await scope.resolve(Greeting)
    unit = await scope.resolve(RequestUnit)
    return response.json({"message": greeting.greet("world"), "request_id": unit.request_id})
```

## The close point, measured

The response middleware is the tempting close point and it is the wrong one. Measured
on sanic 25.12.1, the order inside one request is:

```
request middleware -> handler -> response middleware -> body chunks -> eof
```

So a unit closed in the response middleware is disposed *while the body is still being
written* (SNC-2). The `http.lifecycle.response` signal is no better: a signal handler
that sleeps 0.4 s delays the client's first body byte to 0.476 s, i.e. the signal fires
*before* the body is written.

What is left is the ASGI callable the server invokes. That is the seam this adapter
wraps, and the test measures it rather than trusting it: while the final body message
crosses `send`, `DIASGIApp.events` still holds only `("open", "/stream")`; the single
`("close", "/stream")` appears once the callable returns.

Consequence: `python -m sanic` / `app.run()` **cannot** host this adapter — Sanic's own
HTTP protocol calls `app.handle_request` directly and never goes through the ASGI
callable. Serve the wrapped callable instead:

```bash
hypercorn sanic_app:asgi --bind 127.0.0.1:8000   # any ASGI server will do
```

`requirements.txt` deliberately pins only `sanic` and `sanic-testing`: the example itself
needs no ASGI server, because the self-check below *is* the ASGI seam. What those two
drag in — `websockets`, `httptools`, `uvloop`, `sanic-routing` — is pinned one directory
up in [`../constraints.txt`](../constraints.txt), because that is what the ASGI seam is
exercised through.

## Files

| File | What is in it |
| --- | --- |
| `sanic_app.py` | the container factory, the providers (`Recorder`, `Greeting`, `RequestUnit`, `DoomedTeardown`), the routes and `asgi` |
| `sanic_di.py` | the adapter: `DIASGIApp`, `install`, `current_scope`, the value token, plus the ASGI drivers (`asgi_conversation`, `asgi_lifespan`) the tests and the demo measure with |
| `tests/test_sanic.py` | the §10.1 cases, every one of them driven through the ASGI seam |

## What this host forces

* **A server seam.** Sanic offers no app-level hook after the body, so the ASGI
  callable is the only honest close point (§5.4).
* **Two moving parts to publish the scope.** The wrapper opens the unit but never sees
  the `Request`; a `ContextVar` carries the unit from the wrapper to the request
  middleware, which is what writes `request.ctx.di`. `request.ctx` stays the
  authoritative store — nothing else reads the `ContextVar`.
* **Handler-owned bodies are not enough here.** Even `request.respond()` +
  `resp.eof()` returns from the handler *before* the chunks are written, so a
  post-handler hook would still be too early.

## Rules the example follows

* IC-1/IC-2: one container, started before the host serves, shut down after it.
* IC-3/IC-7: one unit per request, closed exactly once.
* IC-4: the scope lives in the host's store (`request.ctx.di`), never in a module global.
* IC-8: a raising `@pre_destruct` is captured in `scope.errors`, reported to the host's
  channel, and never raised into the response — `/teardown-error` still answers `200`.

## Run it

```bash
python examples/apps/sanic/sanic_app.py            # in-process ASGI self-check
python -m pytest examples/apps/sanic -q            # the tests
```

The self-check prints, per route, the seam's event log *at the moment the last body
message was sent* and after the call returned; the difference is the measurement.

## Measured on Sanic 25.12.1

* **`app.add_task` is not a place to touch request-scoped services.** The task is
  scheduled from the handler and runs *after* the response is sent, so it observes the
  unit already closed: `/background` records `background:closed=True`. PRD 11 §10.1
  (`T11-10`) expects an open unit for every host; on this host the measurement
  disagrees, and `tests/test_sanic.py` asserts the measured order so the page can be
  corrected.
* **A `def` handler runs on `MainThread`** — inline on the loop, so a blocking route
  blocks every other request (`T11-12`, `SNC-4`).
* **A streaming handler may return `None`.** `/stream-none` does `request.respond()`,
  sends three chunks, `eof()`, and returns `None`; no `will not be sent to client`
  error is logged, and the seam still closes after the last message (`T11-15`).
* **The lifespan must be entered explicitly.** There is no built-in in-process client;
  a request served outside `asgi_lifespan()` does not quietly run without a container,
  it fails in Sanic's own loop lookup (`T11-13`, `SNC-5`).
