# `typer` — `lauren_di` with Typer

A single-file example of hosting `lauren_di` inside Typer. It is the cli page of
PRD 14, reduced to one app, one adapter module and one test module.

## The adapter, in three lines

| Step | Where | What it does |
| --- | --- | --- |
| start | `main()` | builds the container, registers providers, awaits `startup()` before the host accepts work (IC-1) |
| open | a Typer command with the same `finally` close point | opens one `ResolutionScope` per unit of work, after the host bound its own state (IC-3) |
| close | after the command body returns, before the process exits | closes it exactly once, in a `finally` (IC-7), and reports `scope.errors` (IC-8) |

## Files

| File | Purpose |
| --- | --- |
| `typer_app.py` | the runnable app: providers, one route or command, and the entry point |
| `typer_di.py` | the adapter: the host hook, nothing else |
| `tests/test_typer.py` | the tests; they skip when Typer is not installed (D-62) |

Both modules import the shared, framework-free reference adapter from
[`../_host.py`](../_host.py): `build_container`, `HostUnit` and — for a
synchronous host — `SyncBridge`.

## What this host forces

Typer wraps click, so the adapter body is shared; only registration differs.

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
# the tests (skipped, with a reason, when Typer is absent)
python -m pytest examples/apps/typer -q

# every app whose host can be installed here
nox -s examples

# install the host this app pins
pip install -c examples/apps/constraints.txt -r examples/apps/typer/requirements.txt
```

The test client is `typer.testing.CliRunner`.

## Run the app

```bash
python examples/apps/typer/typer_app.py ada                 # 0: prints the greeting
python examples/apps/typer/typer_app.py --mode fragile      # 70: scope teardown failed
python examples/apps/typer/typer_app.py --mode stubborn     # 70: shutdown() teardown failed
python examples/apps/typer/typer_app.py --mode boom         # 1: traceback, scope still closed
python examples/apps/typer/typer_app.py --hold 10           # SIGTERM here: 143
```

A single-command `Typer` app collapses, so `invoke(app, [])` runs the body
(T14-15); the command takes a NAME argument plus `--mode`/`--hold` instead of
sub-commands.

## Where the close point sits

Typer wraps click, so the body is the click one and the exit paths are identical:
the scope closes inside `with host.invocation(...)`, before Typer's `SystemExit`.
Two Typer-specific rows matter:

* **A command must be declared `def`.** Typer calls an `async def` command and
  drops the coroutine, so the body never runs and the process still exits 0
  (T14-04). `build_async_app()` is that shape, kept for the test.
* **A parameter must not be a DI token.** `Annotated[Mailer, Inject(Mailer)]` is
  bound as a CLI parameter, and Typer 0.29.2 raises `RuntimeError: Type not yet
  supported` while it builds the command (T14-08). PRD 14 records exit 2 with
  `{mailer}` in the usage line; the installed version fails earlier, and
  `build_annotated_app()` pins that behaviour instead of pretending otherwise.
