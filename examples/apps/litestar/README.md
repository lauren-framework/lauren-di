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

## Measured on litestar 2.25.0

Three things were measured while building this example rather than assumed, and each
one is pinned by a test in `tests/test_litestar.py`:

* **`after_response` takes one callable, not a list.** PRD 11 §5.2 shows
  `after_response=[close_scope]`; the list form fails every request with
  `TypeError: 'list' object is not callable`, so `hooks_only()` passes the callable
  directly.
* **That hook is called with the request, not the response.** The page's snippet
  annotates it as a `Response` and reads `response.request`, which raises
  `AttributeError: 'Request' object has no attribute 'request'`.
* **A `def` handler runs on a worker thread.** With `sync_to_thread=True` it lands on
  anyio's `asyncio_0`; without the flag it logs `LitestarWarning` and lands on the
  router's portal thread. It is never `MainThread`, and the test asserts that.

`TestClient` runs `on_startup` when the `with` block is entered and `on_shutdown` when
it exits — nothing before, nothing after — so a test that builds a client without
entering it sees a container that has not started (`T11-13`). The `/scope` route exists
for `T11-04`: it reports what `request.state["di"]` actually holds, which is a live
`ResolutionScope`, not `None`.
