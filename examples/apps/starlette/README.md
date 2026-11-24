# `starlette` — `lauren_di` with Starlette

A single-file example of hosting `lauren_di` inside Starlette. It is the asgi page of
PRD 10, reduced to one app, one adapter module and one test module.

## The adapter, in three lines

| Step | Where | What it does |
| --- | --- | --- |
| start | the lifespan startup handler | builds the container, registers providers, awaits `startup()` before the host accepts work (IC-1) |
| open | a pure ASGI middleware added with `app.add_middleware(...)` | opens one `ResolutionScope` per unit of work, after the host bound its own state (IC-3) |
| close | after `await app(scope, receive, send)` returns — after the last body chunk | closes it exactly once, in a `finally` (IC-7), and reports `scope.errors` (IC-8) |

## Files

| File | Purpose |
| --- | --- |
| `starlette_app.py` | the runnable app: providers, one route or command, and the entry point |
| `starlette_di.py` | the adapter: the host hook, nothing else |
| `tests/test_starlette.py` | the tests; they skip when Starlette is not installed (D-62) |

Both modules import the shared, framework-free reference adapter from
[`../_host.py`](../_host.py): `build_container`, `HostUnit` and — for a
synchronous host — `SyncBridge`.

## What this host forces

The lifespan handler runs `container.startup()` and `container.shutdown()`.

## Rules the example does not bend

* the scope is stored in the host's own per-unit state under `_host.SCOPE_KEY`,
  never in a module global (D-58);
* the handler resolves through `scope.resolve`, never `container.resolve`, so the
  scope cache is the only cache (IC-5);
* host-owned objects (a request, a connection) are declared with `value_tokens=`
  and supplied through `values=`, never registered as a provider (IC-6);
* the container is started once and shut down once; both tuples are reported
  (IC-9);
* overrides are applied when the container is built, never to a live container
  (IC-11).

## The close point, measured

`TestClient` is not the instrument for this: it reads the body for you, so it
cannot say whether teardown ran before or after the last chunk. `tests/test_starlette.py`
therefore calls the app directly, records every outgoing ASGI message in the same
event log the app and its providers write to, and compares *positions* in that log.
These are the rows it measures (PRD 10 §10.1, `T10-nn`), all reproduced by this app:

| Path | Measured order in the shared log | Answer |
| --- | --- | --- |
| lifespan start | `startup` before the first request reaches a route | 200 |
| `GET /greet/ada` | `construct` → `teardown` → `shutdown` (on exit) | 200 |
| `GET /stream`, pure-ASGI owner | `http.response.body` ×3, each `closed=False` → `teardown` | 200 |
| `GET /stream`, `BaseHTTPMiddleware` owner (negative control) | chunk 1 `closed=False`, chunks 2-3 `closed=True` | 200 |
| `GET /audit` (background task) | `background closed=False` → `teardown` | 200 |
| `GET /guarded` (handled `LookupError`) | exactly one `teardown` | 418 |
| `GET /unhandled` (`RuntimeError`) | exactly one `teardown` | 500 |
| `GET /teardown-fails` | `teardown-raised` → reported as `teardown-error` | 200 |
| shutdown with a leaky singleton | `shutdown` → one `teardown-error`, reported, never raised | — |

The measured conclusion: `DIScopeMiddleware.__call__`'s `finally` runs **after the
last body chunk**, so every chunk of `/stream` is produced by a live service. The
same route owned by `BaseHTTPMiddleware.dispatch` closes after chunk 1, which is
why this app never uses it for ownership.

## Run

```bash
# the tests (skipped, with a reason, when Starlette is absent)
python -m pytest examples/apps/starlette -q

# drive the app in-process and print the measured close point (exits 0)
python examples/apps/starlette/starlette_app.py

# every app whose host can be installed here
nox -s examples

# install the host this app pins
pip install -r examples/apps/starlette/requirements.txt
```

The test client is `starlette.testclient.TestClient` (needs `httpx`).
