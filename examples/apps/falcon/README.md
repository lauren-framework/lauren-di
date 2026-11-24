# `falcon` — `lauren_di` with Falcon

A single-file example of hosting `lauren_di` inside Falcon. It is the wsgi page of
PRD 12, reduced to one app, one adapter module and one test module.

## The adapter, in three lines

| Step | Where | What it does |
| --- | --- | --- |
| start | the process entry point, before the first request | builds the container, registers providers, awaits `startup()` before the host accepts work (IC-1) |
| open | a WSGI middleware around the Falcon app | opens one `ResolutionScope` per unit of work, after the host bound its own state (IC-3) |
| close | after the response iterable's `close()` | closes it exactly once, in a `finally` (IC-7), and reports `scope.errors` (IC-8) |

## Files

| File | Purpose |
| --- | --- |
| `falcon_app.py` | the runnable app: providers, one route or command, and the entry point |
| `falcon_di.py` | the adapter: the host hook, nothing else |
| `tests/test_falcon.py` | the tests; they skip when Falcon is not installed (D-62) |

Both modules import the shared, framework-free reference adapter from
[`../_host.py`](../_host.py): `build_container`, `HostUnit` and — for a
synchronous host — `SyncBridge`.

## What this host forces

Falcon's `process_response` hook fires before the body is written.

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
# the tests (skipped, with a reason, when Falcon is absent)
python -m pytest examples/apps/falcon -q

# every app whose host can be installed here
nox -s examples

# install the host this app pins
pip install -r examples/apps/falcon/requirements.txt
```

The test client is `falcon.testing.TestClient`.
