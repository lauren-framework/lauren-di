# `quart` — `lauren_di` with Quart

A single-file example of hosting `lauren_di` inside Quart. It is the async page of
PRD 11, reduced to one app, one adapter module and one test module.

## The adapter, in three lines

| Step | Where | What it does |
| --- | --- | --- |
| start | the app factory, before the server accepts connections | builds the container, registers providers, awaits `startup()` before the host accepts work (IC-1) |
| open | a wrapper around `app.asgi_app` — never `teardown_request` | opens one `ResolutionScope` per unit of work, after the host bound its own state (IC-3) |
| close | after the wrapped `asgi_app` returns, which is after the last chunk | closes it exactly once, in a `finally` (IC-7), and reports `scope.errors` (IC-8) |

## Files

| File | Purpose |
| --- | --- |
| `quart_app.py` | the runnable app: providers, one route or command, and the entry point |
| `quart_di.py` | the adapter: the host hook, nothing else |
| `tests/test_quart.py` | the tests; they skip when Quart is not installed (D-62) |

Both modules import the shared, framework-free reference adapter from
[`../_host.py`](../_host.py): `build_container`, `HostUnit` and — for a
synchronous host — `SyncBridge`.

## What this host forces

`teardown_request` fires before a streaming body is sent, so it is not a close point.

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
# the tests (skipped, with a reason, when Quart is absent)
python -m pytest examples/apps/quart -q

# every app whose host can be installed here
nox -s examples

# install the host this app pins
pip install -r examples/apps/quart/requirements.txt
```

The test client is `quart.testing.QuartClient`.

## Measured on quart 0.23.1

* **`teardown_request` is not a close point.** A three-chunk streamed body measures
  `unit:open`, `unit:close`, `chunk:0`, `chunk:1`, `chunk:2` — every chunk is produced
  by an already-disposed unit. `create_app(teardown_owner=True)` installs exactly that
  owner so `tests/test_quart.py` can assert the trap (`T11-06`), and it installs no
  ASGI wrapper: the point is that something else is doing the closing.
* **`test_client()` runs no serving hook.** `before_serving`/`after_serving` never fire
  for `app.test_client()`, which is why the tests and the demo bracket their requests
  with `quart_di.startup(app)` / `quart_di.shutdown(app)` — those are thin wrappers over
  `app.startup()` / `app.shutdown()`, exactly what a server calls (`QRT-5`, `T11-13`).
  `QuartClient` supports `async with` only.
* **The wrapper closes after the body and after the background task.** Measured order
  on `/stream`: `chunk:0..2`, `background:closed=False`, `unit:close` — four
  `http.response.body` messages had crossed the seam at close time.
* **A `def` view runs on `asyncio_0`**, never `MainThread` (`T11-12`).
* **`current_scope()` outside a request is a diagnosis, not an `AttributeError`.** It
  turns Quart's "Not within an app context" into the message naming `quart_di.install`.
