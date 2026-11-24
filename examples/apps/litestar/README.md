# `litestar` — `lauren_di` with Litestar

A single-file example of hosting `lauren_di` inside Litestar. It is the async page of
PRD 11, reduced to one app, one adapter module and one test module.

## The adapter, in three lines

| Step | Where | What it does |
| --- | --- | --- |
| start | the app factory, before the server accepts connections | builds the container, registers providers, awaits `startup()` before the host accepts work (IC-1) |
| open | an `ASGIMiddleware` that owns the scope | opens one `ResolutionScope` per unit of work, after the host bound its own state (IC-3) |
| close | after `await app(scope, receive, send)` returns inside the middleware | closes it exactly once, in a `finally` (IC-7), and reports `scope.errors` (IC-8) |

## Files

| File | Purpose |
| --- | --- |
| `litestar_app.py` | the runnable app: providers, one route or command, and the entry point |
| `litestar_di.py` | the adapter: the host hook, nothing else |
| `tests/test_litestar.py` | the tests; they skip when Litestar is not installed (D-62) |

Both modules import the shared, framework-free reference adapter from
[`../_host.py`](../_host.py): `build_container`, `HostUnit` and — for a
synchronous host — `SyncBridge`.

## What this host forces

Litestar passes `state` through the ASGI scope, so the scope object travels there.

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
# the tests (skipped, with a reason, when Litestar is absent)
python -m pytest examples/apps/litestar -q

# every app whose host can be installed here
nox -s examples

# install the host this app pins
pip install -r examples/apps/litestar/requirements.txt
```

The test client is `litestar.testing.TestClient`.
