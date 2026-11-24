# `bottle` — `lauren_di` with Bottle

A two-module example of hosting `lauren_di` inside Bottle: the app in `bottle_app.py`,
the adapter in `bottle_di.py`, and one test module. It is the WSGI page of PRD 12,
reduced to the one hook this host needs on top of the shared reference adapter in
[`../_host.py`](../_host.py).

## The adapter, in three lines

| Step | Where | What it does |
| --- | --- | --- |
| start | the process entry point, before the first request | builds the container, registers providers, awaits `startup()` before the host accepts work (IC-1) |
| open | a WSGI middleware around the Bottle app's callable | opens one `ResolutionScope` per unit of work, after the host bound its own state (IC-3) |
| close | after the response iterable's `close()` | closes it exactly once, in a `finally` (IC-7), and reports `scope.errors` (IC-8) |

## Files

| File | Purpose |
| --- | --- |
| `bottle_app.py` | the runnable app: providers, one route or command, and the entry point |
| `bottle_di.py` | the adapter: the host hook, nothing else |
| `tests/test_bottle.py` | the tests; they skip when Bottle is not installed (D-62) |

Both modules import the shared, framework-free reference adapter from
[`../_host.py`](../_host.py): `build_container`, `HostUnit` and — for a
synchronous host — `SyncBridge`.

## What this host forces

| | |
| --- | --- |
| Store | `bottle.request.environ` — the same dict the middleware wrote, reached through Bottle's thread-local proxy (BTL-4) |
| Who opens | the WSGI middleware, after `environ` exists (IC-3) |
| Who closes | the same middleware, from the response iterable's `close()` (BTL-3) |
| Not an owner | `after_request`: it fires when the route returns, before `chunk:0` (BTL-2) |
| Lifecycle | Bottle has none, so `startup()` lives in the entry point (BTL-5) |
| Thread | an in-process call runs the route on `MainThread`; every container call hops to the bridge loop (BTL-1, IC-10) |

Bottle ships no test client (PRD 12 §5.8), so `tests/test_bottle.py` is the client:
every request is an explicit `environ` handed to the WSGI callable, and the test
plays the server's part by reading the returned iterable and calling `close()` on
it. The missing client is an advantage here — the close point is ours to watch.

## The close point, measured

`python examples/apps/bottle/bottle_app.py` serves one streaming request through a
threaded `wsgiref` server and prints the observation log. The body is produced
lazily by a generator, and every chunk records whether the scope was still open:

```
GET /stream -> 200
chunk:0:closed=False
chunk:1:closed=False
chunk:2:closed=False
events: ['built', 'hook:after_request:closed=False', 'chunk:0', 'chunk:1', 'chunk:2', 'torn-down']
closes: 1 errors: ()
```

The close happens after `chunk:2`, not when the route returned: `torn-down` — the
`@pre_destruct` marker of the request-scoped provider — is the last event, and it is
the only one, so the unit closed exactly once. `hook:after_request` is Bottle's own
end-of-route hook, logged before the first chunk precisely to show why it cannot own
the scope.

## What the tests cover

`tests/test_bottle.py` names each case from PRD 12 §10.1 and asserts on the measured
values, never on a comment:

| Case | Test | Proof |
| --- | --- | --- |
| T12-02 | `test_startup_precedes_the_first_request` | one boot before the first request, none after two |
| T12-03 | `test_each_request_opens_its_own_scope` | two requests, two `scope-id`s |
| T12-04 | `test_the_scope_is_reachable_through_the_environ` | `scope=ResolutionScope` and the `PATH_INFO` the provider was handed |
| T12-01/T12-14 | `test_container_work_leaves_the_caller_thread`, `test_the_in_process_call_runs_on_the_main_thread` | the route on `MainThread`, the container work on `lauren-di-loop` |
| T12-05/T12-12 | `test_teardown_follows_the_last_chunk` | three chunks with `closed=False`, then `torn-down` |
| T12-09 | `test_after_request_is_not_the_owner` | the hook precedes `chunk:0` |
| T12-10 | `test_an_abandoned_body_still_closes_once` | one chunk read, then closed: one close, `errors == ()` |
| T12-11 | `test_a_failing_route_closes_once` | 500, the error handler sees `closed=False`, one close |
| T12-13 | `test_the_singleton_keeps_loop_affinity` | one singleton, one loop, two requests |

T12-06 (Flask's teardown) and T12-07 (Falcon's `process_response`) are the other two
hosts' negative controls; Bottle's is T12-09 above.

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
# one streaming request through a threaded wsgiref server, and its event log
python examples/apps/bottle/bottle_app.py

# the tests (skipped, with a reason, when Bottle is absent)
python -m pytest examples/apps/bottle -q

# every app whose host can be installed here
nox -s examples

# install the host this app pins
pip install -r examples/apps/bottle/requirements.txt
```

The test client is the WSGI callable driven directly with an `environ` mapping.
