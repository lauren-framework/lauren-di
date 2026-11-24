# `falcon` — `lauren_di` with Falcon

A single-file example of hosting `lauren_di` inside Falcon. It is the wsgi page of
PRD 12, reduced to one app, one adapter module and one test module.

## The adapter, in three steps

1. **start** the container once, on the process-wide `_host.SyncBridge`;
2. **open** one scope per request, in a WSGI middleware wrapped *around* the
   `falcon.App`, once the server has bound `environ`;
3. **close** it from the response iterable's `close()` — after the last chunk,
   never at responder return.

### One loop, not one per request (PRD 12 §5.2, IC-10)

A Falcon responder is a plain function on a worker thread with no running loop,
and every container entry point is a coroutine. `falcon_di` therefore sends every
container call through `_host.SyncBridge`: one daemon thread, one event loop,
created once and reused for the life of the process.

`asyncio.run()` per request would be worse than merely slow — the loop is closed
with the call, so a scope opened on one request's loop could never be closed on
the next request's loop, and every request would pay for a thread and a selector.
`test_one_loop_serves_every_request` asserts that three requests add no loop
thread, and `RequestId.built_on` shows the loop thread the provider was built on.

### The close point is the body (PRD 12 §5.3)

Falcon's own hooks are all on the wrong side of the stream. `process_response`
runs inside `App.__call__`, so it finishes before the WSGI server reads the first
chunk of `resp.stream`, and `req.context` is dropped with the request object. The
adapter therefore cannot be a Falcon middleware: the app itself is wrapped,

```python
application = adapter.wrap(falcon.App(middleware=[ResponseWatcher()]), report=record_errors)
```

and the middleware returns a generator whose `finally` closes the unit. A server
that streams, stalls or hangs up early always ends with `close()` (PEP 3333), so
the teardown runs exactly once — and only then.

Measured on `/stream`, whose body is produced *after* the responder returned:

```
uow:begin:…            # resolved in the responder
handler:returned       # the responder is done
falcon:process-response
chunk:1
chunk:2                # the body, produced after the responder returned
uow:commit:…           # the scope closes at the end of the iterable
```

`test_falcon_process_response_fires_before_the_body_is_streamed` pins that order,
and `test_the_scope_closes_when_the_body_ends_not_when_the_responder_returns`
proves the `close()` path for a client that hangs up after one chunk.

If the app raises before returning an iterable there is no iterable left to
close, so `__call__` closes the unit itself and re-raises
(`test_a_host_that_raises_before_returning_an_iterable_does_not_leak_the_scope`).

A failing `@pre_destruct` never rewrites the response: it is reported through the
middleware's `report=` callable (and logged by lauren_di), while the client still
gets its 200 (`test_a_failing_teardown_is_reported_not_raised`, IC-8).

## Files

| File | Purpose |
| --- | --- |
| `falcon_app.py` | the runnable app: providers, three resources, the entry point |
| `falcon_di.py` | the adapter: `WSGIAdapter` (container + bridge), `DIMiddleware`, the `ENVIRON` token |
| `tests/test_falcon.py` | the tests |

Both modules import the shared, framework-free reference adapter from
[`../_host.py`](../_host.py) — `build_container`, `HostUnit`, `SyncBridge` and
`SCOPE_KEY` — so the host hook is the only Falcon-specific code.

## Run

```bash
pip install -r examples/apps/falcon/requirements.txt
python examples/apps/falcon/falcon_app.py   # http://127.0.0.1:8001/hello, /stream, /oops
python -m pytest examples/apps/falcon
```

The test client is `falcon.testing.TestClient`; it drains and closes the iterable,
which is exactly what the close point depends on.

## Tests

| Test | What it measures |
| --- | --- |
| `test_one_scope_per_request_and_a_fresh_object_each_time` | T12-03, one scope per request |
| `test_the_object_the_responder_used_is_the_one_the_scope_closed` | T12-04, the responder's object is the one torn down |
| `test_the_scope_closes_when_the_body_ends_not_when_the_responder_returns` | the `close()` close point, after the last chunk |
| `test_falcon_process_response_fires_before_the_body_is_streamed` | why `process_response` cannot own the scope |
| `test_the_whole_body_reads_before_the_commit` | a drained body commits once, last |
| `test_a_failing_teardown_is_reported_not_raised` | IC-8 |
| `test_startup_runs_once_before_the_first_request` | T12-02 |
| `test_one_loop_serves_every_request` | IC-10, one bridge loop |
| `test_a_host_that_raises_before_returning_an_iterable_does_not_leak_the_scope` | the failure path |
| `test_a_real_server_serves_the_responder_off_the_calling_thread` | T12-01/T12-05 through a threaded WSGI server |
