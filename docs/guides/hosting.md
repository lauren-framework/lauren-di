# Hosting `lauren_di`

`lauren_di` is a container, not a runtime. Something has to build it, start it,
hand out one `ResolutionScope` per unit of work, and close that scope when the
host has finished with the unit. That something is a **host adapter**, and this
page is the framework-neutral contract it has to keep — the twelve obligations,
the close point for each host shape, the synchronous bridge, and the two
mistakes that cost a production incident each.

The framework pages specialise it: ASGI apps in PRD 10, the async frameworks in
PRD 11, WSGI in PRD 12, Django in PRD 13 and the CLI libraries in PRD 14.
Everything here is executable on a bare interpreter, so the contract is verified
before a framework is involved.

## The twelve obligations

| # | Obligation | Why |
| --- | --- | --- |
| IC-1 | one `DIContainer` per process (or per app instance), `await container.startup()` before the host accepts work | a lazy first touch from two threads constructs a singleton twice and keeps one |
| IC-2 | construct with `strict_request_scope=True` unless the host has no `REQUEST` provider, and say which | in lax mode an unscoped resolve is cached and never torn down |
| IC-3 | exactly one `ResolutionScope` per unit of work, opened after the host has bound its own state | `values=` is fixed at open, so an early scope cannot see the request |
| IC-4 | store the scope in the host's own per-unit state and pass it explicitly | there is no ambient scope; a module global is overwritten by the next unit |
| IC-5 | resolve through `scope.resolve`, never `container.resolve`, while a unit is open | `container.resolve` bypasses the scope cache and builds a second instance |
| IC-6 | declare host objects with `value_tokens=` and supply them through `values=` | a `SINGLETON` that depends on a value token captures the first request's object |
| IC-7 | close the scope once, in a `finally`, at the host's finished point | "after the handler returns" is early for a streaming body |
| IC-8 | read the close tuple and report it through the host's log, metric or error channel | the response may already be committed; a teardown error must not replace it |
| IC-9 | `await container.shutdown()` once at process stop and report its tuple; treat a failed `startup()` as fatal | a failed `@post_construct` leaves the cache half-built |
| IC-10 | in a sync host, run every container call on one long-lived loop, or restrict providers to loop-agnostic ones | a loop-bound singleton built with `asyncio.run` is unusable on the next unit |
| IC-11 | apply overrides when the container is built, never to a live container | an override is a build-time decision |
| IC-12 | keep the adapter outside this library | the library imports no framework; the adapter is its `ImportError` |

Two of them are easy to miss. IC-6's singleton clause is silent: `compile()`
skips declared value tokens, so a `SINGLETON` holding a value token validates and
then keeps the first unit's object forever. IC-7's "finished" clause depends on
the transport, not on the handler — which is why the next table exists.

## The reference adapter

Two objects, no framework. This is the whole adapter, and it runs:

<!-- docs-contract: run -->
```python
import asyncio
from collections.abc import AsyncIterator
from typing import Annotated, Any

from lauren_di import (
    DIContainer,
    Inject,
    ResolutionScope,
    Scope,
    Token,
    injectable,
    use_factory,
)

REQUEST = Token("REQUEST")
EVENTS: list[str] = []


class HostAdapter:
    """One container per process; one scope per unit of work."""

    def __init__(self, container: DIContainer) -> None:
        self.container = container
        self.errors: list[Any] = []

    async def start(self) -> None:
        await self.container.startup()

    async def stop(self) -> tuple[Any, ...]:
        return await self.container.shutdown()

    def unit(self, values: dict[Any, Any] | None = None) -> "Unit":
        return Unit(self, values or {})

    def report(self, errors: tuple[Any, ...]) -> None:
        self.errors.extend(errors)


class Unit:
    """One request, job, command or connection. Single use."""

    def __init__(self, adapter: HostAdapter, values: dict[Any, Any]) -> None:
        self._adapter = adapter
        self._values = values
        self.scope: ResolutionScope | None = None

    async def __aenter__(self) -> ResolutionScope:
        self.scope = self._adapter.container.scope(values=self._values)
        return await self.scope.__aenter__()

    async def __aexit__(self, *exc_info: Any) -> bool:
        assert self.scope is not None, "unit entered without a scope"
        self._adapter.report(await self.scope.close())
        return False


class Connection:
    """A resource whose lifetime is the unit, not the process."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.closed = False

    async def aclose(self) -> None:
        self.closed = True
        EVENTS.append(f"connection.closed:{self.name}")


async def open_connection() -> AsyncIterator[Connection]:
    connection = Connection(name="unit")
    try:
        yield connection
    finally:
        await connection.aclose()


@injectable(scope=Scope.REQUEST)
class Repo:
    def __init__(self, request: Annotated[Any, Inject(REQUEST)]) -> None:
        self.request = request


async def main() -> None:
    container = DIContainer(value_tokens=(REQUEST,), strict_request_scope=True)
    container.register(Repo)
    container.register_custom(
        use_factory(provide=Connection, factory=open_connection, scope=Scope.REQUEST)
    )
    adapter = HostAdapter(container)
    await adapter.start()

    first = {"path": "/first"}
    async with adapter.unit({REQUEST: first}) as scope:
        assert await scope.resolve(Repo) is await scope.resolve(Repo)
        assert (await scope.resolve(Repo)).request is first
        connection = await scope.resolve(Connection)
        for chunk in (b"one ", b"two ", b"three"):
            EVENTS.append(f"chunk:{chunk.decode().strip()}")
        assert not connection.closed          # still open while the body streams

    assert connection.closed                  # IC-7: closed after the last chunk
    assert adapter.errors == []               # IC-8: nothing to report

    second = {"path": "/second"}
    async with adapter.unit({REQUEST: second}) as other:
        assert (await other.resolve(Repo)).request is second

    assert await adapter.stop() == ()
    print("reference adapter ok:", EVENTS)


asyncio.run(main())
```

Three details are load-bearing:

- **`unit()` takes a mapping, not `**values`.** `**` forces string keys, so
  `unit(REQUEST=request)` would store the value under `"REQUEST"` while the
  `Token` key never matches — a silent `MissingProviderError` at resolve time.
- **The adapter owns no scope.** `Unit` holds it and hands it to the caller;
  nothing looks a scope up by thread, task or global.
- **`__aexit__` returns `False`.** A teardown error must not replace the
  handler's own exception; the tuple is collected and reported.

The same adapter as a runnable program, with the event trace printed, is
[`examples/host_unit_of_work.py`](https://github.com/lauren-framework/lauren-di/blob/main/examples/host_unit_of_work.py).

## Where the scope lives

There is no ambient scope. The adapter stores the `ResolutionScope` object in
whatever per-unit state the host already has, and passes it to the code that
resolves. Only the attribute name changes:

| Shape | Where the scope lives | Why it is safe |
| --- | --- | --- |
| a per-unit mapping the host owns (WSGI `environ`, ASGI `scope["state"]`) | one key, written once at unit entry | the host creates a fresh mapping per unit |
| a request or context object the handler receives | an attribute on that object | the object's lifetime is the unit's lifetime |
| an explicit argument threaded through the call | a parameter | the type checker can see it |
| a `ContextVar` the adapter sets **and copies itself** | the context variable | only if the host copies the context across its thread hops |

The fourth row is the trap: a `ContextVar` set in an async middleware is not
visible in a sync view the framework runs in a worker thread unless the
framework copies the context. It may be an optimisation, never the only route.

1. Store the scope object, not a token or an id; a lookup table is a second
   cache.
2. Write it once per unit — a second write is a nested scope or a bug.
3. Clear or overwrite it at unit exit so a pooled thread cannot observe the
   previous unit's scope.
4. Never put a scope in a module-level variable, a class attribute or a default
   argument.

## The close point

The close point is a property of the host's **transport**, not of its handler
signature. An adapter that closes when the handler returns is wrong for every
streaming response and for every background task that outlives the response.

| Host shape | Unit | Open the scope | Close the scope | Why that point |
| --- | --- | --- | --- | --- |
| ASGI, pure middleware | request | before `await app(scope, receive, send)` | after that call returns | the app has sent the last body chunk |
| ASGI, `BaseHTTPMiddleware` | request | `dispatch` entry | **not here** | its `finally` runs after the first chunk, before the rest |
| ASGI lifespan | process | lifespan startup | lifespan shutdown | singleton lifetime, not request lifetime |
| WSGI middleware | request | before calling the app | after the response iterable's `close()` | the server calls `close()` after the last byte |
| Django middleware | request | `__call__` before `get_response` | after the response's `close()` | streaming responses outlive `get_response` |
| CLI command | invocation | before the command body | after it, before process exit | one-shot process; no request |
| worker / job | job | before the handler | after the handler | one job, one scope |
| WebSocket | connection | on accept | on close | one connection, not one message |
| background task | task | at task start | at task end | must not borrow a request scope that may already be closed |

Measured on a three-chunk `StreamingResponse` (PRD 10 §5.4, `probe_02_middleware_close.py`):

```text
pure: ['pure-enter', 'chunk-0', 'chunk-1', 'chunk-2', 'pure-finally']
base: ['mw-enter', 'chunk-0', 'mw-got-response', 'mw-finally', 'chunk-1', 'chunk-2']
```

A scope closed in `BaseHTTPMiddleware.dispatch`'s `finally` tears request-scoped
services down after the first chunk; the remaining chunks stream from objects
whose `aclose()` has already run.

## Supplying host objects

A host object — a request, a job context, a connection the framework owns — is
supplied as a **value**, never registered as a provider:

```python fragment
from lauren_di import DIContainer, Token

REQUEST = Token("REQUEST")
container = DIContainer(value_tokens=(REQUEST,))

scope = container.scope(values={REQUEST: host_request})
handler = await scope.resolve(Handler)      # Handler takes Annotated[Any, Inject(REQUEST)]
errors = await scope.close()
```

`values` are read at resolve time, are not providers, and are fixed when the
scope opens. A resolve with no value raises `MissingProviderError`, not a silent
`None`.

Three ownership choices, and only three:

| The object's lifecycle is owned by | Register it with | Hooks run? | Use when |
| --- | --- | --- | --- |
| the host (request, response, framework connection, ORM session) | `register_value` | no | the container must never close it |
| the container, built from scratch | `register` / `use_factory` (generator factory for teardown) | yes, at scope or startup close | the container constructed it and owns its `aclose()` |
| neither — it exists already but the container should run its hooks | `register_instance` | yes, at startup and shutdown | an application-owned pool or client passed in from outside |

Registering a framework-owned object with `register_instance` is the common
mistake: the framework closes it and the container closes it again.
`register_value` is the correct answer for anything the host will close. This is
the only ownership table in the documentation; the provider mechanics behind it
are in [Providers and injection](providers.md).

## The synchronous bridge

A WSGI view, a sync Django view or a CLI command runs on a plain thread with no
running loop, while every container entry point is a coroutine. There are two
patterns, and only two:

| Pattern | Shape | Measured cost | Loop affinity |
| --- | --- | --- | --- |
| one long-lived loop on a dedicated thread (a "bridge") | `run_coroutine_threadsafe(coro, loop).result()` | 0.18–0.22 ms per call | preserved: every call runs on the same loop |
| `asyncio.run()` per unit | a new loop per call | 0.24–0.33 ms per call | **broken**: a resource built on one loop is unusable on the next |

The bridge is the default for a sync host. It is short enough to copy, and it
runs here:

<!-- docs-contract: run -->
```python
import asyncio
import threading

from lauren_di import DIContainer, injectable


class LoopBridge:
    """Run every container call on one long-lived loop, on one thread."""

    def __init__(self) -> None:
        self._loop = asyncio.new_event_loop()
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._run, name="lauren-di-loop", daemon=True)
        self._thread.start()
        self._ready.wait()

    def _run(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._ready.set()
        self._loop.run_forever()

    def call(self, coro: object) -> object:
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result()

    def stop(self) -> None:
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=5)
        self._loop.close()


@injectable()
class Client:
    def __init__(self) -> None:
        self.loop = asyncio.get_running_loop()


bridge = LoopBridge()
container = DIContainer(strict_request_scope=True)
container.register(Client)
bridge.call(container.startup())

first = bridge.call(container.resolve(Client))
second = bridge.call(container.resolve(Client))
assert first is second and first.loop is bridge._loop   # one loop for the process

assert bridge.call(container.shutdown()) == ()
bridge.stop()
print("bridge ok")
```

Four rules follow from the table:

- `asyncio.run()` closes its loop, so a singleton built there keeps a reference
  to a closed loop and unit 2 fails with `RuntimeError: Event loop is closed`.
- The bridge preserves loop affinity — startup, every resolve, every close and
  the final shutdown all run on one loop.
- The bridge serialises container work. That makes the construction race
  impossible, and it means a blocking `__init__` blocks every other thread's
  container call. Keep constructors fast.
- Stop the bridge **after** `shutdown()` and after the last unit's `close()`.
  Stopping it first strands coroutines and makes `shutdown()` unreachable.

Never call `asyncio.run()` from inside a thread that already has a running loop
(a notebook cell, an async test, an ASGI handler): it raises `RuntimeError`, and
the traceback points at the adapter rather than at the call site.

## Startup and shutdown ordering

A host has six phases and exactly two of them call the container's lifecycle
methods:

| Phase | Host hook (any shape) | Container call | On failure |
| --- | --- | --- | --- |
| build | module import, app factory, `AppConfig.ready()` | `DIContainer(...)`, `register*` | declaration error, before anything runs |
| validate | the same hook, after registration | `compile()` | validation error, before binding |
| start | lifespan startup, `ready()`, `main()` | `await startup()` | **fatal**: exit non-zero, do not bind |
| serve | per unit | `scope()`, `scope.resolve(...)` | mapped to the host's error path |
| drain | listener closed, last unit finishing | last `await scope.close()` | reported, never raised |
| stop | lifespan shutdown, `atexit`, `finally` | `await shutdown()` | reported; the exit code is already decided |

Measured against this library (PRD 09 §5.6):

| Observation | Result |
| --- | --- |
| `startup()` twice | one `@post_construct` |
| `shutdown()` twice | one `@pre_destruct`, both calls return `()` |
| `startup()` → `shutdown()` → `startup()` | two `@post_construct` (restart is legal) |
| a `@post_construct` that raises | the exception propagates, the instance **stays cached**, `shutdown()` returns zero errors |

So a failed `startup()` is fatal and `startup()` must run before the host binds
a socket, registers a signal handler or accepts a job. Retrying it on the same
container is not a recovery strategy: the cache is half-built, and `shutdown()`
will not clean it up.

`startup()` also enforces IC-6's singleton clause, which `compile()` does not: a
`SINGLETON` depending on a value token compiles, then fails at `startup()` with
`MissingProviderError`, with that instance cached. A host that skips `startup()`
ships it.

## Concurrency: one scope per task

The container has no lock, no scheduler and no ambient scope; every rule follows
from "one cache, one unit". For a host adapter, one rule is enough:

> **One scope per task, never per thread.** A pooled thread outlives a request.

Store the scope in the host's per-unit state, not in a `threading.local`, and
resolve what a unit needs sequentially rather than with `gather` over two
`scope.resolve` calls. The loop, thread and task model itself is described once
in [Async, concurrency and lifetime](../concurrency.md) — this section only
states the adapter's consequence of it.

## Errors: three channels

Which channel an error takes is a property of **when** it happens, not of its
class:

| When | Channel | Never |
| --- | --- | --- |
| during a unit (resolve) | the host's error path: an exception handler, an error response, a non-zero exit | a silent `None`, or a retry that hides a wiring bug |
| during `scope.close()` | the returned tuple, read and logged by the adapter | raised into a response that is already committed |
| during `startup()` | fatal: log, exit non-zero, do not serve | serving from a half-built cache |
| during `shutdown()` | the returned tuple, logged at exit | changing the exit code after the last unit |
| at declaration or `compile()` | a startup-time crash, before the host binds | a lazy failure on the first request |

`DestructError` is a **value, not a raise**. It subclasses `DIError`, so
`except DIError` catches it if a caller chooses to raise one, but the container
never does. An adapter that wraps `scope.close()` in `try/except` will never see
one; it has to read the tuple.

## Overrides in a host test

An override is a build-time decision, so a host test builds its own container:

```python fragment
from lauren_di import DIContainer, OverridePlan, ProviderOverride

plan = OverridePlan({Repo: ProviderOverride(Repo, "value", value=RecordingRepo())})
container = DIContainer(overrides=plan)
assert container.applied_overrides() == {"Repo": "value"}
```

The full rules — `building_with`, `applied_overrides()`, and why a live
container is never mutated — are in [Testing and overrides](testing.md).
Framework-native override systems are separate and stay separate: FastAPI's
`dependency_overrides` replaces a FastAPI provider, not a container row.

## ASGI hosts

Three rules, each of them measured (PRD 10 §5.4):

1. **Own the scope in pure ASGI.** Wrap `send`; close in the `finally` that runs
   after the final `http.response.body` with `more_body` false. It is the only
   middleware style whose close point is the end of the body.
2. **Never close in `BaseHTTPMiddleware.dispatch`.** If a project already uses
   it, an inner pure-ASGI app owns the scope, not the dispatch method.
3. **Never close in a route handler's `finally`.** FastAPI's `yield` dependency
   is the one correct alternative, because its teardown runs after the body and
   after background tasks.

The shape, with the framework names replaced by the seam:

```python fragment
async def __call__(self, scope, receive, send):
    if scope["type"] not in ("http", "websocket"):
        await self.app(scope, receive, send)
        return
    unit = self.adapter.unit()                    # values fixed below, at open
    scope["state"][SCOPE_KEY] = unit.scope
    try:
        await self.app(scope, receive, send)      # the body is sent inside
    finally:
        self.adapter.report(await unit.scope.close())
```

**Pick one owner per application.** A pure-ASGI middleware and a `yield`
dependency both close correctly, and they disagree about background tasks: the
dependency closes later. Two owners means two scopes or a double close.

`Inject` is not a framework's dependency marker. FastAPI inspects parameter
defaults, does not recognise `Inject`, and either passes the marker through or
refuses to build the route. The adapter resolves from the request's own state
instead. The seam is one sentence: the framework builds what it knows about,
`scope` builds what the container knows about.

## Async frameworks

One row per framework, every cell measured (PRD 11 §5.1):

| Framework | Lifecycle hook | Correct close point | Scope store | Sync handler runs on |
| --- | --- | --- | --- | --- |
| Litestar 2.25.0 | `on_startup` / `on_shutdown` | `ASGIMiddleware` or `after_response` | `scope["state"]` → `request.state` | a portal thread, contextvars copied |
| Quart 0.23.1 | `before_serving` / `after_serving` | pure-ASGI wrapper on `app.asgi_app` | `g` | `asyncio_0` thread |
| Sanic 25.12.1 | `before_server_start` / `after_server_stop` | server-level ASGI wrapper | `request.ctx` | the loop thread |
| aiohttp 3.14.5 | `on_startup` / `on_shutdown` | the handler's write loop, closed in middleware | `request["di"]` | the loop thread |

The universal rule, stated once:

> Open the scope where the host tells you a request began. Close it where the
> body ends — after the last `http.response.body` for ASGI hosts, after
> `write_eof` for aiohttp. Never close it in a hook whose name refers to the
> response object.

**The sync-handler warning.** Three of those four rows run a sync handler on a
thread other than the one that opened the scope. A `ContextVar` or
`threading.local` written on the way in is therefore not visible inside a sync
handler unless the framework copies the context — and one framework does not.
Publish the scope into the framework's own request object and read it there, or
resolve everything the handler needs before it is dispatched.

## WSGI hosts

Bridge first, then one wrapper, then the traps. A WSGI view runs on a worker
thread with no loop, so the bridge from earlier in this page is the starting
point.

One wrapper is enough for every WSGI framework, because the close point is the
response iterable rather than a framework hook:

```python fragment
class DIMiddleware:
    """WSGI wrapper: one scope per request, closed when the body ends."""

    def __init__(self, app, container, bridge):
        self.app = app
        self.container = container
        self.bridge = bridge

    def __call__(self, environ, start_response):
        scope = self.container.scope(values={ENVIRON: environ})
        environ[SCOPE_KEY] = scope

        def body():
            try:
                yield from self.app(environ, start_response)
            finally:
                self.bridge.call(scope.close())

        return body()


app.wsgi_app = DIMiddleware(app.wsgi_app, container, bridge)
```

`close()` runs on exhaustion **and** on abandonment: a client that disconnects
makes the server call `close()` on the iterable, which runs the `finally`. No
framework import appears in the wrapper — it is a WSGI app wrapping a WSGI app.

The four traps are the hooks that look like close points and are not:

| Framework | The hook | What it actually does |
| --- | --- | --- |
| Flask 3.1.3 | `teardown_request` | fires before the first chunk, and a second time at the end |
| Falcon 4.4.0 | `process_response` | fires before the first chunk |
| Pyramid 2.1 | `request.add_finished_callback` | fires before the first chunk |
| Bottle 0.13.4 | `after_request` | fires before the body |

Use them to publish the scope into the framework's request object — `g`,
`req.context`, `request.environ`, `bottle.request.environ` — and never to close
it.

## The conformance suite

"Compatible" is not an opinion; it is twelve observations, each of which fails
loudly when an adapter skips its obligation:

| # | Invariant | The observation that fails it |
| --- | --- | --- |
| C-1 | the container is started before the host accepts work | the first unit's resolve constructs a singleton |
| C-2 | strict mode is on | an unscoped `REQUEST` resolve returns an instance instead of raising |
| C-3 | one scope per unit, opened after the host bound its state | two units share a request-scoped instance |
| C-4 | the scope lives in the host's per-unit state | two concurrent units resolve the same scope object |
| C-5 | every resolution goes through the scope | `container.resolve` during a unit builds a second instance |
| C-6 | host objects are values, not generator providers | the framework closes an object the container also closes |
| C-7 | the scope closes after the last body chunk or command output | a `@pre_destruct` runs before the client reads the last chunk |
| C-8 | teardown errors are reported, not raised | a failing `aclose()` turns a 200 into a 500 |
| C-9 | `shutdown()` runs once and its tuple is read | a `@pre_destruct` count of zero at exit |
| C-10 | a sync host uses one loop, or loop-agnostic providers | a loop-bound singleton raises `Event loop is closed` on unit 2 |
| C-11 | overrides are applied at build | a per-test double is visible to another test's container |
| C-12 | the adapter lives outside the library | `lauren_di.__all__` gains a name, or the package imports the host |

Two rules make the suite usable across the fifteen apps on this branch:

- **An absent host is a SKIP, never a PASS.** A run without the package reports
  the same tests as skipped, naming the `requirements.txt` that provides it, so
  a green suite cannot mean "the framework was not installed".
- **The suite is parametrised by adapter, not by framework.** Every framework
  answers the same twelve questions, so one that cannot satisfy an invariant
  fails the same assertion as its sibling.

## Where the code is

| What | Where |
| --- | --- |
| the reference adapter as a program | [`examples/host_unit_of_work.py`](https://github.com/lauren-framework/lauren-di/blob/main/examples/host_unit_of_work.py) |
| fifteen framework adapters over four seams | [`examples/apps/`](https://github.com/lauren-framework/lauren-di/blob/main/examples/apps/README.md) |
| the C-1…C-12 suite, parametrised per adapter | `tests/integration/hosts/` |
| what each app's tests prove | the app's own `tests/` directory |

If you are writing an adapter for a host this branch does not cover, copy the
reference adapter, keep the twelve obligations, and prove your close point with
the three-chunk body from this page: a `@pre_destruct` that runs before the last
chunk is the one failure that no integration test catches by accident.
