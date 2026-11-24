# `fastapi` — `lauren_di` with FastAPI

A single-file example of hosting `lauren_di` inside FastAPI. It is the asgi page of
PRD 10, reduced to one app, one adapter module and one test module.

## The adapter, in three lines

| Step | Where | What it does |
| --- | --- | --- |
| start | the lifespan startup handler | builds the container, registers providers, awaits `startup()` before the host accepts work (IC-1) |
| open | the same pure ASGI middleware, plus `app.state` for the container | opens one `ResolutionScope` per unit of work, after the host bound its own state (IC-3) |
| close | after `await app(scope, receive, send)` returns — after the last body chunk | closes it exactly once, in a `finally` (IC-7), and reports `scope.errors` (IC-8) |

## Files

| File | Purpose |
| --- | --- |
| `fastapi_app.py` | the runnable app: providers, one route or command, and the entry point |
| `fastapi_di.py` | the adapter: the host hook, nothing else |
| `tests/test_fastapi.py` | the tests; they skip when FastAPI is not installed (D-62) |

Both modules import the shared, framework-free reference adapter from
[`../_host.py`](../_host.py): `build_container`, `HostUnit` and — for a
synchronous host — `SyncBridge`.

## What this host forces

`Depends` and `Annotated[T, Inject(token)]` are shown side by side.

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

## Run

```bash
# the tests (skipped, with a reason, when FastAPI is absent)
python -m pytest examples/apps/fastapi -q

# every app whose host can be installed here
nox -s examples

# install the host this app pins
pip install -r examples/apps/fastapi/requirements.txt
```

The test client is `fastapi.testclient.TestClient` (needs `httpx`).
