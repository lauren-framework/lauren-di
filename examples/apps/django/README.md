# `django` — `lauren_di` with Django

Hosting `lauren_di` inside Django, in one app: one adapter module, one app
module, one test module. It is the Django page of PRD 13, reduced to something
you can run. Nothing here is scaffolding you have to keep — a real project moves
the settings into `settings.py` and keeps the adapter as it is.

## The adapter, in three steps

| Step | Where | What it does |
| --- | --- | --- |
| start | `AppConfig.ready()` | builds the container, compiles it, and runs `startup()` on a dedicated loop — once per process (IC-1, IC-9) |
| open | the `with_scope` decorator | opens one `HostUnit` per request, *after* Django has bound the request, and stores it on the request object (IC-3, IC-4) |
| close | after `response.close()` | closes the unit exactly once, in a `finally`; a streaming response is closed after its last chunk, so it outlives the view (IC-7, DJ-9, DJ-10) |

`ready()` is synchronous and cannot await, while `startup()` is a coroutine, so
the container is started through `_host.SyncBridge`: **one** long-lived loop on
one daemon thread, shared by every sync request. `asyncio.run()` in `ready()`
looks simpler and is wrong — measured in PRD 13 §5.2, it leaves every loop-bound
singleton attached to a loop that is already closed.

## Why a decorator and not a middleware

Django's middleware is the obvious hook and does not fit, for reasons PRD 13 §6.3
measured against `WSGIHandler`, `ASGIHandler` and `django.test.Client` alike:

* a middleware's `finally` runs **before** the first chunk of a streaming
  response, so a scope opened there is closed while the body is still being
  produced;
* `request_finished` fires *after* the body was written, which is too late to
  hand a scope to the view;
* a middleware that resolves `REQUEST` tokens is itself the middleware that
  decides when the scope opens, so the bracket and the consumer are the same
  object.

The decorator is exactly one bracket around exactly one view, and it can tell an
`async def` view from a `def` one — which is what decides whether the close is
awaited or bridged.

## Why a `ContextVar` is *not* a substitute

The tempting shortcut is a `ContextVar` holding the current scope: set it in the
middleware, read it in the repository. PRD 13 §5.4 measured that Django's
`sync_to_async(..., thread_sensitive=True)` — the only correct way for an async
view to call sync code — copies the calling context **into** the worker thread.
That is why the shortcut *seems* to work, and why it is a trap:

* the copy is a *copy*: the context the thread writes back is not the context the
  view reads, and whether a write even travels back is an implementation detail
  of the hop (this asgiref shares the context object; a different `executor=` or
  a `thread_sensitive=False` hop does not). The scope must not depend on that —
  a scope resolved inside the thread is not the scope the view sees outside it
  (DJ-13);
* a `Task` is not a request: nothing guarantees one task per request, or that a
  library does not wrap the view in another context;
* in tests the leak is visible in the other direction — a sync view's
  `ContextVar.set()` lands in the caller's context, so a green `TestCase` can
  leave ambient state behind (DJ-21, measured here: `client.cv_after=set-by-sync-view`).

So the scope travels where the host can see it: on the `HttpRequest`
(`_host.SCOPE_KEY`), never in a module global and never in ambient context
(D-58, IC-4).

## What this host forces

| Django fact | Consequence in the adapter |
| --- | --- |
| `ready()` runs once per process, but in both the reloader's watcher and its child | the container is built there and nowhere else, and `ready()` never calls `sys.exit()` (DJ-1, DJ-4) |
| a `def` view has no running loop, and may be called *from* one | it resolves through `bridge.run(...)`, never `asyncio.run(...)` (DJ-12) |
| an `async def` view has its own loop | it resolves with `await scope.resolve(...)`; the loop differs from the bridge's (DJ-11) |
| `django.db.connections["default"]` is thread-affine | the wrapper is handed in as a per-unit *value*, never a provider and never a singleton, and is never closed here (DJ-15 … DJ-17) |
| `override_settings` cannot reach a container built before it | a test that needs other settings builds its own container inside the override, through `OverridePlan` (DJ-18) |
| a `StreamingHttpResponse` may be consumed after the view returns | its `streaming_content` is replaced with a generator whose `finally` closes the unit (DJ-9) |

The connection is worth a second look: `register_value(...)` on the live
container would be a mutation of a container that is already serving requests
(IC-11 forbids it), so the token is declared up front with
`build_container(value_tokens=(REQUEST, CONNECTION))` and supplied through
`values=` for every unit (IC-6). `close_old_connections` remains the host's job.

## Files

| File | Purpose |
| --- | --- |
| `django_app.py` | the app: `SETTINGS`, `DjangoAppConfig.ready()`, the providers, a sync view, an async view, a streaming view, a raising view, an ORM view, a settings view, the URLconf, and a runnable demo |
| `django_di.py` | the adapter: `start_container()`, `stop_container()`, `with_scope()`, `resolve()` / `scope_of()`, the two value tokens — no app knowledge |
| `tests/test_django.py` | one test per requirement in PRD 13's inventory, named `test_djN_...` |

Both modules import the shared, framework-free reference adapter from
[`../_host.py`](../_host.py): `build_container`, `HostUnit`, `SCOPE_KEY` and,
for a synchronous host, `SyncBridge`.

## Rules the example does not bend

* the scope is stored in the host's own per-unit state (`request`), never in a
  module global or a `ContextVar` (D-58);
* the view resolves through `scope.resolve`, never `container.resolve`, so the
  scope cache is the only cache (IC-5);
* host-owned objects (the request, the connection) are declared with
  `value_tokens=` and supplied through `values=`, never registered as providers
  (IC-6);
* the adapter never closes a Django connection; `close_old_connections` does
  (DJ-17);
* overrides are applied when the container is built, never to a live container
  (IC-11);
* nothing builds a container at import time — only `configure()` (the
  `manage.py` half) reaches `ready()` (DJ-1).

## Probes covered

| # | Requirement | Test |
| --- | --- | --- |
| DJ-1 | build in `ready()`, not at import | `test_dj1_the_container_is_built_by_ready_not_at_import` |
| DJ-2 | `compile()` errors become `ImproperlyConfigured` | `test_dj2_bad_wiring_is_improperly_configured_at_boot` |
| DJ-3 | `compile()` is sync, `startup()` is a coroutine | `test_dj3_compile_is_sync_and_startup_is_a_coroutine` |
| DJ-4 | a failed `startup()` is re-raised, never `sys.exit()` | `test_dj4_a_startup_failure_is_re_raised_not_sys_exit` |
| DJ-5 | the container lives on the `AppConfig` | `test_dj5_the_container_lives_on_the_app_config` |
| DJ-6 | one bridge loop per process | `test_dj6_one_bridge_loop_per_process_and_sync_views_resolve_on_it` |
| DJ-7 | one scope per request, opened by the wrapper | `test_dj7_the_decorator_opens_one_scope_per_request` |
| DJ-8 | no middleware attaches or brackets anything | `test_dj8_no_middleware_attaches_or_brackets_anything` |
| DJ-9 | a stream closes after the last chunk | `test_dj9_a_streaming_response_outlives_the_view`, `test_dj9b_an_async_stream_closes_after_the_last_chunk` |
| DJ-10 | a raising view still closes the scope | `test_dj10_a_raising_view_still_closes_the_scope` |
| DJ-11 | an async view awaits, on its own loop | `test_dj11_an_async_view_awaits_the_scope_on_its_own_loop` |
| DJ-12 | a sync view bridges, even inside a running loop | `test_dj12_a_sync_view_bridges_even_inside_a_running_loop` |
| DJ-13 | `sync_to_async` copies context in; not the scope | `test_dj13_a_context_var_cannot_carry_the_scope` |
| DJ-14 | no middleware, so no `async_capable` flag | `test_dj14_there_is_no_middleware_to_declare_sync_or_async_capable` |
| DJ-15 | the connection is a value | `test_dj15_the_connection_is_handed_in_as_a_value` |
| DJ-16 | the connection is never a singleton | `test_dj16_the_connection_is_never_a_singleton` |
| DJ-17 | teardown never closes the connection | `test_dj17_teardown_never_closes_the_connection` |
| DJ-18 | settings are frozen at build time; `OverridePlan` for the rest | `test_dj18_a_container_built_in_ready_is_a_process_singleton`, `test_dj18_an_override_plan_is_a_build_time_decision` |
| DJ-19 | a `TestCase` reuses the production bracket | `TestCaseUsesTheProductionBracket::test_dj19_the_view_runs_inside_the_production_bracket` |
| DJ-20 | `SimpleTestCase` does not touch `startup()` | `TestCaseUsesTheProductionBracket::test_dj20_a_simple_test_case_does_not_touch_startup` |
| DJ-21 | a green test says nothing about threads or ambient state | `test_dj21_the_client_runs_the_view_on_the_callers_thread_and_dirties_it` |
| DJ-22 | no ambient scope: open it explicitly | `test_dj22_there_is_no_ambient_scope_to_lean_on` |

The same file then runs PRD 13 §10's test plan, `T13-01 … T13-16`, through the
seams `django.test.Client` hides: the real `WSGIHandler` and `ASGIHandler`, a
subprocess pair, `transaction.atomic()`, and one threaded Werkzeug server.

| # | What is driven | Test |
| --- | --- | --- |
| T13-01 | `django.setup()` twice in one process | `test_t13_01_setup_twice_in_one_process_runs_ready_once` |
| T13-02 | the reload model: two subprocesses | `test_t13_02_two_processes_each_run_ready_once` |
| T13-03 | `WSGIHandler`, `def` view | `test_t13_03_the_wsgi_handler_runs_a_sync_view_on_the_calling_thread` |
| T13-04 | `ASGIHandler`, `def` view | `test_t13_04_the_asgi_handler_runs_a_sync_view_on_a_worker_thread` |
| T13-05 | `ASGIHandler`, `async def` view | `test_t13_05_the_asgi_handler_runs_an_async_view_on_the_loop_thread` |
| T13-06 | `WSGIHandler`, streaming view | `test_t13_06_a_wsgi_stream_closes_after_the_last_chunk` |
| T13-07 | `ASGIHandler`, blocking `receive` | `test_t13_07_an_asgi_stream_closes_in_the_same_order` |
| T13-08 | a client that abandons the body | `test_t13_08_an_abandoned_stream_closes_exactly_once` |
| T13-09 | `request_started` / `request_finished` | `test_t13_09_request_finished_may_fire_twice_but_the_scope_closes_once` |
| T13-10 | the connection token in two threads | `test_t13_10_two_threads_get_two_connection_wrappers` |
| T13-11 | teardown inside `transaction.atomic()` | `test_t13_11_teardown_inside_a_transaction_leaves_the_connection_usable` |
| T13-12 | `override_settings` and a fresh container | `test_t13_12_a_test_that_changes_settings_builds_its_own_container` |
| T13-13 | no middleware, so no capability flags | `test_t13_13_there_is_no_middleware_to_declare_async_capable` |
| T13-14 | a dirty `ContextVar` between requests | `test_t13_14_a_dirty_context_cannot_hand_a_scope_to_the_next_request` |
| T13-15 | a missing provider at boot | `test_t13_15_a_missing_provider_fails_boot_not_the_first_request` |
| T13-16 | `Client` vs a threaded Werkzeug server | `test_t13_16_the_client_is_not_a_threaded_server` |

## Run

```bash
# the tests (skipped, with a reason, when Django is absent)
python -m pytest examples/apps/django -q

# the app itself: every route through django.test.Client, plus the lifecycle
python examples/apps/django/django_app.py

# every app whose host can be installed here
nox -s examples

# install the host this app pins
pip install -r examples/apps/django/requirements.txt
```

There is no `manage.py`, no migrations package and no database file: the example
configures settings itself (`django_app.SETTINGS` through
`settings.configure()`), calls `django.setup()`, and drives the views with
`django.test.Client` and `AsyncClient` — the same configuration a real project's
`manage.py` and `wsgi.py` do. The `AsyncClient` case is the only one that needs
`asyncio.run` of its own, because an async view has to be awaited on a loop.
