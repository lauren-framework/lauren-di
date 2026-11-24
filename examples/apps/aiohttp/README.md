# `aiohttp` — `lauren_di` with aiohttp

A single-file example of hosting `lauren_di` inside aiohttp. It is the async page of
PRD 11, reduced to one app, one adapter module and one test module — and on aiohttp the
rule is the opposite of Sanic's: the handler owns the body, and the middleware closes
(§5.5).

## The adapter, in three lines

| Step | Where | What it does |
| --- | --- | --- |
| start | `on_startup` / `on_cleanup` | builds the container, registers providers, awaits `startup()` before the first request and `shutdown()` after the last one |
| open | the `unit_of_work` middleware, before `await handler(request)` | opens one `HostUnit` per request, hands the `web.Request` to the container as the one value token, publishes the scope as `request["di"]` |
| close | the same middleware, after `await handler(request)` | closes the unit exactly once, at the point the handler finished writing the body |

Handlers never see the container, and they write their own body:

```python
async def stream(request: web.Request) -> web.StreamResponse:
    scope = aiohttp_di.current_scope(request)
    unit = await scope.resolve(RequestUnit)
    response = web.StreamResponse(headers={"content-type": "text/plain"})
    await response.prepare(request)
    for index in range(3):
        await response.write(f"{index}:{unit.request_id}\n".encode())
    await response.write_eof()
    return response
```

## The close point, measured

aiohttp gives the middleware exactly one post-handler moment, and what happens at that
moment depends on the shape of the response:

* **the handler wrote the body** (`prepare` → `write` → `write_eof`): the response is
  finished when the handler returns, so the middleware's close *is* the transport's
  finished point (AIO-4). Measured on the `/stream` route: `chunk:0`, `chunk:1`,
  `chunk:2`, `body:done`, then `unit:close`.
* **the handler returned an unwritten `web.Response`**: aiohttp writes the body after
  the middleware chain unwinds, so closing there disposes the unit first (AIO-3).
  Measured on the `/unwritten` route: `unit:close`, then `response-prepare`. The body
  still reaches the client — the failure is ordering, not delivery, which is exactly
  why it is worth measuring.

Every route except `/unwritten` therefore takes the first shape, and the middleware
records `Closed(path, after_body, errors)` for each request so the difference is a
test assertion rather than a comment.

## Files

| File | What is in it |
| --- | --- |
| `aiohttp_app.py` | the container factory, the providers (`Recorder`, `Greeting`, `RequestUnit`, `DoomedTeardown`), the routes, the `on_response_prepare` signal the tests read |
| `aiohttp_di.py` | the adapter: the `unit_of_work` middleware, `install`, `current_scope`, the value token, the `Closed` ledger |
| `tests/test_aiohttp.py` | the §10.1 cases, every one of them served on a real socket via `TestServer` |

## What this host forces

* **Handler-owned bodies.** The only way for the middleware's close to be the finished
  point is for the handler to have finished the body — the `/unwritten` route shows
  what the alternative costs.
* **Two closes, one code path.** A handler that raises wrote nothing, so the middleware
  closes in the exception path too; a unit is never left to the garbage collector.
* **A signal for the truth.** `on_response_prepare` fires where aiohttp starts writing
  headers, so recording it is how this example proves *when* the close happened.

## Rules the example follows

* IC-1/IC-2: one container, started before the host serves, shut down after it.
* IC-3/IC-7: one unit per request, closed exactly once.
* IC-4: the scope lives in the host's store (`request["di"]`), never in a module global.
* IC-8: a raising `@pre_destruct` is captured in `scope.errors`, handed to the app's
  report channel, and never raised into the response — `/teardown-error` still answers
  `200`.

## Run it

```bash
python examples/apps/aiohttp/aiohttp_app.py         # serves on a socket, prints the ledger
python -m pytest examples/apps/aiohttp -q           # the tests
```

The self-check prints one line per request plus the close ledger, so `after_body=True`
for every owned-body route and `after_body=False` for `/unwritten` are visible without
reading any code.

## Measured on aiohttp 3.14.1

* **Work after the body, inside the handler, sees an open unit.** aiohttp has no
  per-response background callable, so `/background` writes its body and *then* does
  its extra work before returning: the middleware has not closed yet, and the route
  records `background:closed=False` (`T11-10`, `AIO-2`).
* **A `def` handler runs on `MainThread`**, inline on the loop, and aiohttp warns at
  *registration* time that bare functions are deprecated. `/sync` exists to measure
  that (`T11-12`, `AIO-5`); it also lands on `AIO-3`'s branch — a returned
  `web.Response` gets its body written after the middleware closed — so its close
  ledger reads `after_body=False`.
* **`TestClient` runs both lifecycle hooks**: `greeting:start` appears on
  `start_server()` and `greeting:stop` on `close()`, never before (`T11-13`, `AIO-1`).
