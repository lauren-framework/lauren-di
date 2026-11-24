# Hosting `lauren_di` in a CLI

A command-line program is the simplest host there is, and the easiest one to get
wrong. There is no server loop to hang the container on, no request object to
store a scope in, and no framework that will close anything for you when the
command finishes. What a CLI host has instead is a process lifetime and an exit
code, and both decide whether your `@pre_destruct` hooks run.

The shape is the same in all five recipes below: the **process** is the
container lifetime and the **command body** is the unit of work. Everything else
is the host's dialect.

This page is the CLI member of the hosting family — see
[Hosting in a framework](hosting.md) for the framework-neutral contract
(`IC-1`…`IC-12`) these recipes specialise.

## What changes, and what does not

| Question | A web host answers | A CLI host answers |
| --- | --- | --- |
| container lifetime | the app object | the process, built inside the entry point, never at import |
| unit of work | one request | one command invocation |
| where the scope lives | the request's own state | a local variable, or `ctx.obj` on click and Typer |
| the close point | after the body is streamed | after the command body returns, before the process exits |
| how failure is reported | status code | exit code: `0`, `2` for usage, `70` for DI or teardown |
| what "shutdown" means | `await container.shutdown()` on app stop | the same call, in a `finally`, once |

Two rules carry most of the weight. Build the container **inside** the entry
point (`A-1`): an import-time container is built before the parser has even
validated the arguments, and its loop-bound singletons outlive the loop. And put
`shutdown()` in a `finally` (`A-2`): the command body raises, the parser exits,
the user hits Ctrl-C — the container still has to stop.

## The shared shape

One helper carries the contract for every host. Each recipe below is a thin
wrapper around it:

```python fragment
import asyncio
import sys
from collections.abc import Awaitable, Callable

from lauren_di import DIContainer, DestructError, injectable


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
    """One command invocation: start, one scope, one shutdown, one exit code."""
    errors: tuple[DestructError, ...] = ()
    await container.startup()
    try:
        async with container.scope() as scope:
            await body(scope)
    finally:
        errors = await container.shutdown()
    for error in errors:
        print(f"teardown {error.code}: {error.message}", file=sys.stderr)
    return 70 if errors else 0
```

Three details in that helper are the contract, not style.

`shutdown()` **returns** its failures as a tuple; it never raises (`A-5`,
`A-7`). A `finally` that ignores the return value loses every teardown error, so
the caller iterates, prints one line per `DestructError`, and converts the tuple
to exit code `70`. A process that printed a teardown failure and exited `0`
tells its caller that nothing went wrong.

The body receives the **scope**, never the container (`A-3`). `await
container.resolve(T)` outside a scope bypasses the scope cache, builds a second
instance, and never tears it down — the failure shows up later as a connection
that stayed open, long after the command exited `0`.

One container serves one invocation (`A-12`). Reusing it is safe only when every
provider is known to be loop-free, and that is a property of the providers, not
of the host.

## The five recipes

### argparse: the exit code needs `SystemExit`

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

`argparse` discards the handler's return value, so returning `70` from `main`
exits `0`. The `raise SystemExit(main())` at the bottom is the adapter. On
Ctrl-C the interpreter unwinds the stack through `run_invocation`, so the
`finally` runs and the scope closes; the shell still reports the signal, not a
clean exit.

### click: the container in `ctx.obj`, the code in `ctx.exit`

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

`ctx.obj` is the right home for the container: click keeps one invocation
object per run, the group callback's assignment wins over whatever object the
runner was given, and `@click.pass_obj` sees it too.

`ctx.call_on_close` is **not** the close point. Teardown inside
`run_invocation`'s `finally` runs before it, so a `call_on_close` hook that also
shuts the container down stops it twice. Keep one only as a safety net for the
paths that never reach the body — a usage error, for instance — and make it
idempotent.

`ctx.exit(code)` raises `SystemExit(code)`; a plain `return` from the command
exits `0`.

### Typer: a `def` command, and no injected parameters

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

The command is `def`, never `async def`. Typer accepts an `async def` command
and never awaits it: the body is skipped, the process exits `0`, and the only
trace is a `RuntimeWarning` that says the coroutine was never awaited. Exit `0`
with no work done is the worst failure mode on this page, because the caller
believes it succeeded.

No parameter is ever injected. Declaring `Annotated[Mailer, Inject(Mailer)]`
makes Typer publish the parameter as a command-line **argument**, so the command
fails before it runs:

```text
Usage: injected [OPTIONS] {mailer}
Try 'injected --help' for help.

Error: Missing argument 'mailer'.
```

That is exit `2` — the host's own usage error, indistinguishable from a typo in
the user's command line. Resolve every service inside the body and keep the
signature clean (`A-6`). The same mistake on cyclopts surfaces as a
`CoercionError` instead, which is at least visible.

A one-command `Typer()` app is not a group, so the command name is not parsed as
a subcommand: `CliRunner().invoke(app, [])` runs the body and sees `ctx.obj`,
while `invoke(app, ["send"])` feeds `"send"` to the first parameter and exits
`2`. A second command turns the app into a group and restores the usual
behaviour.

### cyclopts: the only host that awaits the command

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

cyclopts awaits an `async def` command and uses its return value as the exit
code, so this recipe needs neither `asyncio.run` nor a `SystemExit` wrapper: the
`int` the command returns is the exit status. Reaching for `asyncio.run` here
would nest a second loop inside the one already running the command.

### fire: `Fire` returns, it does not exit

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

`fire.Fire` hands the handler's return value back to the caller and sets no exit
code of its own, so `raise SystemExit(... or 0)` is mandatory — without it a
teardown failure returns `70` into a process that exits `0`.

fire converts in neither direction. An exception inside the handler propagates
out of `fire.Fire` as a traceback rather than becoming an exit code, and a
returned tuple comes back verbatim. It also **prints** a returned value, so
returning the code while also printing status means the number appears twice on
stdout; print nothing extra and return the `int`.

## Exit codes

| Code | Meaning | Source |
| --- | --- | --- |
| `0` | the command ran and every teardown succeeded | `shutdown()` returned `()` |
| `2` | the host rejected the command line | the parser's own usage error |
| `70` | a `DIError`, or a non-empty teardown tuple | `A-5`, `A-8` |
| `130` | interrupted | `SIGINT`, after `finally` ran |
| `143` | terminated | `SIGTERM` with the adapter's handler installed |

`70` is the whole DI story: a `MissingProviderError`, a scope violation, an
unresolvable parameter and a failed destructor all leave through the same door,
one line on stderr, one exit code. The message carries the error's `code` and
its text so a script can tell them apart without parsing a traceback.

Signals deserve their own line. The default `SIGTERM` handler kills the process
without unwinding, so no `finally` runs and nothing is cleaned up. Where the
host allows it, install a handler that raises `SystemExit(143)`; the exception
unwinds normally, the scope closes once, and the exit code still says
"terminated". `SIGINT` needs no help — Python already turns it into
`KeyboardInterrupt`.

## Failure modes

| Symptom | Cause | Fix |
| --- | --- | --- |
| exits `0`, nothing happened | `async def` command on Typer | make the command `def`, call `asyncio.run` inside |
| `Missing argument '…'` or `Got unexpected extra arguments`, exit `2` | an injected parameter became a CLI argument | resolve inside the body, keep the signature clean |
| `CoercionError: Invalid value for …` | the same mistake on cyclopts | the same fix |
| exit `0` but the connection stayed open | `await container.resolve(T)` outside a scope | resolve from `scope`, never from the container |
| a teardown failure printed nothing | `shutdown()`'s tuple was ignored | iterate it, print, exit `70` |
| nothing cleaned up on `kill` | the default `SIGTERM` handler | install a handler raising `SystemExit(143)` |
| a bare traceback for a DI problem | `DIError` not caught at the entry point | catch it, print one line, exit `70` |
| the value printed twice | fire prints the return value | return an `int`, print nothing else |

## The contract, executable

Everything above describes a host; this is the library side, and it runs on a
bare interpreter with no CLI framework installed. It is the same
startup → scope → shutdown order the recipes use:

<!-- docs-contract: run -->
```python
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

The `assert` is the point of the block: it fails if the request-scoped
`Session` outlives its scope, which is the one bug a CLI host hides best.

## Testing a command

`click.testing.CliRunner` and `typer.testing.CliRunner` capture the exit code,
stdout and the exception, and isolate `ctx.obj` per invocation, so two
invocations in one test start from two containers. cyclopts' `App(...)` object
is callable and accepts `exit_on_error=False`; `argparse` and fire are tested by
calling `main(argv)` and the wrapped function directly.

To swap a provider for a test, build the container with an `OverridePlan` and
run the command against it — see [Testing and overrides](testing.md). The CLI
wiring never changes: `CliRunner().invoke(cli, [...], obj={"container":
container})` receives the overridden container through the same `ctx.obj` the
real group callback writes.

Every one of these tests is skip-guarded (`D-62`): with the framework absent the
test skips with a reason, and a skip is never reported as a pass.

## Where the runnable apps live

Each of the five hosts has a complete app and a test module —
[`examples/apps/`](https://github.com/lauren-framework/lauren-di/blob/main/examples/apps/README.md)
lists all sixteen host examples. The CLI ones are
[`argparse`](https://github.com/lauren-framework/lauren-di/tree/main/examples/apps/argparse),
[`click`](https://github.com/lauren-framework/lauren-di/tree/main/examples/apps/click),
[`typer`](https://github.com/lauren-framework/lauren-di/tree/main/examples/apps/typer),
[`cyclopts`](https://github.com/lauren-framework/lauren-di/tree/main/examples/apps/cyclopts)
and
[`fire`](https://github.com/lauren-framework/lauren-di/tree/main/examples/apps/fire).

```bash
python -m pytest examples/apps/click -q   # one host; skips if click is absent
nox -s examples                           # every host whose package installs here
```
