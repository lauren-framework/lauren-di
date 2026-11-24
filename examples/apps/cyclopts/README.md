# `cyclopts` — `lauren_di` with cyclopts

A single-file example of hosting `lauren_di` inside cyclopts. It is the cli page of
PRD 14, reduced to one app, one adapter module and one test module.

## The adapter, in three lines

| Step | Where | What it does |
| --- | --- | --- |
| start | `main()` | builds the container, registers providers, awaits `startup()` before the host accepts work (IC-1) |
| open | an `App(default=...)` command that opens the scope in its body | opens one `ResolutionScope` per unit of work, after the host bound its own state (IC-3) |
| close | after the command body returns, before the process exits | closes it exactly once, in a `finally` (IC-7), and reports `scope.errors` (IC-8) |

## Files

| File | Purpose |
| --- | --- |
| `cyclopts_app.py` | the runnable app: providers, one route or command, and the entry point |
| `cyclopts_di.py` | the adapter: the host hook, nothing else |
| `tests/test_cyclopts.py` | the tests; they skip when cyclopts is not installed (D-62) |

Both modules import the shared, framework-free reference adapter from
[`../_host.py`](../_host.py): `build_container`, `HostUnit` and — for a
synchronous host — `SyncBridge`.

## What this host forces

cyclopts raises its own exit exception, so the close point is still the command body.

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
# the tests (skipped, with a reason, when cyclopts is absent)
python -m pytest examples/apps/cyclopts -q

# every app whose host can be installed here
nox -s examples

# install the host this app pins
pip install -r examples/apps/cyclopts/requirements.txt
```

The test client is `cyclopts.App.__call__` with an explicit `argv`.

## Run the app

```bash
python examples/apps/cyclopts/cyclopts_app.py ada               # 0: prints the greeting
python examples/apps/cyclopts/cyclopts_app.py --mode fragile    # 70: scope teardown failed
python examples/apps/cyclopts/cyclopts_app.py --mode stubborn   # 70: shutdown() teardown failed
python examples/apps/cyclopts/cyclopts_app.py --mode boom       # 1: traceback, scope still closed
python examples/apps/cyclopts/cyclopts_app.py --hold 10         # SIGTERM here: 143
```

## Where the close point sits

cyclopts owns the process exit: it awaits the command, takes its return value as
the exit code and calls `sys.exit` itself. That happens *after* the command
returned, so the close point inside `with host.invocation(...)` has already run —
including when the host raising `SystemExit(70)` is what cyclopts is exiting on.

| shape | observation |
|---|---|
| `async def` command returning 9 | cyclopts awaits it and the process exits 9 (T14-12); Typer cannot do this |
| `Annotated[Mailer, Inject(Mailer)]` parameter | cyclopts cannot coerce the token: it prints its error panel and exits 2 before any body (T14-09) |

Because cyclopts is willing to run a loop of its own, every container call here
goes through `CliHost` and the bridge's single long-lived loop instead.
