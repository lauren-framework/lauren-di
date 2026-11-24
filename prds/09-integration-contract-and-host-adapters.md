# PRD 09 — Host Integration Contract & Adapters

**Status:** Draft — 2026-11-24
**Depends on:** PRDs 01–08
**Resolves:** D-57 (no framework code ships), D-58 (explicit scope, no ambient ContextVar), D-59 (a sync host owns its bridge), D-60 (`startup()` before serving), D-61 (the host owns the scope lifetime), D-62 (SKIP is never PASS), D-63 (framework imports only in partial fences); introduces the adapter contract `IC-1`…`IC-12`
**Related:** `prds/README.md` §3 (product boundary), §4 (decision register)
**Probes:** `/tmp/compat/core_probe.py`, `core_probe2.py`, `bridge_probe.py`, `probe09.py`, `probe09b.py`, `probe09c.py` on Python 3.13.13; indexed in §6.5

---

## 1. Problem Statement

`lauren-di` is a container, not a runtime. It knows how to build a graph, cache a
singleton, cache a request-scoped instance in a dict the caller owns, and tear both down.
It does not know when a request starts, which thread or event loop it runs on, what "the
response is finished" means, or where a host keeps per-request state.

Every framework integration therefore answers the same six questions, and each framework
answers them differently. That is expected. What is not acceptable is answering them
*implicitly*: a scope opened in the wrong place, a teardown that runs before the body is
streamed, a singleton constructed twice because two threads arrived at once, or a
`shutdown()` that reports success after closing a resource on the wrong loop. Those are
silent failures, and the library's own design rule is that a silent failure is a bug
(PRD 05 §5.7).

This PRD fixes the answers once. The host pages that follow apply them to the frameworks; they do
not re-decide them.

### 1.1 A container is not a runtime

| Concern | Owner | Why not the container |
|---|---|---|
| When a unit of work begins and ends | the host | only the host knows what a request, a job, a command or a WebSocket connection is |
| Which thread or event loop runs the work | the host | the container has no scheduler and no loop (D-4, D-7) |
| What "the response is finished" means | the host | headers, body, streaming and background tasks differ per framework |
| Where per-request state lives | the host | the container accepts an opaque `values` mapping and never inspects it (PRD 01 §5.3.3) |
| Which objects it owns and may destroy | the caller | `register_value` vs `register_instance` vs a factory provider is the caller's choice (§5.4) |
| The dependency graph, caches, scope rules, teardown order | the container | PRDs 02, 03, 04 |

The boundary is not a limitation to work around; it is the reason the container can be
used by a framework without importing it (D-57).

### 1.2 What the library already gives an embedder

| Seam | Signature | What it provides | What it does not provide |
|---|---|---|---|
| Scope factory | `container.scope(*, values=None) -> ResolutionScope` | a per-unit cache and a place for host values | a lifetime; the caller must open and close it |
| Scope resolve | `await scope.resolve(token, *, owning_module=None) -> Any` | request-scoped identity inside the unit | concurrency control (M-3) |
| Scope close | `await scope.close() -> tuple[DestructError, ...]` | reverse-creation teardown, idempotent, never raises (M-9) | a hook into the host's "finished" signal |
| Host values | `DIContainer(value_tokens=…)` + `scope(values=…)` | out-of-band objects without a provider | late binding: values are fixed when the scope opens (M-12) |
| Process lifecycle | `await startup()`, `await shutdown() -> tuple[DestructError, ...]` | singleton construction and teardown, idempotent and restartable (M-16) | a process signal handler |
| Strict mode | `DIContainer(strict_request_scope=True)` | a loud failure instead of a silent leak (M-11) | a scope; it only refuses the unscoped path |
| Overrides | `DIContainer(overrides=plan)`, `building_with(plan)` | build-time substitution (PRD 07 §5.5) | per-request substitution |
| Ownership choice | `register_value` / `register_instance` / `adopt_lifecycle` | three answers to "who tears this down" (M-15) | automatic detection of the right one |

Two absences are deliberate and are decisions, not gaps: there is no ambient
`current_scope()` (D-58) and no event-loop bridge (D-59). §5.3 and §5.5 state what to do
instead.

### 1.3 Twenty-one measured gaps between the seams and a host

Every number below was produced by the probe named in §6.5 on the artifact printed as the
first line of its transcript (`lauren_di.__file__` under
`/root/python_projects/lauren-all/lauren-di`). None is quoted from a framework's
documentation.

| # | Gap | Measurement | Consequence |
|---|---|---|---|
| M-1 | Lazy singleton construction is not serialised | eight threads race the first `resolve` of one `time.sleep(0.05)` singleton → **8 distinct instances**, the container's cache holds **1** | seven instances leak, never torn down; a threaded host must construct eagerly (M-2, D-60) |
| M-2 | Eager construction removes the race | same probe after `await container.startup()` → **1 instance**, cache holds **1** | `startup()` before the host binds is the supported multithreaded contract |
| M-3 | Concurrent resolution inside one scope can duplicate a request-scoped instance | `asyncio.gather(scope.resolve(T), scope.resolve(T))` where `T`'s factory awaits → `a is b` is **False** and the cache ends with **2** entries; without an `await` in construction → `a is b` is **True** | a handler that parallelises resolution must resolve first or use one scope per task |
| M-4 | `asyncio.run` per unit is a tax and breaks loop affinity | 200 cached resolves × 5 runs → **0.24–0.33 ms/op** (mean ≈0.30) | a loop-bound singleton built on one temporary loop is unusable on the next (M-6) |
| M-5 | One long-lived loop on a dedicated thread is cheaper and keeps affinity | 1000 calls × 5 runs → **0.18–0.33 ms/op** (0.18–0.22 idle, 0.32–0.33 with five processes competing; mean ≈0.25); 8 threads × 50 scoped resolves → no errors, one singleton id | the bridge is the sync-host default (D-59) |
| M-6 | A loop-bound resource does not cross loops, and the failure can be silent | an `aiohttp.ClientSession` built on loop A used on loop B → `RuntimeError: Event loop is closed`; `shutdown()` on loop B returned **no errors** | teardown "success" is not proof the resource was usable; keep build, use and close on one loop |
| M-7 | A synchronous constructor blocks every container call on the bridge loop | a 0.2 s `__init__` delayed an unrelated cached resolve by **0.20 s** | constructors must not do I/O; do that work in `@post_construct` on a resource the host owns, or accept the serialisation |
| M-8 | Teardown order is reverse creation order, not framework order | `B` depends on `A`; both request-scoped → `["B", "A"]` | adapters must not assume the host's teardown order |
| M-9 | Teardown failures are returned, not raised | a failing `aclose()` → `(DestructError,)`, `scope.close()` does not raise, `DestructError` is a `DIError` | the adapter must read and report; the response is already committed |
| M-10 | A handler exception still closes the scope | raise inside `async with container.scope()` → the scope is closed | `finally`/`async with` is sufficient for the happy and error paths |
| M-11 | The unscoped warning is once per provider | three unscoped `REQUEST` resolutions → **one** warning | a warning is not a guarantee; use strict mode |
| M-12 | Values are bound when the scope opens | `scope(values={REQUEST: obj})` → `scope.resolve(REQUEST)` returns `obj`; `scope.values` is a copy | a host cannot add a value after the request is bound |
| M-13 | A value token is not a provider | `container.resolve(REQUEST)` with no values → `MissingProviderError` | declare the token, supply the value, or both fail loudly |
| M-14 | A scope is single-use | resolve after `close()` → `LifecycleViolationError` | a host that caches scopes across requests is broken, not slow |
| M-15 | Ownership is explicit | `register_value` runs no hooks; `register_instance`/`adopt_lifecycle` run them at startup/shutdown | registering a framework-owned object as an instance makes the container close it twice |
| M-16 | Startup and shutdown are idempotent, restartable, and half-built on failure | `startup()` twice → 1 `@post_construct`; `shutdown()` twice → 1 `@pre_destruct`; restart → 2; a raising `@post_construct` propagates and **leaves the instance cached**, `shutdown()` then returns 0 errors | a host must treat a failed `startup()` as fatal and exit; retrying on a half-built cache hides the failure |
| M-17 | Per-request cost is dominated by constructor introspection, not by the scope | scope open+close **1.1 µs**; open+resolve+close **0.27 ms** warm on an idle box (**0.33–0.39 ms** with the machine loaded; 0.50 ms first pass); profile: two `inspect.signature` calls per construction, 0.55 s of 0.65 s under the profiler | budget ~0.25 ms per request-scoped class; cache signatures if a host constructs many |
| M-18 | The ASGI close point depends on the middleware style | `BaseHTTPMiddleware` with a 3-chunk streaming body → `mw-enter, chunk-0, mw-got-response, mw-finally, chunk-1, chunk-2`; pure ASGI → `pure-enter, chunk-0, chunk-1, chunk-2, pure-finally` | a scope closed in `BaseHTTPMiddleware` is closed **before the body finishes**; use pure ASGI for scope ownership |
| M-19 | `container.resolve` inside an open scope bypasses the scope's cache | scope-resolved `a`, then `container.resolve(R)` → `a is b` is **False**, one cache entry, one warning | IC-5: every resolution in a unit goes through the scope |
| M-20 | `scope()` works before `compile()` | `scope()` then `scope.resolve` on an uncompiled container → resolves (lazy compile) | a host may open a scope in a request path without an explicit `compile()`; still compile at startup to fail fast |
| M-21 | A singleton that injects a declared value token compiles and then fails at `startup()` | `compile()` accepts `Repo(request: Annotated[Any, Inject(REQUEST)])` registered as a `SINGLETON`; `startup()` raises `MissingProviderError: No provider for Token(REQUEST)`, the cache then holds **1**, and `shutdown()` returns `()` | IC-6's singleton clause is enforced at construction, not by validation: never let a `SINGLETON` depend on a value token |

### 1.4 What "compatible" means here

An adapter is compatible with `lauren-di` when the conformance suite in §5.10 passes for
it. Operationally, that means all six of the following are observable, not merely
intended:

1. **One scope per unit of work.** A request, job, command or connection has exactly one
   `ResolutionScope`, opened after the host has bound its own state.
2. **Identity inside the unit.** Two resolutions of a `REQUEST` provider in one unit
   return the same object (sequentially; see M-3 for the concurrent case).
3. **Isolation between units.** Two concurrent units never see each other's
   request-scoped instances, even when the host runs them on the same thread pool.
4. **Teardown exactly once, after the host is finished.** The scope closes after the last
   body chunk or command output, and `@pre_destruct`/`aclose()` run in reverse creation
   order (M-8, M-18).
5. **Errors are mapped, not swallowed.** Resolution errors reach the host's error
   channel; teardown errors are returned and reported; a failed startup is fatal (M-16).
6. **No library change.** The adapter adds no dependency to `lauren-di`, imports the
   host package itself, and leaves `lauren_di.__all__` at 38 names (D-57).

"Works on the happy path" is not the definition. The three cases that most often fail are
a streaming response, a teardown hook that raises, and a second thread.

### 1.5 Evidence method

All probes ran on Python 3.13.13 with `PYTHONPATH` pinned to this checkout, and each
transcript starts by printing `lauren_di.__file__`. The pin matters for the same reason it
mattered in PRD 08 §1.5: an installed `lauren-di` is a different artifact, and a probe run
from a directory that is not on `sys.path` measures it in silence. Framework versions are
recorded in §6.5 and in each page 10–14.

This page contains no framework import: it is the contract, and it is deliberately
neutral. The compatibility pages 10–14 are where framework nouns appear, and there D-63
requires every framework sample to sit in a `fragment`/`skip` fence so that the `run`
fences — which execute in an environment with no framework installed — never promise
something the corpus cannot keep.

Two things are **not** measured here and are labelled as design rules rather than
findings: the ordering of a host's own teardown hook relative to `scope.close()` (each
page 10–14 measures its host), and the behaviour of a framework that is not in §6.5.

---

## 2. Goals

| # | Goal | Acceptance |
|---|---|---|
| G1 | One adapter contract, stated once, testable per framework | §5.1's `IC-1`…`IC-12` each name the conformance check that fails when it is skipped |
| G2 | The close point is defined per host shape, not per framework | §5.2's table covers ASGI pure/`BaseHTTPMiddleware`, WSGI, Django, CLI, worker, WebSocket and background task, with the measured ASGI proof |
| G3 | Host values have one route and one ownership rule | §5.4's three-way ownership table and the singleton/value-token rule |
| G4 | A sync host can be correct without a library change | §5.5's bridge is runnable from this page, measured, and its failure mode (M-6) is demonstrated |
| G5 | Startup/shutdown ordering is explicit, including the failure path | §5.6's phase table and M-16's half-built rule |
| G6 | Concurrency limits are stated, not discovered in production | §5.7 reproduces M-1, M-3 and M-7 and states the permitted shapes |
| G7 | Every `DIError` leaf has a host-visible outcome and no new leaf is added | §7's mapping table covers all 13 leaves |
| G8 | Overrides are usable from a host test without mutating a live container | §5.9's three patterns, each mapped to a `T09` row |
| G9 | "Compatible" is a suite, and an absent framework is SKIP | §5.10's `C-1`…`C-12`; D-62 |
| G10 | Nothing here asks the library to learn a framework | §6.2's forbidden list and the 38-name assertion in `T09-18` |

---

## 3. Non-Goals

- **Framework-specific code in `lauren-di`.** No `lauren_di.integrations` module, no
  optional extra, no lazy `import flask` anywhere in the package (D-57). A packaged
  adapter, if one ever exists, is a separate distribution that depends on both projects.
- **An ambient scope.** No `current_scope()` ContextVar, no thread-local, no
  `contextvars` in the library (D-58). §5.3 states what replaces it.
- **An event-loop bridge in the library.** No `run_sync()`, no background loop thread
  (D-59). §5.5 gives the reference implementation the host copies.
- **A fix for the construction race.** v1 does not add a lock (D-60); it states the
  supported contract (`startup()` before serving) and the unsupported one (lazy first
  touch from worker threads).
- **HTTP vocabulary.** No request, response, header, route, status code or ASGI scope in
  the library's API or docstrings (PRD 01 §9).
- **Framework features.** Routing, authentication, ORM sessions, CLI parsing, template
  rendering and configuration loading are the host's; this set only says where the
  container sits.
- **Per-request overrides.** Overrides are applied when the container is built (PRD 07
  §5.5); a host test that needs a different double builds a different container.
- **Automatic discovery of host objects.** The adapter names the tokens it declares; the
  container never scans a request for attributes.
- **Claims about frameworks not measured.** A framework absent from §6.5 is unmeasured,
  and an unmeasured adapter is SKIP, never PASS (D-62).
- **Committing a test suite in this PRD.** §10 specifies the tests; the pages 10–14 and
  the follow-up goal own their execution.

---

## 4. Prior Art

Every DI library that is used by a web framework has had to answer "what is the lifetime
of a request". The answers cluster into three families, and the cluster explains the
choices in §5.

### 4.1 Scope-per-request in other DI libraries

| Library / framework | Unit of scope | How the scope is carried | Teardown | What is borrowed | What is rejected |
|---|---|---|---|---|---|
| FastAPI `Depends` | request | `request.scope["app"].dependency_overrides` + a per-request cache | `yield` dependencies close after the response | the per-request cache and the "one unit, one cache" rule | the dependency graph lives in the route signature; `lauren-di` has no route layer |
| `dependency-injector` | request / resource | explicit `Resource`/`Singleton` providers wired into a container | provider `.shutdown()` | explicit provider ownership and reverse-order teardown | the provider DSL and the `containers` code generator |
| `injector` | request | `@inject` + `Scope` decorators, a thread-local request scope | scope decorator exit | the `Scope` vocabulary (PRD 03 already uses it) | thread-local ambient scope (D-58) |
| `flask-injector` | request | `injector.get` inside a Flask app | Flask `teardown_appcontext` | the framework hook as the close point | a module-level injector singleton |
| `django-injector` | request | a `Request`-bound injector, middleware | middleware exit | middleware as the scope owner | implicit binding to Django's `Request` type |
| .NET `IServiceScope` | request | `CreateScope()` / `Dispose()` | `Dispose()` at the end of the unit | explicit scope object with `Dispose` | ambient `HttpContext.RequestServices` |
| NestJS request-scoped providers | request | per-request DI sub-tree | framework-managed | "request-scoped" as a declared lifetime | the module graph (PRD 06 keeps it out) |
| Spring `request` scope | request | thread-bound proxy | framework-managed | the lifetime name | thread-bound implicit lookup |

The shape that fits `lauren-di` is the explicit scope object: `dependency-injector`'s
ownership rule, .NET's `IServiceScope`, and FastAPI's per-request cache, without any of
the three's ambient lookup.

### 4.2 What is borrowed, what is rejected

**Borrowed:** one cache per unit of work; an explicit object that owns it; reverse-order
teardown; overrides applied at build time; a strict mode that turns a missing scope into
an error; a conformance suite rather than per-framework folklore.

**Rejected:** an ambient lookup (`current_scope()`, thread-local, `Request` special case);
a provider DSL; a framework-specific module inside the DI package; a scheduler; automatic
request-object discovery. Each rejection is a decision with an id (D-57…D-63) or a
non-goal above, so a later patch cannot reintroduce it as a convenience.

---

## 5. Proposed API

This section is the contract. It names no framework type; the host pages map each rule to a
framework's hook.

### 5.1 The adapter contract

| # | Obligation | Why | Checked by |
|---|---|---|---|
| IC-1 | Build one `DIContainer` per process (or per app instance), register every provider, and `await container.startup()` before the host accepts work | M-1: lazy first touch from two threads constructs twice; M-2: eager construction is the fix | C-1, T09-01 |
| IC-2 | Construct with `strict_request_scope=True` unless the host has no `REQUEST`-scoped provider, and say which | M-11: a warning is emitted once and the instance is never torn down | C-2, T09-02 |
| IC-3 | Open exactly one `ResolutionScope` per unit of work, after the host has bound its own state | M-12: values are fixed at open; a scope opened too early cannot see the request | C-3, T09-03 |
| IC-4 | Store the scope object in the host's own per-unit state and pass it explicitly | D-58: no ambient scope; a module global is overwritten by the next unit | C-4, T09-04 |
| IC-5 | Resolve through `scope.resolve`, never `container.resolve`, while a unit is open | M-19: `container.resolve` bypasses the scope cache and creates a second instance | C-5, T09-05 |
| IC-6 | Declare host objects with `value_tokens=` and supply them through `values=`; never register a framework-owned object as a generator provider, and never let a `SINGLETON` depend on a value token | M-13, M-15; a singleton would capture the first request's object | C-6, T09-06 |
| IC-7 | Close the scope exactly once, in a `finally`, at the host's "finished" point — after the last body chunk or command output | M-18: "after the handler returns" is early for streaming; M-9: close is idempotent | C-7, T09-07 |
| IC-8 | Read `scope.errors` and report them through the host's log/metric/error channel; never raise a teardown error into a response | M-9: teardown returns values; the response is already committed | C-8, T09-08 |
| IC-9 | Call `await container.shutdown()` once at process stop and report its tuple; treat a failed `startup()` as fatal and exit | M-16: a failed `@post_construct` leaves the instance cached | C-9, T09-09 |
| IC-10 | In a sync host, run every container call on one long-lived loop; if using `asyncio.run` per unit, restrict providers to loop-agnostic ones | M-4/M-5/M-6 | C-10, T09-10 |
| IC-11 | Apply overrides when the container is built (`DIContainer(overrides=…)` or `building_with(…)`); never mutate a live container | PRD 07 §5.5 and §5.6 | C-11, T09-11 |
| IC-12 | Keep the adapter outside `lauren-di`: it imports the host, the library never imports the host, and absence of the host is the adapter's `ImportError` | D-57; `dependencies = []` and `tests/packaging/test_no_framework_imports.py` | C-12, T09-12 |

Two obligations are easy to miss and are called out separately:

- **IC-6's singleton clause.** `compile()` skips declared value tokens (PRD 08 §5.4), so a
  `SINGLETON` that depends on `Request` passes validation and then captures the first
  request's object forever. No error is raised. The rule is the only defence.
- **IC-7's "finished" clause.** A framework's "response returned" hook is not the same as
  "body sent" (M-18). §5.2 names the correct point per host shape.

### 5.2 Scope ownership and the close point

| Host shape | Unit | Open the scope | Close the scope | Why that point |
|---|---|---|---|---|
| ASGI, pure middleware | request | before `await app(scope, receive, send)` | after that call returns | measured: the app has sent the last body chunk (M-18) |
| ASGI, `BaseHTTPMiddleware` | request | `dispatch` entry | **not here** | measured: `finally` runs after the first chunk, before the rest (M-18) |
| ASGI lifespan | process | lifespan startup | lifespan shutdown | singleton lifetime, not request lifetime |
| WSGI middleware | request | before calling the app | after the response iterable's `close()` | the WSGI server calls `close()` after the last byte |
| Django middleware | request | `__call__` before `get_response` | after the response's `close()` (or `request_finished`) | streaming responses outlive `get_response` |
| CLI command | invocation | before the command body | after the command body, before process exit | one-shot process; no request |
| Worker / job | job | before the handler | after the handler | one job, one scope |
| WebSocket | connection | on accept | on close | one connection, not one message |
| Background task | task | at task start | at task end | must not borrow a request scope that may already be closed |

The measured ASGI proof, from `probe09.py` (`starlette 1.10.0`, a three-chunk
`StreamingResponse`):

```text
base: ['mw-enter', 'chunk-0', 'mw-got-response', 'mw-finally', 'chunk-1', 'chunk-2']
pure: ['pure-enter', 'chunk-0', 'chunk-1', 'chunk-2', 'pure-finally']
```

A scope closed in `BaseHTTPMiddleware.dispatch`'s `finally` tears down request-scoped
services after the first chunk. The body then streams from objects whose `aclose()` has
already run. The pure middleware closes after the last chunk, which is the only correct
point. The ASGI page applies this to Starlette and FastAPI; the rule is framework-neutral.

The close point is a property of the host's **transport**, not of its handler signature.
An adapter that closes when the handler returns is wrong for every streaming response and
for every background task that outlives the response.

### 5.3 Carrying the scope through a host

There is no ambient scope (D-58). The adapter stores the `ResolutionScope` object in
whatever per-unit state the host already has, and passes it to the code that resolves.
The shapes are the same everywhere; only the attribute name changes.

| Shape | Where the scope lives | Why it is safe | Failure it prevents |
|---|---|---|---|
| A per-request mapping owned by the host (WSGI `environ`, ASGI `scope["state"]`, `request.state`) | one key, written once at unit entry | the host creates a fresh mapping per unit | a module global overwritten by the next request |
| A request/context object the handler receives | an attribute on that object | the object's lifetime is the unit's lifetime | a closure that captures a stale scope |
| An explicit argument threaded through the call | a parameter | the type checker can see it | a hidden dependency on import order |
| A `ContextVar` **the adapter sets and copies itself** | `ContextVar` | only if the host copies the context across its thread hops | a value that silently disappears in a worker thread |

The fourth row is the trap. A `ContextVar` set in an async middleware is **not** visible
in a sync view that the framework runs in a thread pool unless the framework copies the
context (Django's `sync_to_async` is the case measured in PRD 13). An adapter may use a
`ContextVar` as an optimisation, but it must still pass the scope explicitly at the
boundary where the host changes threads.

Rules:

1. Store the scope object, not a token or an id; a lookup table is a second cache.
2. Write it once per unit. A second write is either a nested scope (IC-3 violation) or a
   bug.
3. Clear or overwrite it at unit exit so a pooled thread cannot observe the previous
   unit's scope.
4. Never put a scope in a module-level variable, a class attribute or a default argument.

### 5.4 Supplying host objects

A host object (a request, a job context, a connection the framework owns) is supplied as a
**value**, not registered as a provider:

```python fragment
from lauren_di import DIContainer, Token

REQUEST = Token("REQUEST")                 # a type, a Token or a string all work as a key
container = DIContainer(value_tokens=(REQUEST,))

scope = container.scope(values={REQUEST: host_request})
handler = await scope.resolve(Handler)     # Handler may take `request: Annotated[Any, Inject(REQUEST)]`
errors = await scope.close()
```

`values` are read at resolve time, are not providers, and are fixed when the scope opens
(M-12). A resolve with no value raises `MissingProviderError` (M-13), not a silent `None`.

Three ownership choices, and only three:

| The object's lifecycle is owned by | Register it with | Hooks run? | Use when |
|---|---|---|---|
| the host (request, response, framework connection, ORM session) | `register_value` (or a factory that returns it) | no | the container must never close it |
| the container, built from scratch | `register_class` / `register_factory` (a generator factory for teardown) | yes, at scope/startup close | the container constructed it and owns its `aclose()` |
| neither — it exists already but the container should run its hooks | `register_instance` / `adopt_lifecycle` | yes, at startup/shutdown | an application-owned pool or client passed in from outside |

Registering a framework-owned object with `register_instance` is the common mistake: the
framework closes it and the container closes it again. `register_value` is the correct
answer for anything the host will close (M-15).

### 5.5 The synchronous bridge

A sync host (WSGI, Django in sync mode, a CLI) has no event loop, but every container
entry point is a coroutine. There are two patterns, and only two.

| Pattern | Shape | Measured cost | Loop affinity | Thread safety |
|---|---|---|---|---|
| One long-lived loop on a dedicated thread ("bridge") | `run_coroutine_threadsafe(coro, loop).result()` | **0.18–0.22 ms/call** (mean ≈0.20) | preserved: every container call runs on the same loop | container work is serialised on one thread; M-1's race cannot occur |
| `asyncio.run()` per unit | a new loop per call | **0.24–0.33 ms/call** (mean ≈0.30) | **broken**: a resource built on one loop is unusable on the next (M-6) | each thread has its own loop; the container's caches are still shared |

The bridge is the default for a sync host (D-59). The reference implementation is short
enough to copy, and it is executed as part of this page's test plan:

```python run
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

    def call(self, coro):
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result()

    def stop(self) -> None:
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=5)
        self._loop.close()


@injectable()
class Repo:
    pass


@injectable()
class Service:
    def __init__(self, repo: Repo):
        self.repo = repo


bridge = LoopBridge()
container = DIContainer(strict_request_scope=True)
container.register(Repo)
container.register(Service)
bridge.call(container.startup())

scope = container.scope()                       # one scope, opened by the host
service = bridge.call(scope.resolve(Service))   # resolved through the scope
assert isinstance(service.repo, Repo)
bridge.call(scope.close())
assert scope.closed and scope.errors == ()

bridge.call(container.shutdown())
bridge.stop()
print("bridge adapter ok")
```

Why the bridge is correct and `asyncio.run` is not, for a resource-owning provider:

- `asyncio.run` creates a loop, runs the coroutine, and **closes the loop**. A singleton
  built there (an async HTTP client, an async DB pool, an `asyncio.Lock`) keeps a
  reference to a closed loop; the next unit's loop cannot use it (M-6).
- The bridge keeps one loop alive for the process. Startup, every unit's resolve, every
  scope close and the final shutdown all run on it, so a loop-bound singleton is built,
  used and closed on the same loop.
- The bridge serialises container work. That is a feature for construction (M-1 becomes
  impossible) and a cost for a blocking constructor (M-7): a 0.2 s `__init__` blocks
  every other thread's container call for 0.2 s. Keep constructors fast; do I/O in a
  `@post_construct` that the host schedules, or accept the serialisation.
- The bridge must stop **after** `shutdown()` and after the last unit's `close()`. Stopping
  it first strands coroutines and makes `shutdown()` unreachable.

Two rules that are not optional:

1. Never create a scope on one loop and close it on another. If the host owns the loop
   (ASGI, Sanic, aiohttp), everything is already on one loop; if it does not, use the
   bridge.
2. Never call `asyncio.run()` from inside a thread that already has a running loop (a
   Jupyter cell, an async test, an ASGI handler). It raises `RuntimeError` and the
   traceback points at the adapter, not at the call site that made the loop.

### 5.6 Startup and shutdown ordering

A host has six phases and exactly two of them call the container's lifecycle methods. The
table is the whole rule; the numbers under it are what makes it non-negotiable.

| Phase | Host hook (any shape) | Container call | On failure |
|---|---|---|---|
| build | module import, app factory, `AppConfig.ready()` | `DIContainer(...)`, `register*` | declaration error, before anything runs (PRD 02 §5.5) |
| validate | the same hook, after registration | `compile()` | validation error, before binding (PRD 05 §5.6) |
| start | lifespan startup / `ready()` / `main()` | `await startup()` | **fatal**: exit non-zero, do not bind |
| serve | per unit | `scope()`, `scope.resolve(...)` | mapped, §7 |
| drain | listener closed, last unit finishing | last `await scope.close()` | reported, never raised (IC-8) |
| stop | lifespan shutdown / `atexit` / `finally` | `await shutdown()` | reported; exit code already decided |

Measured, from `probe09.py`:

| Observation | Result |
|---|---|
| `startup()` twice | **1** `@post_construct` |
| `shutdown()` twice | **1** `@pre_destruct`, both calls return `()` |
| `startup()` → `shutdown()` → `startup()` | **2** `@post_construct` (restartable, D-22) |
| a `@post_construct` that raises | `RuntimeError` propagates, **instance stays cached**, `shutdown()` then returns **0** errors |
| `scope()` on an uncompiled container | resolves (lazy compile, M-20) |
| `container.resolve(T, request_cache={})` | caches in that dict, but there is **no scope object to close** |

Two consequences that a host must encode, not remember:

1. **A failed `startup()` is fatal.** The cache is half-built (M-16) and `shutdown()` will
   not clean it up: it reports zero errors because the instance never finished
   construction. Retrying `startup()` on the same container is not a recovery strategy;
   exit, let the supervisor restart the process, and let the next process build from
   scratch. `startup()` must therefore run **before** the host binds a socket, registers
   a signal handler, or accepts a job.
2. **Restart is legal but not free.** A second `startup()` re-fires every
   `@post_construct`, so it is only correct after a completed `shutdown()`. A host that
   hot-reloads a module (Django's autoreload, uvicorn's `--reload`, a notebook cell) sees
   the hook run again in the same process; the count is the evidence that it did.

The eager-construction rule (IC-1, D-60) is the reason `startup()` is not optional in a
threaded host: eight threads racing the first lazy resolve of one singleton produce eight
instances and keep one (M-1), and the seven losers are never torn down because nothing
holds a reference to them.

`startup()` also enforces IC-6's singleton clause, which validation does not:

| Step | Result (M-21, `probe09f.py`) |
|---|---|
| register `Repo(request: Annotated[Any, Inject(REQUEST)])` as a `SINGLETON`, then `compile()` | accepted |
| `await startup()` | `MissingProviderError: No provider for Token(REQUEST)` |
| cache after the failure | **1** instance |
| `await shutdown()` | `()` — the failure is not re-reported |

So a value-token edge into a singleton is caught at start, not at compile, and a host
that skips `startup()` (or ignores its exception) ships it to production.

### 5.7 Concurrency limits

The container has no lock, no scheduler and no ambient scope. What it does have is a
per-scope cache, and every concurrency rule follows from "one cache, one unit".

| Shape | Loop | Scope sharing | Permitted? |
|---|---|---|---|
| Async host (ASGI, aiohttp, Sanic) | one loop, many tasks | one scope per task/request | **yes** |
| Sync host through the bridge (D-59) | one long-lived loop, one thread | one scope per request, every call submitted to the bridge | **yes** — the default |
| Sync host with `asyncio.run` per unit | a fresh loop per unit | one scope per unit | only if every provider is loop-agnostic (M-6) |
| Worker pool, one scope per job | any | never shared between jobs | **yes** |
| One scope shared by two concurrent tasks | any | shared | **never** (M-3) |
| `container.resolve` while a scope is open | any | bypasses the scope cache | **never** (M-19) |

Measured:

| Observation | Result |
|---|---|
| 8 threads race the first lazy resolve of one singleton | **8** distinct instances, **1** kept (M-1) |
| the same race after `startup()` | **1** instance (M-2) |
| `asyncio.gather(scope.resolve(T), scope.resolve(T))`, `T`'s factory awaits | `a is b` **False**, cache ends with **2** entries (M-3) |
| the same gather with no `await` in construction | `a is b` **True** |
| `container.resolve(R)` inside an open scope that already resolved `R` | `a is b` **False**, one cache entry, one warning (M-19) |
| 8 threads × 50 scoped resolves through the bridge | no errors, **1** singleton id (M-5) |
| a 0.2 s synchronous constructor on the bridge | an unrelated cached resolve waited **0.20 s** (M-7) |

Four rules, each of which a host adapter must be able to state:

1. **Resolve once, then share the object.** If a handler needs two services
   concurrently, resolve both sequentially first. `gather` over two `scope.resolve` calls
   is the one pattern that silently duplicates a request-scoped instance (M-3) — and the
   duplicate is cached, so the leak is invisible until teardown.
2. **One scope per task, never per thread.** A pooled thread outlives a request; a scope
   does not (M-14). Store the scope in the host's per-request state, not in a
   `threading.local`.
3. **Constructors are not for I/O.** The bridge serialises container work, so a blocking
   constructor blocks every other thread's container call (M-7). Do I/O in
   `@post_construct` on a resource the host schedules, or accept the serialisation.
4. **Strict mode is the only guarantee.** The unscoped warning fires once per provider
   (M-11); `strict_request_scope=True` raises
   `DIScopeViolationError(detail={"token", "reason": "no_active_scope"})` on every
   offence. Every adapter in this set constructs the container with it on (IC-2).

### 5.8 Error mapping

Every error the container produces reaches the host through exactly one of three
channels. Which channel is a property of **when** the error happens, not of its class.

| When | Channel | Never |
|---|---|---|
| during a unit (resolve) | the host's error path: an exception handler, an error response, a non-zero exit | a silent `None`; a retry that hides a wiring bug |
| during `scope.close()` | returned tuple, read and logged by the adapter (IC-8) | raised into a response that is already committed |
| during `startup()` | fatal: log, exit non-zero, do not serve (IC-9) | serving from a half-built cache |
| during `shutdown()` | returned tuple, logged at exit; the exit code is already decided | changing the exit code after the last unit |
| at declaration or `compile()` | a startup-time crash, before the host binds | a lazy failure on the first request |

The leaf-by-leaf table is §7. Two rules keep it honest:

- **`DestructError` is a value, not a raise.** It subclasses `DIError` so `except DIError`
  catches it if a caller chooses to raise one, but the container never does (D-35). An
  adapter that wraps `scope.close()` in `try/except` will never see one; it must read the
  tuple.
- **No new leaf.** The host pages map the thirteen existing leaves and add none. A
  framework-specific exception in this library would be an HTTP concern wearing a DI name
  (PRD 01 §9), and a host that needs a distinct outcome maps the existing leaf in its own
  handler.

### 5.9 Overrides in host tests

Overrides are applied when the container is **built** (PRD 07 §5.5, IC-11). A host test
therefore has exactly three patterns, and all three end with "one container per test".

| Pattern | Shape | Use when |
|---|---|---|
| explicit plan | `DIContainer(overrides=plan)` inside the test's build helper | the default; the plan is visible at the call site |
| ambient plan | `with building_with(plan): container = build_app()` | the helper predates overrides and must not learn about them |
| per-test factory | a fixture that calls the helper with a plan, one container per test | a suite with many tests and one shared `conftest.py` |

Measured, from `probe09d.py` (one line per claim):

| Observation | Result |
|---|---|
| `DIContainer(overrides=plan)` with a `value` double | `Service.clock.now()` → **frozen**; `applied_overrides()` → `[Clock]` |
| `with building_with(plan):` around a plan-free helper | **frozen** |
| `active_override_plan()` after the block | `None` |
| one plan object, two containers, one built with it | `('frozen', 'real')` — no cross-container leakage |
| activating a plan after a container is built | `real` — the live container is unchanged |
| a `class` override with `scope=Scope.REQUEST` on a request-scoped target | two scopes resolve **two** objects (`x is y` → `False`) |
| a plan whose token nothing registers | `OverrideError {'token': 'Clock', 'kind': 'value', 'reason': 'target_missing', 'hint': 'register the token, or mark the override optional'}` |

The rules a host test suite must follow:

1. **Build, do not mutate.** There is no supported way to override a live container; a
   test that tries is testing a container it does not have.
2. **One plan, many containers is fine; one container, many plans is not.** The plan is
   frozen and read once (PRD 07 §5.9).
3. **Never leak the ambient plan.** `building_with` is a helper seam for a build function,
   not a per-request switch; a request that runs inside the block sees the plan only
   because the container was built inside it.
4. **A missing target is a plan error, not a resolution error.** `compile()` reports it
   before the first request, with `reason="target_missing"` and a hint naming
   `.optional()`.

### 5.10 The conformance suite

"Compatible" is not an opinion; it is twelve observations, each of which fails loudly when
an adapter skips its obligation. The suite is one parametrised test module per host shape,
with the host's own test client.

| # | Invariant | The observation that fails it |
|---|---|---|
| C-1 | The container is started before the host accepts work | the first unit's resolve constructs a singleton (hook count 0 before it) |
| C-2 | Strict mode is on | an unscoped `REQUEST` resolve returns an instance instead of raising |
| C-3 | One scope per unit, opened after the host bound its state | two units share a request-scoped instance, or a scope sees an empty value |
| C-4 | The scope lives in the host's per-unit state | two concurrent units resolve the same scope object |
| C-5 | Every resolution goes through the scope | `container.resolve` during a unit creates a second instance (M-19) |
| C-6 | Host objects are values, not generator providers | the framework closes an object the container also closes (M-15) |
| C-7 | The scope closes after the last body chunk / command output | a `@pre_destruct` runs before the client reads the last chunk (M-18) |
| C-8 | Teardown errors are reported, not raised | a failing `aclose()` turns a 200 into a 500 (M-9) |
| C-9 | `shutdown()` runs once and its tuple is read | a `@pre_destruct` count of 0 at exit, or 2 after a restart |
| C-10 | A sync host uses one loop, or loop-agnostic providers | a loop-bound singleton raises `RuntimeError: Event loop is closed` on unit 2 (M-6) |
| C-11 | Overrides are applied at build | a per-test double is visible to another test's container |
| C-12 | The adapter lives outside the library | `lauren_di.__all__` gains a name, or the package imports the host (D-57) |

Two rules make the suite usable across ten frameworks:

- **Absent host is SKIP, never PASS** (D-62). Each page pins the versions it measured;
  a run without the package reports the same test as skipped, so a green suite cannot
  mean "the framework was not installed".
- **The suite is parametrised by adapter, not by framework.** One module per host shape
  (ASGI, async web, WSGI, Django, CLI) and one adapter fixture per framework inside it.
  A framework that cannot satisfy an invariant fails the same assertion as its sibling,
  which is what makes the table above comparable across pages.

### 5.11 Reference adapter

The contract above fits in two objects. This is the whole adapter, and it is executed by
the documentation tests — it is the shape the host pages specialise, not a sketch.

```python run
import asyncio
from typing import Annotated, Any

from lauren_di import DIContainer, Inject, ResolutionScope, Scope, Token, injectable


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
        for error in errors:
            self.errors.append(error)


class Unit:
    """One request, job, command or connection. Single use."""

    def __init__(self, adapter: HostAdapter, values: dict[Any, Any]) -> None:
        self._adapter = adapter
        self._values = values
        self.scope: ResolutionScope | None = None

    async def __aenter__(self) -> ResolutionScope:
        self.scope = self._adapter.container.scope(values=self._values)
        return self.scope

    async def __aexit__(self, *exc_info: Any) -> bool:
        assert self.scope is not None, "unit entered without a scope"
        self._adapter.report(await self.scope.close())
        return False


REQUEST = Token("REQUEST")


class Connection:
    def __init__(self) -> None:
        self.closed = False

    async def aclose(self) -> None:
        self.closed = True


async def open_connection():
    conn = Connection()
    try:
        yield conn
    finally:
        await conn.aclose()


@injectable()
class Repo:
    def __init__(self, request: Annotated[Any, Inject(REQUEST)]) -> None:
        self.request = request


async def main() -> None:
    from lauren_di import use_factory

    container = DIContainer(strict_request_scope=True, value_tokens=(REQUEST,))
    container.register_class(Repo, Repo, scope=Scope.REQUEST)
    container.register_custom(
        use_factory(provide=Connection, factory=open_connection, scope=Scope.REQUEST)
    )
    adapter = HostAdapter(container)
    await adapter.start()

    first = Connection()
    async with adapter.unit({REQUEST: first}) as scope:
        assert await scope.resolve(Repo) is await scope.resolve(Repo)
        assert (await scope.resolve(Repo)).request is first
        conn = await scope.resolve(Connection)

    assert scope.closed and adapter.errors == []
    assert conn.closed                      # teardown ran after the unit, not during it

    second = Connection()
    async with adapter.unit({REQUEST: second}) as other:
        assert (await other.resolve(Repo)).request is second

    assert await adapter.stop() == ()


asyncio.run(main())
```

It is deliberately small, and three details are load-bearing:

- **`unit()` takes a mapping, not `**values`.** `**` forces string keys, so
  `unit(REQUEST=request)` would store the value under `"REQUEST"` and the `Token` key
  would never match — a silent `MissingProviderError` at resolve time. The measured
  failure is in `probe09e.py`'s history and is the reason the signature is a mapping.
- **The adapter owns no scope.** `Unit` holds it and hands it to the caller; nothing
  looks a scope up by thread, task or global, which is D-58 in code.
- **`__aexit__` returns `False`.** A teardown error must not replace the handler's own
  exception; the tuple is collected and reported (IC-8).

## 6. Architecture Fit & Feasibility

### 6.1 Layering

```text
host framework  ->  adapter (user code)      ->  lauren-di
--------------      ----------------------      ---------
request/job/CLI     opens the Unit              scope()
per-unit state      stores the ResolutionScope  resolve() on the scope
response end        closes the Unit in finally  close() -> tuple[DestructError, ...]
process start/stop  start() / stop()            startup() / shutdown()
```

Three layers, one arrow each. The adapter imports the host and `lauren_di`; `lauren_di`
imports neither the host nor the adapter (D-57). The adapter is application code — a
module in the user's project, not a package in this distribution — which is why the
library's `dependencies` list stays empty and why a framework's release cadence never
constrains this project.

### 6.2 What the library must not learn

The temptation, once ten adapters exist, is to move the common part into the library. It
must not move. Each row below is a thing an adapter wants and the library must refuse:

| Tempting addition | Why it stays out |
|---|---|
| `lauren_di.integrations.flask` (or any per-framework module) | D-57: a packaged adapter is a separate distribution depending on both projects; `import flask` in the package is already a test failure |
| `current_scope()` / a scope `ContextVar` | D-58: it does not survive a WSGI thread hop or `sync_to_async`, so it fails exactly where it is needed |
| `run_sync()` / a background loop thread | D-59: which loop model is correct depends on the host, and the measured cost difference (M-4, M-5) is the host's to pay |
| a construction lock | D-60: a blocking lock cannot be held across an `await`; the supported contract is `startup()` before serving |
| `Request` / `Response` / route / header types | PRD 01 §9: the container has no transport vocabulary |
| per-request overrides | PRD 07 §5.5: overrides are a build-time substitution; a request-time one is a second resolution path |
| automatic host-object discovery | IC-6: values are declared (`value_tokens=`) and supplied explicitly; scanning a request for attributes is the ambient lookup in another costume |

The mechanical guard is an assertion, not a promise: `T09-18` compares
`lauren_di.__all__` to the 38 names PRD 01 §5.3 fixes, and
`tests/packaging/test_no_framework_imports.py` fails on any framework import in the
package. Adding a name to make an adapter easier is a red test in two places.

### 6.3 Risks

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| an adapter closes the scope where the host's "response returned" hook fires | high — it is the natural place | teardown before a streamed body finishes (M-18) | close in the transport-level `finally`; the conformance case C-7 is written for exactly this |
| a host resolves concurrently inside one unit | medium | duplicate request-scoped instance, cached and leaked (M-3) | resolve sequentially; C-3/C-5 |
| a sync host reaches for `asyncio.run` per request | medium | loop-bound singleton breaks on the next request, sometimes silently (M-6) | the bridge is the default (D-59); C-10 |
| a threaded host relies on lazy construction | medium | N−1 leaked singletons on the first burst (M-1) | `startup()` before binding (D-60); C-1 |
| a framework-owned object is registered as an instance | medium | double close, or a close on the wrong loop | `register_value` for host-owned objects (M-15); C-6 |
| a failed `startup()` is retried | low | serving from a half-built cache (M-16) | treat it as fatal; C-9 |
| an adapter grows a framework-specific exception | low | the error catalogue stops being the contract | map the existing leaves; §7 |

### 6.4 Cost

Per unit of work, measured on Python 3.13.13 (`probe09b.py`, `probe09c.py`, `probe09d.py`):

| Operation | Cost | Note |
|---|---|---|
| open + close an empty scope | **1.1 µs** | the scope itself is not the cost |
| open + resolve one request-scoped class + close | **0.27 ms** idle, **0.33–0.39 ms** loaded | two `inspect.signature` calls per construction |
| profile of the same loop | **0.62 s of 0.72 s** inside `inspect` | constructor introspection dominates |
| one bridge call (sync host) | **0.18–0.33 ms** | M-4/M-5; includes the thread hand-off |
| one `asyncio.run` per unit | **0.24–0.33 ms** | plus broken loop affinity (M-6) |

The budget an adapter should publish is **~0.25–0.4 ms per request-scoped class
constructed**, dominated by introspection rather than by the scope or the cache. A host
that constructs many short-lived services per request should measure its own graph before
blaming the container; a host that constructs one service per request will not see it.

These bands are wall-clock figures from one box, so re-running `bridge_probe.py` on a second
host gave **0.17–0.26 ms/op** over ten runs — the floor and ceiling move with load, which is
why the durable claims are the *ordering* (one bridge call costs less than one `asyncio.run`,
0.32 ms in the same session) and the preserved loop affinity, not the absolute microsecond
count. Re-measure on the host you are deploying to before quoting a number to a user.

### 6.5 Evidence index

Every claim above traces to one of these files, run on Python 3.13.13 with `PYTHONPATH`
pinned to this checkout. The first line of each transcript is `lauren_di.__file__`.

| Probe | What it measures | Claim ids |
|---|---|---|
| `core_probe.py` | lazy compile, scope cache, values, single-use scope, cross-loop shutdown, teardown order, strict mode | M-8, M-11, M-12, M-13, M-14, M-15, M-19 |
| `core_probe2.py` | gather duplication, eager construction, `asyncio.run` cost, loop affinity, teardown errors, handler exception | M-1, M-2, M-3, M-4, M-6, M-9, M-10 |
| `bridge_probe.py` | bridge cost, 8-thread scoped resolves, blocking constructor | M-5, M-7 |
| `probe09.py` | startup/shutdown idempotency and restart, half-built cache, lazy compile, ASGI close point (`starlette 1.10.0`) | M-16, M-18, M-20 |
| `probe09b.py` | scope cost, `container.resolve` inside a scope, non-awaiting gather | M-17, M-19 |
| `probe09c.py` | profile of open+resolve+close | M-17 |
| `probe09d.py` | the three host-test override patterns, isolation, scope inheritance, missing target | §5.9 rows |
| `probe09e.py` | the reference adapter of §5.11, executed verbatim | §5.11, C-3, C-5, C-7, C-8 |
| `probe09f.py` | singleton → value token: compile accepts, `startup()` fails, cache holds 1 | M-21 |

The framework versions named in this page are the ones the probes printed:
`starlette 1.10.0` for M-18 and nothing else — every other number is the container alone.

## 7. Error Catalog Impact

Thirteen leaves, one base, and no new error class. Each row is the host-visible outcome an
adapter must produce; "response" means the host's error path (an error response, a
non-zero exit, a closed connection), never a silent default.

| Leaf | When a host sees it | Host-visible outcome |
|---|---|---|
| `MissingProviderError` | a resolve names a token nothing binds, or a value token is resolved with no value (M-13) | 500 / exit 1; a wiring bug, never a 404 |
| `DIScopeViolationError` | a `REQUEST` provider resolved with no scope and strict mode on | 500; the adapter failed to open a scope (C-2) |
| `LifecycleViolationError` | a resolve after `scope.close()` (M-14) | 500; a scope was cached across units |
| `CircularDependencyError` | `compile()` | fatal at startup, before binding |
| `DuplicateBindingError` | `compile()` | fatal at startup |
| `ProtocolAmbiguityError` | `compile()`, or a scalar resolve of a multi-bound token | fatal at startup |
| `UnresolvableParameterError` | registration | fatal at startup |
| `InvalidProviderError` | registration | fatal at startup |
| `LifecycleConfigError` | registration (e.g. a generator provider marked `TRANSIENT`) | fatal at startup |
| `DecoratorUsageError` | the decorator | fatal at import |
| `MetadataInheritanceError` | the decorator | fatal at import |
| `OverrideError` | plan construction or `compile()` (`target_missing`) | fatal in a test; the plan is wrong |
| `DestructError` | returned by `scope.close()` / `shutdown()` (M-9) | log + metric; never raised into a committed response |
| `DIError` | the base | the adapter's catch-all for "DI failed", mapped to 500 / exit 1 |

Two statements the host pages must repeat:

- **No new leaf.** If a framework needs a distinct outcome, the adapter maps one of the
  thirteen. A `HostIntegrationError` in this library would be a framework noun in the
  error catalogue.
- **`DestructError` is returned.** It subclasses `DIError` so a caller may raise it
  deliberately, but the container does not; an adapter that never reads `scope.errors` is
  silently discarding teardown failures.

## 8. Phased Implementation Plan

No library change is in any phase. Each phase is documentation, an example, and a
skip-guarded test module; the version column is the point at which a user can rely on it.

| Phase | Deliverable | Gate that proves it | Version |
|---|---|---|---|
| P09-1 | `docs/guides/hosting.md`: this contract, framework-neutral, with the reference adapter | the page compiles; its `run` fence executes | 0.1.0 |
| P09-2 | `examples/host_unit_of_work.py`: one runnable app using `HostAdapter`, no framework | `tests/examples/` executes it as a subprocess | 0.1.0 |
| P09-3 | `tests/integration/hosts/`: the C-1…C-12 suite, parametrised by adapter, every framework test skip-guarded | SKIP without the package, PASS with it (D-62) | 0.2.0 |
| P09-4 | one compatibility page per host family, each pinning the versions it measured | `prds/check_consistency.py` and the corpus tests | 0.2.0 |
| P09-5 | a second adapter (a sync host) proving the bridge is copyable, not theoretical | the same suite, `C-10` included | 0.3.0 |

The order matters: the contract and its executable example exist before any framework
page, so a page that contradicts §5 fails a test rather than starting a debate.

## 9. Documentation & Convention Updates

| Document | Change | Rule it carries |
|---|---|---|
| `docs/guides/hosting.md` (new) | §5 in prose, the reference adapter, the close-point table | the only page that names a "request" as a host concept |
| `docs/concurrency.md` | link to §5.7; the "one scope per task" rule | no second statement of the rule |
| `docs/guides/providers.md` | `register_value` vs `register_instance` for host-owned objects | one ownership table, not two |
| `llms.txt`, `llms-full.txt` | the new page in the index and the corpus | regenerated, never hand-edited |
| `examples/README.md` | the host example row | the example is executed, so the row cannot rot |
| host pages | `fragment`/`skip` fences only, per D-63 | a `run` fence must execute with no framework installed |

Two conventions, both mechanical:

1. **Every framework sample is `fragment` or `skip`.** The corpus test executes `run`
   fences in an environment whose dependency list is empty; a framework sample marked
   `run` is a promise the corpus cannot keep (D-63).
2. **Every claim names its artifact.** A version, a probe file or the words "documented
   API, not measured here". There is no third category, and a number without one is a
   documentation bug.

## 10. Test Plan

### 10.1 Enumerated test cases

`T09-nn` are the tests this page specifies. The integration ones live in
`tests/integration/hosts/` and are skip-guarded per adapter (D-62); the unit ones need no
framework.

| # | Invariant | Fixture | Action | Expected observation |
|---|---|---|---|---|
| T09-01 | IC-1 | a singleton with a hook counter | start the adapter, then resolve | hook count 1 **before** the first resolve |
| T09-02 | IC-2 | a `REQUEST` provider, no scope | `container.resolve(T)` | `DIScopeViolationError` with `reason="no_active_scope"` |
| T09-03 | IC-3 | two units | resolve the same `REQUEST` token in each | different objects |
| T09-04 | IC-4 | two concurrent units on one adapter | read the scope from each unit's state | distinct `ResolutionScope` objects |
| T09-05 | IC-5 | an open unit | `container.resolve(T)` after `scope.resolve(T)` | a second instance — the test asserts the adapter never does this |
| T09-06 | IC-6 | a host-owned object supplied as a value | close the unit | the object is **not** closed by the container |
| T09-07 | IC-7 | a streaming unit with a 3-chunk body | read the whole body, then exit the unit | the `@pre_destruct` marker appears **after** the last chunk |
| T09-08 | IC-8 | a `@pre_destruct` that raises | close the unit | one `DestructError` in `scope.errors`; the unit's own exception still propagates |
| T09-09 | IC-9 | a container started and stopped once | stop, then stop again | one `@pre_destruct`; both tuples are `()` |
| T09-10 | IC-10 | the bridge from §5.5 | 100 scoped resolves from 4 threads | no errors; one singleton id |
| T09-11 | IC-11 | a plan with a `value` double | build two containers, one with the plan | `('frozen', 'real')`; `applied_overrides()` names the token once |
| T09-12 | IC-12 | the package | `dir(lauren_di)` and an import scan | no framework module imported; `__all__` unchanged |
| T09-13 | M-3 | a `REQUEST` provider whose factory awaits | `gather(scope.resolve(T), scope.resolve(T))` | documents the duplicate (`a is b` False, 2 cache entries) — the adapter rule is "resolve sequentially" |
| T09-14 | M-16 | a `@post_construct` that raises | `startup()` | the exception propagates, the cache holds 1, `shutdown()` returns `()` |
| T09-15 | M-8 | `B` depends on `A`, both request-scoped | close the unit | teardown order `['B', 'A']` |
| T09-16 | M-12, M-13 | a value token supplied at open | resolve it, then resolve an unsupplied one | the value; then `MissingProviderError` |
| T09-17 | M-14 | a closed scope | resolve again | `LifecycleViolationError` |
| T09-18 | G10 | the package | compare `lauren_di.__all__` to PRD 01 §5.3 | exactly 38 names, none host-specific |

### 10.2 Acceptance traceability

| Goal | Tests |
|---|---|
| G1 (one contract, testable per framework) | T09-01 … T09-12 |
| G2 (close point per host shape) | T09-07, T09-15 |
| G3 (one route for host values, one ownership rule) | T09-06, T09-16 |
| G4 (a sync host is correct without a library change) | T09-10 |
| G5 (startup/shutdown ordering, including failure) | T09-09, T09-14 |
| G6 (concurrency limits stated, not discovered) | T09-13, T09-17 |
| G7 (every leaf has a host outcome, no new leaf) | T09-02, T09-08; the §7 table |
| G8 (overrides usable in a host test) | T09-11 |
| G9 ("compatible" is a suite; absent is SKIP) | T09-01 … T09-12 under D-62 |
| G10 (nothing here teaches the library a framework) | T09-12, T09-18 |


