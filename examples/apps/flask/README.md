# `flask` — `lauren_di` with Flask

A single-file example of hosting `lauren_di` inside Flask. It is the wsgi page of
PRD 12, reduced to one app, one adapter module and one test module.

## The adapter, in three steps

1. **start** the container once, on the process-wide `_host.SyncBridge`;
2. **open** one scope per request, in a WSGI middleware, once Flask has bound
   `environ`;
3. **close** it from the response iterable's `close()` — after the last chunk,
   never at view return.

### One loop, not one per request (PRD 12 §5.2, IC-10)

A Flask view is a plain function on a worker thread with no running loop, and
every container entry point is a coroutine. `flask_di` therefore sends every
container call through `_host.SyncBridge`: one daemon thread, one event loop,
created once and reused for the life of the process.

`asyncio.run()` per request would be worse than merely slow — the loop is closed
with the call, so a scope opened on one request's loop could never be closed on
the next request's loop, and every request would pay for a thread and a selector.
`test_one_loop_serves_every_request` asserts that three requests add no loop
thread, and `RequestId.built_on` shows the loop thread the provider was built on.

### The close point is the body (PRD 12 §5.3)

`app.wsgi_app = adapter.wrap(app.wsgi_app)` wraps `wsgi_app` rather than the app
object, so `url_for`, `test_client` and the Flask CLI keep working while a real
server still runs the middleware.

The middleware returns a generator whose `finally` closes the unit. A server that
streams, stalls or hangs up early always ends with `close()` (PEP 3333), so the
teardown runs exactly once — and only then.

Measured on `/stream`, whose body is produced *after* the view returned:

```
uow:begin:…          # resolved in the view
handler:returned     # the view function is done
flask:teardown-request
chunk:1
chunk:2              # the body, produced after the view returned
uow:commit:…         # the scope closes at the end of the iterable
```

That ordering is why `teardown_request` is not the close point: it fires
**between** the view returning and the first chunk, so closing there would
destroy request-scoped objects while the generator is still handing out chunks.
`test_flask_teardown_request_fires_before_the_body_is_streamed` pins that order,
and `test_the_scope_closes_when_the_body_ends_not_when_the_view_returns` proves
the `close()` path for a client that hangs up after one chunk.

If the host raises before returning an iterable there is no iterable left to
close, so `__call__` closes the unit itself and re-raises
(`test_a_host_that_raises_before_returning_an_iterable_does_not_leak_the_scope`).

A failing `@pre_destruct` never rewrites the response: it is reported through the
middleware's `report=` callable (and logged by lauren_di), while the client still
gets its 200 (`test_a_failing_teardown_is_reported_not_raised`, IC-8).

## Files

| File | Purpose |
| --- | --- |
| `flask_app.py` | the runnable app: providers, three routes, the entry point |
| `flask_di.py` | the adapter: `WSGIAdapter` (container + bridge), `DIMiddleware`, the `ENVIRON` token |
| `tests/test_flask.py` | the tests |

Both modules import the shared, framework-free reference adapter from
[`../_host.py`](../_host.py) — `build_container`, `HostUnit`, `SyncBridge` and
`SCOPE_KEY` — so the host hook is the only Flask-specific code.

## Run

```bash
pip install -r examples/apps/flask/requirements.txt
python examples/apps/flask/flask_app.py   # http://127.0.0.1:8000/hello, /stream, /oops
python -m pytest examples/apps/flask
```

The test client is `flask.testing.FlaskClient`. The tests close its responses
explicitly: a test client is allowed to abandon an iterable that a real server
would always close.

## Tests

| Test | What it measures |
| --- | --- |
| `test_one_scope_per_request_and_a_fresh_object_each_time` | T12-03, one scope per request |
| `test_the_object_the_view_used_is_the_one_the_scope_closed` | T12-04, the view's object is the one torn down |
| `test_the_scope_closes_when_the_body_ends_not_when_the_view_returns` | the `close()` close point, after the last chunk |
| `test_flask_teardown_request_fires_before_the_body_is_streamed` | why `teardown_request` cannot own the scope |
| `test_the_whole_body_reads_before_the_commit` | a drained body commits once, last |
| `test_a_failing_teardown_is_reported_not_raised` | IC-8 |
| `test_startup_runs_once_before_the_first_request` | T12-02 |
| `test_one_loop_serves_every_request` | IC-10, one bridge loop |
| `test_a_host_that_raises_before_returning_an_iterable_does_not_leak_the_scope` | the failure path |
| `test_a_real_server_serves_the_handler_off_the_calling_thread` | T12-01/T12-05 through a threaded WSGI server |
