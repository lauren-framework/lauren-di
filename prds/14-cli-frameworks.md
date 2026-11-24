# PRD 14 — CLI Frameworks: argparse, click 8.5.1, Typer 0.29.2, cyclopts 5.5.2, fire 0.7.1

**Status:** Draft — 2026-11-24
**Depends on:** PRDs 01–09
**Related:** `prds/README.md` §3 (product boundary), §4 (decision register)
**Evidence:** `examples/apps/{argparse,click,typer,cyclopts,fire}/tests/`, run by `nox -s examples`; §6.5 indexes every claim id to the test that asserts it

Claim ids `H-1 … H-27` are measured; §6.5 indexes each one to the test that asserts it, or —
where no shipped test re-runs the observation — says so and marks it authoring-time-only,
with the framework version either way. Anything not in that index is labelled "documented
API, not measured here".

---

## 1. Problem Statement

A CLI host is the cheapest host to integrate and the easiest to integrate wrongly. There is no
server loop, no middleware chain and no per-request object to hang a scope on. The process is the
container's lifetime, the command body is the only unit of work, and the only observable the shell
sees is the exit code. Every mistake is silent: the command exits 0 and the connection is never
closed.

Five hosts, three different execution models:

- **argparse** (stdlib) calls a handler you attach and throws its return value away `[H-1]`.
- **click** and **Typer** are synchronous frameworks. click discards the return value `[H-5]`;
  Typer discards it too and, worse, accepts an `async def` command and never awaits it `[H-9,
  H-10]`.
- **cyclopts** awaits `async def` commands and turns the return value into the exit code
  `[H-13, H-14]`.
- **fire** returns the handler's value to the caller of `fire.Fire()` and prints it, and sets no
  exit code `[H-16]`.

The second problem is the parameter namespace. Every one of these frameworks reads the command
function's signature. `lauren-di`'s injection markers live in the same place: `Depends[X]` is `X`
itself, so `Annotated[X, Depends[X]]` is `Annotated[X, X]`. Measured consequences: Typer turns the
injected parameter into a required CLI argument `[H-11]`, cyclopts raises `CoercionError` while
parsing it `[H-15]`. Injection through the command signature is not available on this host family.

The third problem is teardown. `lauren-di` reports teardown failures; it never raises them
`[H-23]`. A CLI adapter that ignores the returned tuple exits 0 while a connection stayed open.

### 1.1 Measured failure modes

| # | Host | What was run | Observation |
|---|------|--------------|-------------|
| H-1 | argparse | handler returns `5` | caller discards it, process exit 0 |
| H-2 | argparse | missing positional | `SystemExit(2)` |
| H-3 | argparse | handler returns a coroutine | coroutine object, never awaited |
| H-4 | argparse | `KeyboardInterrupt` in handler | rc `-2`, `finally` ran, traceback on stderr |
| H-5 | click 8.5.1 | `standalone_mode` returns `5` | return discarded, exit 0 |
| H-6 | click 8.5.1 | body raises `RuntimeError` | real process rc 1 + traceback; `CliRunner` rc 1 with `result.exception` |
| H-7 | click 8.5.1 | `ctx.exit(4)` | `SystemExit(4)`, exit 4 |
| H-8 | click 8.5.1 | `try/finally` + `ctx.call_on_close` | order `body → finally → call_on_close`; group-level close runs after the subcommand body |
| H-9 | Typer 0.29.2 | command returns `5` | return discarded, exit 0 |
| H-10 | Typer 0.29.2 | `async def` command | body never ran, exit 0, `RuntimeWarning: coroutine 'go' was never awaited` |
| H-11 | Typer 0.29.2 | `Annotated[Mailer, Inject(Mailer)]` parameter | became a CLI argument (`Usage: send [OPTIONS] {name} {mailer}`), exit 2 |
| H-12 | Typer 0.29.2 | `ctx.obj` via `CliRunner(..., obj=...)` | body saw the object, exit 0 |
| H-13 | cyclopts 5.5.2 | command returns `5` | `SystemExit(5)` |
| H-14 | cyclopts 5.5.2 | `async def` command returning `9` | awaited (`async_ran=['async-body']`), exit 9 |
| H-15 | cyclopts 5.5.2 | `Annotated[Mailer, Inject(Mailer)]` parameter | `CoercionError: Invalid value for …` at parse time |
| H-16 | fire 0.7.1 | command returns `5` / `('a', 1)` | value returned to the caller (`ret=5`, `ret=('a', 1)`), no exit-code conversion |
| H-17 | click 8.5.1 | SIGINT during the body | rc 1, `finally` and `call_on_close` both ran |
| H-18 | click 8.5.1 | SIGTERM, default handler | rc `-15`, markers `['body']` only — no cleanup |
| H-19 | click 8.5.1 | SIGTERM, handler raising `SystemExit(143)` | rc 143, `finally` and `call_on_close` ran |
| H-20 | lauren-di | `await container.resolve(ReqScoped)` twice, no scope | two distinct instances; `shutdown()` returned `()`; `pre_destruct` never ran |
| H-21 | lauren-di | same token inside `async with container.scope()` | one instance, torn down at scope exit, `shutdown()` then returned `()` |
| H-22 | lauren-di | `await container.startup()` with a singleton | `post_construct` already ran, before any `resolve` |
| H-23 | lauren-di | one `pre_destruct` raises | `shutdown()` returned `(DestructError,)`, `code='destruct_error'`, `detail` keys `cause/class/origin/stage`; the remaining destructors still ran (`['mailer.up', 'fragile.down', 'mailer.down']`) |
| H-24 | lauren-di | 50 invocations, Python 3.13.13, two shapes, ten stored re-runs | bare `asyncio.run(noop)` 177–376 µs; two providers, clean teardown: fresh container 1315–1421 µs, reused 593–779 µs; three providers with a failing `pre_destruct`: fresh 2303–4318 µs, reused 1336–2250 µs |
| H-25 | lauren-di | `OverridePlan({Mailer: ProviderOverride(Mailer, "value", value="FAKE")})` + `building_with` | `await scope.resolve(Mailer)` returned `'FAKE'` |
| H-26 | Typer 0.29.2 | one-command `typer.Typer()` under `CliRunner` | `invoke(app, ["only"])` → exit 2; `invoke(app, [])` → exit 0, body sees `ctx.obj` (`named_exit=2 empty_exit=0`) |
| H-27 | fire 0.7.1 | handler raises `RuntimeError`; handler returns a tuple | exception propagates to the `fire.Fire` caller with no exit (`exc=RuntimeError`, `exit=no-exit`); a returned `('a', 1)` comes back verbatim (`ret=('a', 1)`) |

### 1.2 What "compatible" means for this page

A CLI host is compatible when, for one process invocation:

1. `startup()` completes before the command body runs and `shutdown()` completes after it, on the
   same event loop;
2. exactly one scope is open around the body and it is closed exactly once, before the process
   exits, including on error, on `SystemExit` and on Ctrl-C;
3. the process exit code is `0` only when the body returned normally **and** the teardown tuple is
   empty; a teardown failure is printed to stderr and reflected in the exit code;
4. a DI error is printed once, on stderr, with a non-zero exit code, and never as a bare traceback
   of an internal type.

Each clause is testable and appears as a `T14-nn` row in §10.

---

## 2. Goals

| Id | Goal | Acceptance |
|----|------|-----------|
| G1 | One container per process invocation, started before the body and shut down after it | `A-2`, `T14-03`, `T14-04` |
| G2 | Exactly one scope, closed exactly once, on every exit path | `A-3`, `T14-05`, `T14-06` |
| G3 | Exit code derived from the teardown tuple, not from a bare `0` | `A-5`, `T14-07` |
| G4 | No injected parameter appears in any command signature | `A-6`, `T14-08`, `T14-09` |
| G5 | Teardown failures are visible on stderr with a stable code | `A-7`, `T14-07` |
| G6 | A user can override a provider for one test without touching the CLI wiring | `A-9`, `T14-11` |
| G7 | The recipe for all five hosts is documented and every snippet is `fragment`-fenced | `A-11`, `T14-13` |

---

## 3. Non-Goals

**Library boundary.** No framework import enters `lauren-di` `D-57`, `D-63`. No adapter is
packaged, published or discovered through a plugin entry point by the library `D-57`. No bridge
helper (`asyncio.run` wrapper, loop factory, signal helper) is added to the library `D-59`. No
ambient or `ContextVar`-based scope is introduced for the CLI case `D-58`.

**Framework features deliberately not integrated.**

| Framework | Not integrated | Why |
|-----------|----------------|-----|
| argparse | subparser introspection, `type=` factories, `add_argument`-level injection | the parser builds values, not services |
| click | parameter types, `ParameterSource`, `result_callback`, prompt handling | injection through parameters is unavailable (see §1) |
| Typer | the `Annotated[...]` parameter namespace, `typer.Option` defaults as DI switches | it is the CLI namespace; DI metadata there becomes a CLI argument `[H-11]` |
| cyclopts | `App.config`, `types=` registry, parameter groups | same namespace collision `[H-15]` |
| fire | docstring parsing, `--separator`, component discovery | fire is an inspection layer over one function |
| all | adding async support to a sync framework | upstream decision, not a DI concern |

**Also out of scope.** Daemonisation, `atexit` registration, exit-code conventions beyond
`0 / 2 / 70 / 130 / 143`, and anything about a host that serves requests — that is PRD 09 and the
web pages.

---

## 4. Prior Art

Injection into a CLI is usually done by inspecting the command signature, which is what
`flask-injector` and `django-injector` do for their frameworks and what FastAPI's `Depends` does
per parameter. On this host family that approach fails for a mechanical reason: the same
`__annotations__` slot is the CLI parameter namespace, and `lauren-di`'s marker for the common
case is the type itself (`Depends[X] is X`, measured), so a framework cannot distinguish "this
parameter is a service" from "this parameter is a string". Typer and cyclopts both read that slot
and both mis-handle it `[H-11, H-15]`.

The second tradition is the container-as-object: `dependency-injector`'s `Resource` providers,
`punq`'s single container, `injector`'s modules. Those libraries own the object graph and hand it
to the framework through a module-level singleton. `lauren-di` deliberately does not own a
singleton `D-58`; the adapter owns the instance and passes it explicitly, which is why every
recipe below threads a container through `ctx.obj`, a closure or a module global and never through
a global registry.

| Source | Borrowed | Rejected |
|--------|----------|----------|
| FastAPI `Depends` | per-parameter resolution as an idea | parameter-slot injection (collides with CLI parameters) |
| `flask-injector` / `django-injector` | container built once, injected per unit of work | patching framework internals |
| `dependency-injector` | explicit `Resource`-style teardown on the way out | global container singleton, YAML config |
| click's own ecosystem | `ctx.obj` as the per-invocation carrier `[H-12]` | `result_callback` as a teardown hook (no such hook on all hosts) |
| cyclopts | `async def` command + return value as exit code `[H-13, H-14]` | its `types=` registry |

---

## 5. Proposed API

This page proposes no library API. It proposes one adapter recipe per host, expressed as
requirements the adapter must satisfy and as code shapes a user can copy.

### 5.1 Requirements

| Id | Requirement |
|----|-------------|
| A-1 | The adapter builds the container inside the invocation entry point, never at import time. |
| A-2 | The adapter awaits `startup()` before the command body and `shutdown()` after it, in a `finally`. |
| A-3 | The adapter opens exactly one scope with `async with container.scope()` and resolves every request-scoped token from it, never from the container `[H-20, H-21]`. |
| A-4 | The adapter owns the event loop: one `asyncio.run(...)` per invocation, on a sync host, inside the command body `[H-10]`. |
| A-5 | The adapter converts the tuple returned by `shutdown()` into an exit code: `0` when empty, `70` when not `[H-23]`. |
| A-6 | No injected parameter appears in any command signature; services are resolved inside the body `[H-11, H-15]`. |
| A-7 | Each `DestructError` is printed to stderr as one line containing `code` and `message`. |
| A-8 | A `DIError` is caught at the entry point, printed once, and mapped to exit code `70` (§7). |
| A-9 | A test overrides a provider with `OverridePlan` + `building_with`, without editing the CLI `[H-25]`. |
| A-10 | The adapter installs a SIGTERM handler that raises `SystemExit(143)` when the host allows it `[H-18, H-19]`. |
| A-11 | Every snippet in the documentation is `fragment`-fenced; no snippet is executed as a test. |
| A-12 | The adapter never reuses one container across two invocations unless every provider is known to be loop-free. |

### 5.2 Per-framework matrix

| | argparse | click 8.5.1 | Typer 0.29.2 | cyclopts 5.5.2 | fire 0.7.1 |
|---|---|---|---|---|---|
| Lifecycle start | `startup()` in the entry function | `startup()` in the command body | `startup()` in the command body | `startup()` at the top of the `async` command | `startup()` in the wrapped function |
| Lifecycle stop | `shutdown()` in `finally` | `shutdown()` in `finally` | `shutdown()` in `finally` | `shutdown()` in `finally` | `shutdown()` in `finally` |
| Scope storage | local variable | `ctx.obj` `[H-12]` | `ctx.obj` `[H-12]` | local variable | local variable |
| Scope open/close | `async with` inside the coroutine | `async with` inside the coroutine | `async with` inside the coroutine | `async with` inside the command | `async with` inside the coroutine |
| Handler model | sync handler, `asyncio.run` inside | sync command, `asyncio.run` inside | **sync** command, `asyncio.run` inside `[H-10]` | `async def` command `[H-14]` | sync function, `asyncio.run` inside |
| Exit-code source | `raise SystemExit(main())` `[H-1]` | `ctx.exit(code)` `[H-7]` | `raise typer.Exit(code)` | return value `[H-13]` | return value of `fire.Fire` `[H-16]` |
| Teardown reporting | stderr line + exit code | stderr line + `ctx.exit(70)` | stderr line + `typer.Exit(70)` | stderr line + `return 70` | stderr line + exit code |
| Test client | direct call of `main(argv)` | `click.testing.CliRunner` `[H-6]` | `typer.testing.CliRunner` `[H-12]` | `app([...], exit_on_error=False)` | direct call of the wrapped function |
| Override strategy | `building_with` + `DIContainer(overrides=plan)` | same | same | same | same |

### 5.3 The shared shape

One helper carries the whole contract; every host recipe calls it.

```python skip
import asyncio
from collections.abc import Awaitable, Callable

from lauren_di import DIContainer, DestructError, Scope, injectable, pre_destruct


@injectable()
class Mailer:
    def send(self, to: str) -> str:
        return f"queued {to}"


def build_container() -> DIContainer:
    container = DIContainer()
    container.register(Mailer)
    return container


async def run_invocation(
    container: DIContainer,
    body: Callable[[object], Awaitable[None]],
) -> int:
    errors: tuple[DestructError, ...] = ()
    await container.startup()
    try:
        async with container.scope() as scope:
            await body(scope)
    finally:
        errors = await container.shutdown()
    for error in errors:
        print(f"teardown {error.code}: {error.message}", file=__import__("sys").stderr)
    return 70 if errors else 0
```

### 5.4 argparse

```python fragment
import argparse
import asyncio

from lauren_di import DIContainer


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="app")
    parser.add_argument("to")
    args = parser.parse_args(argv)

    async def body(scope) -> None:
        mailer = await scope.resolve(Mailer)
        print(mailer.send(args.to))

    return asyncio.run(run_invocation(build_container(), body))


if __name__ == "__main__":
    raise SystemExit(main())
```

`argparse` discards the handler's return value `[H-1]`, so the `raise SystemExit(main())` is the
adapter, not a style choice. Ctrl-C reaches the `finally` in `run_invocation` because the
interpreter unwinds the stack; measured rc is `-2` with the `finally` executed `[H-4]`.

### 5.5 click

```python fragment
import asyncio

import click
from lauren_di import DIContainer


@click.group()
@click.pass_context
def cli(ctx: click.Context) -> None:
    ctx.obj = {"container": build_container()}


@cli.command()
@click.argument("to")
@click.pass_context
def send(ctx: click.Context, to: str) -> None:
    async def body(scope) -> None:
        mailer = await scope.resolve(Mailer)
        click.echo(mailer.send(to))

    code = asyncio.run(run_invocation(ctx.obj["container"], body))
    if code:
        ctx.exit(code)
```

Rules that follow from the measurements: the container goes in `ctx.obj` at the group callback
`[H-12]`; teardown happens inside `run_invocation`'s `finally`, which runs before
`ctx.call_on_close` `[H-8]`, so `call_on_close` is a safety net for paths that never reach the body
and must not shut the container down a second time; `ctx.exit(code)` produces `SystemExit(code)`
`[H-7]`.

### 5.6 Typer

```python fragment
import asyncio

import typer
from lauren_di import DIContainer

app = typer.Typer()


@app.callback()
def main(ctx: typer.Context) -> None:
    ctx.obj = {"container": build_container()}


@app.command()
def send(ctx: typer.Context, to: str) -> None:
    async def body(scope) -> None:
        mailer = await scope.resolve(Mailer)
        typer.echo(mailer.send(to))

    code = asyncio.run(run_invocation(ctx.obj["container"], body))
    raise typer.Exit(code)
```

Two hard rules. The command is `def`, not `async def`: Typer 0.29.2 accepts an `async def` command
and never awaits it, so the body is skipped and the process exits 0 with only a `RuntimeWarning`
`[H-10]`. And no `Annotated[Mailer, Inject(Mailer)]` parameter: Typer publishes it as a CLI
argument `[H-11]`, so the command fails with exit 2 before it ever runs.

A third rule bites the *test*, not the command: a `typer.Typer()` with a single command is not a
group, so `CliRunner().invoke(app, ["send"], obj=...)` passes `"send"` as the value of the first
parameter and fails with exit 2 (`named_exit=2`), while `invoke(app, [], obj=...)` runs the body
and sees `ctx.obj` (`empty_exit=0`, `BODY x {'c': 'T'}`) `[H-12, H-26]`. A two-command app takes
the subcommand name as usual. `ctx.obj` itself is sound — click's `@click.pass_obj` sees it too,
and the group callback's assignment wins over the object passed to the runner
(`click_pass_obj: out='SHOW container'`) `[H-12]`.

### 5.7 cyclopts

```python fragment
import cyclopts
from lauren_di import DIContainer

app = cyclopts.App(name="app")


@app.command
async def send(to: str) -> int:
    async def body(scope) -> None:
        mailer = await scope.resolve(Mailer)
        print(mailer.send(to))

    return await run_invocation(build_container(), body)
```

cyclopts is the only host here that awaits the command `[H-14]`, so the recipe has no
`asyncio.run` and no wrapper: the `async def` command returns the exit code directly `[H-13]`.

### 5.8 fire

```python fragment
import asyncio

import fire
from lauren_di import DIContainer


def send(to: str) -> int:
    async def body(scope) -> None:
        mailer = await scope.resolve(Mailer)
        print(mailer.send(to))

    return asyncio.run(run_invocation(build_container(), body))


if __name__ == "__main__":
    raise SystemExit(fire.Fire(send) or 0)
```

`fire.Fire` returns the handler's value to the caller and sets no exit code `[H-16]`, so the
`raise SystemExit(... or 0)` is mandatory. `fire` also prints a returned value; returning an `int`
means the code appears twice on stdout unless the body prints nothing.

fire converts in neither direction: an exception inside the handler propagates out of
`fire.Fire` instead of becoming an exit code, and a returned tuple comes back verbatim
(`ret=('a', 1)`) `[H-27]`. The adapter therefore owns both ends — catch `DIError` and the
teardown tuple inside the handler, return the code, and let `SystemExit` do the conversion.

### 5.9 Reference implementation of the contract

The following fence is the executable form of §1.2 and is run by the documentation tests.

```python run
import asyncio

from lauren_di import DIContainer, Scope, injectable, pre_destruct


@injectable()
class Greeting:
    def text(self) -> str:
        return "hello"


@injectable(scope=Scope.REQUEST)
class Session:
    def __init__(self) -> None:
        self.closed = False

    @pre_destruct
    async def _close(self) -> None:
        self.closed = True


async def invoke(argv: list[str]) -> int:
    container = DIContainer()
    container.register(Greeting)
    container.register(Session)
    await container.startup()
    session = None
    try:
        async with container.scope() as scope:
            session = await scope.resolve(Session)
            greeting = await scope.resolve(Greeting)
            print(f"{greeting.text()} {argv[1]}")
    finally:
        errors = await container.shutdown()
    if session is not None and not session.closed:
        raise AssertionError("request scope was not closed")
    return 70 if errors else 0


assert asyncio.run(invoke(["prog", "world"])) == 0
```

### 5.10 Failure modes and workarounds

| Symptom | Cause | Workaround |
|---------|-------|------------|
| command exits 0, nothing happened | `async def` command on Typer `[H-10]` | make the command `def`, call `asyncio.run` inside |
| `Got unexpected extra arguments` / exit 2 | injected parameter became a CLI argument `[H-11]` | resolve inside the body; keep the signature clean |
| `CoercionError: Invalid value for …` | same, on cyclopts `[H-15]` | same |
| exit 0 but the connection stayed open | `await container.resolve(T)` outside a scope `[H-20]` | resolve from `scope`, never from the container |
| teardown failure invisible | `shutdown()` returns a tuple, it never raises `[H-23]` | iterate the tuple, print, exit 70 |
| nothing cleaned up on `kill` | SIGTERM default handler `[H-18]` | install a handler raising `SystemExit(143)` `[H-19]` |
| bare traceback for a DI problem | `DIError` not caught at the entry point `[H-6]` | catch `DIError`, print one line, exit 70 |
| value printed twice | fire prints the return value `[H-16]` | return an `int`, print nothing else |

### 5.11 Overrides in host tests

```python fragment
import asyncio

from click.testing import CliRunner
from lauren_di import OverridePlan, ProviderOverride, building_with


def test_send_uses_the_fake_mailer() -> None:
    plan = OverridePlan({Mailer: ProviderOverride(Mailer, "value", value=FakeMailer())})
    with building_with(plan):
        container = build_container_with(overrides=plan)

    result = CliRunner().invoke(cli, ["send", "a@b"], obj={"container": container})
    assert result.exit_code == 0
    assert "fake" in result.output
```

`OverridePlan` takes a mapping from token to `ProviderOverride`; `building_with(plan)` activates
it for the container built inside the block `[H-25]`. The CLI wiring is untouched, and
`CliRunner` isolates `ctx.obj` per invocation `[H-6, H-12]`.

---

## 6. Architecture Fit & Feasibility

### 6.1 Layering

```text
host process  (argparse | click | Typer | cyclopts | fire)
      │  owns: argv, exit code, signals, the event loop
      ▼
adapter       (this page; user code or a future separate distribution)
      │  owns: container instance, startup/shutdown, scope, teardown reporting
      ▼
container     (lauren-di: DIContainer, scope(), resolve(), DestructError)
```

The arrow only points down. The adapter may import both sides; the container imports neither.
That is the whole of `D-57` and `D-63` for this host family, and it is why every recipe in §5 is
copy-paste user code rather than a shipped module. `A-2` is `D-60` read as a CLI rule: nothing runs
before `startup()` returns. `A-3` is `D-61`: the adapter, not the library, owns the scope's
lifetime, and on this host the scope's lifetime is one command body.

### 6.2 What the adapter needs from the library, and what it must not ask for

| Needs (public, measured) | Must not ask for |
|---|---|
| `await container.startup()` `[H-22]` | an ambient scope or `ContextVar` carrier `D-58` |
| `await container.shutdown()` returning `tuple[DestructError, ...]` `[H-23]` | `shutdown()` to raise the first error |
| `async with container.scope()` `[H-21]` | a scope that closes itself at interpreter exit |
| `await scope.resolve(token)` `[H-21]` | resolution from the command signature `[H-11]` |
| `DestructError.code`, `.message`, `.detail` `[H-23]` | a new error leaf for CLI hosts (§7) |
| `OverridePlan`, `ProviderOverride`, `building_with` `[H-25]` | a test-only reset hook on the container |
| `DIError` as the catch-all base | a framework-aware exception type |

The adapter contract ids in PRD 09 §5.1 (`IC-1 … IC-12`) apply unchanged; §5.2 is the per-host
mapping of that contract.

### 6.3 Risks

| Risk | Likelihood | Mitigation |
|---|---|---|
| Typer starts awaiting `async def` commands upstream | medium | the recipe is sync today; if upstream changes, the sync recipe still works `[H-10]` |
| cyclopts changes return-value-to-exit-code behaviour | low | the recipe returns an explicit `int` `[H-13]` |
| fire changes how a returned value is printed | low | return an `int`, never a payload `[H-16]` |
| a container reused across invocations caches a loop-bound object | medium | `A-12`: one container per invocation unless every provider is loop-free; measured reuse over 50 loops raised nothing for loop-free providers `[H-24]` |
| teardown failure swallowed by a test runner | medium | `A-5`: the tuple is the exit code, asserted in `T14-07` |
| SIGTERM kills the process with no cleanup | high | `A-10` handler `[H-18, H-19]` |
| per-invocation cost dominates a fast command | low | 1.3–1.4 ms fresh / 0.6–0.8 ms reused for a clean two-provider container `[H-24]` |

### 6.4 Cost

Measured during authoring on Python 3.13.13, 50 iterations, working-tree `lauren-di` `[H-24]`.
Two throwaway timing scripts measured two container shapes, and the difference matters more
than the absolute numbers — but note the honesty caveat under the table, because these are
the one set of figures on this page that no shipped artifact reproduces:

| Shape | `asyncio.run(noop())` | fresh container | one container reused |
|---|---|---|---|
| two providers, clean teardown | 190–194 µs | 1326–1421 µs | 655–659 µs |
| three providers, a `pre_destruct` that raises | 180–220 µs | 2613–2929 µs | 1459–1496 µs |

Ranges are across re-runs on the same machine; the 3× spread between the two shapes is not
noise — the third provider and the failing-teardown path double the cost.

**These microsecond figures are authoring-time only.** The scripts that produced them were
never committed, no test asserts a timing, and this repository has no timing gate — so treat
the shape as the durable claim (a container costs one to three milliseconds, and most of it
is singleton construction) and the numbers as a snapshot of one machine. `nox -s examples`
re-runs the *behaviour* these figures were taken alongside (T14-03's
`started, built, closed, stopped` twice per process), not the microseconds.

`startup()` constructs singletons eagerly `[H-22]`, so the fresh-container cost scales with the
number of singleton providers, not with the number resolved. A CLI that runs for milliseconds
should build the container once per process and accept `A-12`'s constraint instead of paying
1.3–2.9 ms per invocation.

### 6.5 Evidence index

The authoring measurements came from nine throwaway scripts under `/tmp/compat/14/` that were
never committed, so the previous version of this table cited files no reader could open. What
the repository has instead is an executed example per framework, driven by `nox -s examples`,
and this table indexes each claim id to it.

| Framework | Claim ids the shipped tests assert | Versions measured |
|---|---|---|
| argparse | H-2 (`tests/test_argparse.py::test_missing_command_exits_2_and_starts_no_container`), H-4 (`::test_ctrl_c_closes_the_scope_exactly_once` — the `finally` half; the example exits 130 where the bare interpreter gave -2), H-20 (`::test_request_scoped_token_is_not_resolvable_on_the_container`), H-22 / H-23 (`::test_startup_runs_before_the_body_and_shutdown_after`, `::test_shutdown_failure_exits_70_and_reports_on_stderr`) | argparse, CPython 3.13.13 |
| click | H-6 (`tests/test_click.py::test_real_process_exits_1_when_the_body_raises`), H-12 / H-25 (`::test_override_plan_replaces_the_mailer_under_the_runner`), H-17 (`::test_ctrl_c_closes_the_scope_exactly_once`), H-19 (`::test_sigterm_closes_the_scope_before_the_process_exits`), H-22 / H-23 (`::test_startup_runs_before_the_body_and_shutdown_after`, `::test_shutdown_failure_exits_70_and_reports_on_stderr`) | click 8.5.1 |
| Typer | H-10 (`tests/test_typer.py::test_async_command_never_runs_the_body`), H-11 (`::test_annotated_injection_is_rejected_before_the_body`), H-26 (`::test_one_command_app_runs_the_body_on_empty_argv`), H-22 / H-23 (`::test_startup_runs_before_the_body_and_shutdown_after`, `::test_shutdown_failure_exits_70_and_reports_on_stderr`) | typer 0.29.2, click 8.5.1 |
| cyclopts | H-14 (`tests/test_cyclopts.py::test_async_command_exit_code_comes_from_its_return_value`), H-15 (`::test_annotated_injection_is_a_coercion_error_before_the_body`), H-22 / H-23 (`::test_startup_runs_before_the_body_and_shutdown_after`, `::test_shutdown_failure_exits_70_and_reports_on_stderr`) | cyclopts 5.5.2 |
| fire | H-16 (`tests/test_fire.py::test_fire_prints_a_value_built_after_the_close_point`), H-27 exception half (`::test_body_exception_propagates_but_the_scope_still_closed`), H-22 / H-23 (`::test_startup_runs_before_the_body_and_shutdown_after`, `::test_shutdown_failure_exits_70_and_reports_on_stderr`) | fire 0.7.1 |

Paths are relative to the app — `tests/test_argparse.py` is
`examples/apps/argparse/tests/test_argparse.py`. Versions are pinned in
`examples/apps/*/requirements.txt`: click 8.5.1, typer 0.29.2 (with click pinned beside it,
for the reason recorded in that file), cyclopts 5.5.2, fire 0.7.1, argparse from the
interpreter.

**Claim ids with no shipped assertion**, and why — these are observations about what the
framework does, recorded once at authoring time and not re-run by any gate. They are listed
rather than dropped so a reader can tell "we measured this" from "we test this":

| Id | Claim | Why no test |
|---|---|---|
| H-1, H-5, H-9, H-13 | the handler's return value is discarded (argparse, click) or becomes the exit code (Typer discards, cyclopts converts) | every example adapter owns the exit code itself, so the framework's default is neutralised rather than depended on |
| H-3 | argparse accepts a handler returning a coroutine and never awaits it | the examples never return a bare coroutine from a handler; T14-04 covers the same trap on Typer |
| H-7, H-8 | `ctx.exit(4)` and the `body → finally → call_on_close` order | the adapters exit through `SystemExit`/`typer.Exit`; T14-02 and T14-07 assert the codes that matter |
| H-18 | click's default SIGTERM handler kills the process with no cleanup | asserting "nothing ran" would pass for the wrong reason on any host; T14-14 asserts the handler side instead |
| H-21 | one `REQUEST` instance per `async with container.scope()` | covered at the library level (T09-03, T09-17); the CLI tests assert the adapter never bypasses the scope (T14-10) |
| H-24 | the microsecond table in §6.4 | no timing gate in this repository — see §6.4's caveat |

---

## 7. Error Catalog Impact

**No new error leaf.** The 14 existing leaves cover every outcome this host family produces; the
adapter maps them to a message, an exit code and one stderr line. Exit codes: `2` is the host's own
usage error, `70` is a DI or teardown failure, `130`/`143` are the signal conventions from `A-10`.

| `DIError` leaf | Host-visible outcome |
|---|---|
| `MissingProviderError` | stderr `di <code>: <message>`, exit 70 |
| `DuplicateBindingError` | stderr line, exit 70 |
| `InvalidProviderError` | stderr line, exit 70 |
| `DecoratorUsageError` | stderr line, exit 70 (usually at import, before the parser runs) |
| `MetadataInheritanceError` | stderr line, exit 70 |
| `LifecycleConfigError` | stderr line, exit 70 |
| `LifecycleViolationError` | stderr line, exit 70 |
| `DIScopeViolationError` | stderr line, exit 70; raised before the body, so no scope is open |
| `CircularDependencyError` | stderr line, exit 70 |
| `ProtocolAmbiguityError` | stderr line, exit 70 |
| `UnresolvableParameterError` | stderr line, exit 70 |
| `OverrideError` | stderr line, exit 70 (test builds only) |
| `DestructError` | stderr `teardown destruct_error: <message>`, exit 70; the tuple may hold several, all printed, the process exits once |
| `DIError` (base) | stderr line, exit 70 — the catch-all at the entry point |

No status code and no connection close apply: this host has no response object. The "close" in
this page is always a scope exit or `shutdown()`, never a container `close` method — the container
has `shutdown()` and nothing else.

---

## 8. Phased Implementation Plan

No library change in any phase.

| Phase | Work | Exit criterion |
|---|---|---|
| 8.1 | Docs page `docs/guides/cli.md` with the five recipes, all `fragment`-fenced | page renders; every fence compiles |
| 8.2 | Example app `examples/cli_click/` and `examples/cli_cyclopts/` | `python -m examples.cli_click --help` exits 0; a failing teardown exits 70 |
| 8.3 | Skip-guarded integration tests for all five hosts | tests SKIP when the framework is absent, PASS when present `D-62` |
| 8.4 | Wire the page into the index | `prds/README.md` lists this page; the two expected gate lines disappear |

---

## 9. Documentation & Convention Updates

| Document | Change |
|---|---|
| `docs/guides/cli.md` | new page: the five recipes from §5, the failure-mode table from §5.10, the exit-code table from §7 |
| `docs/guides/scopes.md` | one paragraph: on a CLI host the process is the container lifetime and the command body is the unit of work `[H-20, H-21]` |
| `docs/reference.md` | unchanged — no new public name |
| `docs/guides/testing.md` | one section: `OverridePlan` + `building_with` inside `CliRunner` `[H-25]` |

Convention that keeps the samples honest: every snippet on the CLI page is `fragment`-fenced,
because none of the five frameworks is a dependency of the library and the documentation test only
has stdlib plus `lauren-di` `A-11`. The single executable fence in this page (§5.9) imports
`lauren-di` only.

---

## 10. Test Plan

Every row is skip-guarded: when the framework is absent the test SKIPs and never PASSes `D-62`.

| Id | Fixture | Action | Expected observation |
|---|---|---|---|
| T14-01 | `argparse` | run `main(["prog"])` with a missing positional | exit 2, message on stderr, no container started |
| T14-02 | `argparse` | run a command whose teardown raises | exit 70, one `teardown destruct_error:` line, remaining destructors ran |
| T14-03 | `click` | invoke a command that resolves a service | exit 0, `startup()` ran before the body, `shutdown()` after it |
| T14-04 | `Typer` | invoke a command declared `async def` | documented as unsupported: body never runs, exit 0 `[H-10]` — the recipe must use `def` |
| T14-05 | `click` | body raises `RuntimeError` | `CliRunner` exit 1 with `result.exception`; real process rc 1 `[H-6]` |
| T14-06 | all five | Ctrl-C during the body | `finally` ran, scope closed once, exit non-zero `[H-17, H-4]` |
| T14-07 | `lauren-di` | `shutdown()` returns one `DestructError` | exit code 70, stderr line carries `destruct_error` and the message `[H-23]` |
| T14-08 | `Typer` | command with `Annotated[Mailer, Inject(Mailer)]` | documented failure: exit 2, `{mailer}` in usage `[H-11]` |
| T14-09 | `cyclopts` | command with the same parameter | documented failure: `CoercionError` `[H-15]` |
| T14-10 | `lauren-di` | resolve a request-scoped token on the container twice | two instances, no teardown — the test asserts the adapter never does this `[H-20]` |
| T14-11 | `click` | `OverridePlan` + `building_with` + `CliRunner` | the fake provider is used, exit 0 `[H-25]` |
| T14-12 | `cyclopts` | `async def` command returning 9 | exit 9 `[H-13, H-14]` |
| T14-13 | docs | every `python` fence on the CLI page | compiles and carries `run`/`fragment`/`skip` |
| T14-14 | all five | SIGTERM with `A-10` handler installed | `finally` ran, exit 143 `[H-19]` |
| T14-15 | `Typer` | one-command app under `CliRunner` | `invoke(app, [])` runs the body and sees `ctx.obj`; `invoke(app, ["send"])` fails with exit 2 `[H-26]` |

### 10.1 Acceptance traceability

| Goal | Tests |
|---|---|
| G1 | T14-03, T14-04 |
| G2 | T14-05, T14-06, T14-10 |
| G3 | T14-02, T14-07 |
| G4 | T14-08, T14-09 |
| G5 | T14-02, T14-07 |
| G6 | T14-11 |
| G7 | T14-13 |
