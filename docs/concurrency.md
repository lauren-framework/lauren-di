# Async, concurrency and lifetime

Resolution is `async`, but the container itself owns no task, no thread and no
lock. Everything concurrent happens in *your* loop, *your* tasks and *your*
threads, and the container only holds two pieces of mutable state: the
singleton cache and the per-scope request cache.

Every claim on this page was measured with a probe script run against this
build (one process, `PYTHONPATH=.`, all timings on an idle machine). Where the
probe showed no guarantee, the page says so instead of implying safety. The
lifetimes themselves are described in [guides/scopes.md](guides/scopes.md).

## Why resolution is async

`resolve` is a coroutine because the *dependency graph* can suspend, not
because the container schedules anything. Awaiting it is a normal call: no
task is created, nothing is queued, no background thread is involved.

What actually awaits:

| Suspends on | Does not suspend on |
| --- | --- |
| an `async` factory (`use_factory`) | a plain class constructor |
| a `post_construct` hook declared `async` | reading a cached singleton |
| async-generator provider setup (`__aenter__`) | reading a cached request instance |
| teardown during `scope.close()` | the recursive walk over sync deps |

Measured: resolving a singleton whose constructor is plain `def __init__`
never yields to the loop — a task that was already runnable did not run at all
during `await container.resolve(...)`. So `await resolve(...)` is *not* a
suspension point unless something inside actually suspends. `resolve` does
return a coroutine object, an `async` factory is awaited and built exactly once
for that call, and an `async` `post_construct` hook has finished before
`resolve` returns.

<!-- docs-contract: run -->
```python
import asyncio

from lauren_di import DIContainer, Scope, injectable


@injectable(scope=Scope.SINGLETON)
class Config:
    pass


@injectable(scope=Scope.REQUEST)
class Repo:
    def __init__(self, cfg: Config) -> None:      # constructor injection
        self.cfg = cfg


async def main() -> None:
    container = DIContainer()
    container.register(Config)
    container.register(Repo)
    container.compile()

    # Two awaits, one singleton: nothing yields, so it is built once.
    first, second = await asyncio.gather(
        container.resolve(Config),
        container.resolve(Config),
    )
    print("singleton shared:", first is second)      # True

    async with container.scope() as scope:
        a = await scope.resolve(Repo)
        b = await scope.resolve(Repo)
        print("per-scope cache:", a is b)            # True


asyncio.run(main())
```

## One container across several event loops

Building is loop-free. The compiled graph and the singleton cache are plain
Python objects; nothing in the container is bound to a loop, and nothing checks
which loop you resolve on.

Measured: a singleton built in loop A and resolved again in loop B came back as
the *same cached object* with no rebuild and no error. Measured: a singleton
whose factory returned an `asyncio.Lock`, acquired and released in loop A, was
re-acquired in loop B without error — CPython only checks loop affinity when
the primitive actually has to wait, so the failure mode for contention is a
hang, not an exception.

* Safe: share one compiled container across loops, threads and processes' worth
  of sequential `asyncio.run()` calls, as long as the singletons you reuse are
  plain data.
* Not safe: singletons that captured loop-bound objects — a `Task`, a `Future`,
  a socket or `AsyncClient` opened in loop A, a queue with waiters. The
  container neither detects nor repairs this; you get a hang or a stale
  connection later, far from the cause.
* No guarantee: resolving the same singleton for the *first* time from two loops
  at once (see the next section) and tearing it down while another loop uses it.

## Concurrent resolution of one singleton

There is no lock and no in-flight table in the container: `lauren_di/_container.py`
contains no `Lock` at all. The singleton path is read-cache, build, write-cache,
with no protection between the read and the write.

| Case | Builds for 5 concurrent `gather`s | Distinct objects |
| --- | --- | --- |
| singleton, plain `def` constructor | 1 | 1 |
| singleton, `async` factory that awaits once | 5 | 5 |

Measured, both rows. The sync row is not a guarantee of de-duplication — it is
an accident of nothing suspending, so the five awaits run one after another.
The async row is the honest answer: **no in-flight de-duplication**. Every task
that arrives while the factory is suspended builds its own instance; the cache
keeps the last writer (measured: the cache held one of the five). The four
losers are never torn down and their side effects stand — four connections, four
warm-up calls, four log lines.

If a singleton must be built once under concurrency, serialise the first
resolution yourself (an `asyncio.Lock` around your own bootstrap, or resolve it
before fanning out).

## The request cache is per scope, not per task

`async with container.scope() as scope` opens one cache object. The cache is
keyed by token and shared by everything that resolves through that scope object.

Measured: two resolves of a REQUEST dep through one scope returned the same
object (one build); two scopes produced two objects (two builds). Measured: five
concurrent resolves of a REQUEST dep *inside one scope* produced five builds and
five distinct objects, the last write winning. Same cause as singletons — the
cache is not a lock, so concurrent tasks in one scope can each build their own
instance.

```python
async def two_scopes(container: DIContainer) -> None:
    async with container.scope() as one, container.scope() as two:
        left = await one.resolve(Repo)
        right = await two.resolve(Repo)
        assert left is not right        # one instance per scope
```

Measured: resolving a REQUEST dep with no open scope is *allowed* — the
container warns that the instance will never be torn down — and with
`DIContainer(strict_request_scope=True)` it raises `DIScopeViolationError`
instead; see [errors](errors.md). Practical rule: share a scope between tasks
only when the deps they resolve are built synchronously, or resolve once and
pass the object down.

## Overrides are a build-time ContextVar

`building_with(plan)` sets the active `OverridePlan` in a `ContextVar`;
`active_override_plan()` reads it. Measured visibility:

| Where | Sees the plan |
| --- | --- |
| the same task, inside the block | yes |
| a child task created inside the block | yes |
| a sibling task created *before* the block | no |
| a plain `threading.Thread` | no |
| `await asyncio.to_thread(...)` | yes (the context is copied) |

Measured: after the block, `active_override_plan()` is `None` again. Measured:
the container exposes no resolve-time override surface — the only public
attribute is `applied_overrides` (plus private helpers), and there is no
`container.override()` method. Overrides are applied to the *build*, so the
plan must reach the build, either as `DIContainer(overrides=plan)` or via the
context.

```python
from lauren_di import DIContainer, OverridePlan, building_with


def build_with(plan: OverridePlan) -> DIContainer:
    with building_with(plan):                 # active for this build only
        container = DIContainer(overrides=plan)
    container.compile()
    return container
```

No guarantee: this probe did not verify end-to-end substitution.
`ProviderOverride(Real, "class", Fake)` was rejected with
`OverrideError: bad_payload`, so the payload shape for that kind was not
established here. Measured caveat: `applied_overrides` is always an
`OverridePlan` object — even for a container built with no plan — so comparing
it by identity tells you nothing; compare its rows.

## Threads

Measured: four threads, each running its own loop, resolving one singleton whose
factory sleeps 100 ms — four builds, four distinct objects, no exception
raised. The losing writes simply overwrite the cache entry. The same probe
showed no error of any kind, which is the dangerous part: silent duplication.

* Safe: build and `compile()` in one thread, then resolve from other threads,
  provided no two threads are the *first* to resolve the same singleton.
* Not safe: two threads racing the first resolution of a singleton (duplicate
  construction, as above).
* No guarantee: registering, re-registering or compiling while another thread
  resolves — not measured, and there is no lock that would make it atomic.

## Blocking factories block the loop

The container never offloads work for you: a synchronous factory runs inline in
whatever task called `resolve`.

Measured: a 10 ms ticker task got **0 ticks** while a 200 ms `time.sleep`
factory ran. Measured: the same delay written as `await asyncio.to_thread(...)`
inside an `async` factory let the ticker run **20 times** over the same window.

```python
import asyncio
import time

from lauren_di import Scope, injectable


@injectable(scope=Scope.SINGLETON)
class Client:
    pass


async def make_client() -> Client:
    await asyncio.to_thread(time.sleep, 0.2)   # blocking setup, offloaded
    return Client()
```

Rule: every factory that touches I/O is `async`, and any blocking call inside it
goes through `asyncio.to_thread`.

## Cancellation mid-resolve

Measured, with a task cancelled while it was suspended inside a REQUEST dep's
factory:

* the caller saw `CancelledError`;
* the scope stayed usable — `scope.closed` was still false;
* a token already built in that scope still returned the same object;
* re-resolving the cancelled token built it again from scratch (that factory
  never completes, so the retry then timed out);
* leaving the `async with` block still closed the scope, ran the destructor of
  the dep that *had* been built, and recorded no errors.

```python
async def cancel_inside(container: DIContainer) -> None:
    async with container.scope() as scope:
        task = asyncio.create_task(scope.resolve(Repo))
        await asyncio.sleep(0)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass          # the scope itself is unaffected
```

No guarantee: an instance whose factory was cancelled part-way is not tracked
anywhere, so if that factory had already opened a connection nothing closes it.
Also measured, and worth knowing: in the same scope a plain factory returning an
object with a `@pre_destruct` hook got no hook call on close — teardown fired
only for the class-registered dep. Use the provider form described in
[guides/scopes.md](guides/scopes.md) if you need deterministic teardown. A
cancelled `scope.close()` was not measured at all.

## Rules of thumb

| Situation | Verdict | Evidence |
| --- | --- | --- |
| `await container.resolve(T)` with a sync constructor | no yield, no task created | measured: 0 loop turns |
| one container, several loops, plain-data singletons | fine | measured: cached object reused |
| one container, several loops, loop-bound singleton | unsafe, may hang | measured: uncontended reuse OK, affinity only checked when waiting |
| 5 concurrent first resolutions of an `async`-factory singleton | 5 builds | measured |
| 5 concurrent first resolutions of a sync singleton | 1 build, by accident | measured |
| concurrent resolves in one scope | one build per task | measured |
| sharing one scope across tasks | safe only for sync construction | measured |
| REQUEST dep with no scope | allowed, leaks; `strict_request_scope=True` raises | measured |
| override plan and a child task | visible | measured |
| override plan and a pre-existing sibling task or plain thread | invisible | measured |
| blocking work inside a factory | freezes the loop | measured: 0 ticks vs 20 |
| cancel during resolve | scope survives, partial instance untracked | measured |
| register/compile while another thread resolves | no guarantee | not measured, no lock exists |

## What was not measured

Destructor *ordering* when several deps are torn down, a cancelled
`scope.close()`, `OverridePlan` substitution end to end, and registration races
between threads. Treat each as unspecified rather than as safe.
