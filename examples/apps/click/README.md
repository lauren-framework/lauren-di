# `click` — `lauren_di` with click

A single-file example of hosting `lauren_di` inside click. It is the cli page of
PRD 14, reduced to one app, one adapter module and one test module.

## The adapter, in three lines

| Step | Where | What it does |
| --- | --- | --- |
| start | `main()` | builds the container, registers providers, awaits `startup()` before the host accepts work (IC-1) |
| open | a `@click.command` whose body opens the scope, with `finally` as the close point | opens one `ResolutionScope` per unit of work, after the host bound its own state (IC-3) |
| close | after the command body returns, before `SystemExit` | closes it exactly once, in a `finally` (IC-7), and reports `scope.errors` (IC-8) |

## Files

| File | Purpose |
| --- | --- |
| `click_app.py` | the runnable app: providers, one route or command, and the entry point |
| `click_di.py` | the adapter: the host hook, nothing else |
| `tests/test_click.py` | the tests; they skip when click is not installed (D-62) |

Both modules import the shared, framework-free reference adapter from
[`../_host.py`](../_host.py): `build_container`, `HostUnit` and — for a
synchronous host — `SyncBridge`.

## What this host forces

`click` 8.5.1 keeps the invocation object per run, so `ctx.obj` carries the unit.

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
# the tests (skipped, with a reason, when click is absent)
python -m pytest examples/apps/click -q

# every app whose host can be installed here
nox -s examples

# install the host this app pins
pip install -r examples/apps/click/requirements.txt
```

The test client is `click.testing.CliRunner`.

## Run the app

```bash
python examples/apps/click/click_app.py greet ada    # 0: prints the greeting
python examples/apps/click/click_app.py fragile      # 70: scope teardown failed
python examples/apps/click/click_app.py stubborn     # 70: shutdown() teardown failed
python examples/apps/click/click_app.py boom         # 1: traceback, scope still closed
python examples/apps/click/click_app.py              # 2: usage error, no container
python examples/apps/click/click_app.py hold 10      # SIGTERM here: 143, scope closed
```

## Where the close point sits

The command body owns the whole adapter: `with host.invocation(...)` starts the
container, opens the one unit, and closes it in a `finally`. Every way click can
leave a command therefore meets the close point *before* click acts on it:

| exit path | what runs first |
|---|---|
| the body returns | the scope closes, then click exits 0 |
| the body raises | the scope closes, then click reports the traceback (rc 1) |
| a teardown raises | the scope closes, the error is captured in `scope.errors`, one `destruct_error:` line goes to **stderr**, and the host raises `SystemExit(70)` |
| a usage error | `parse_args` fails before the body, so no container is built at all |
| Ctrl-C / SIGTERM | `KeyboardInterrupt` / `Terminated` unwinds the `finally` (rc 130 / 143) |

`result.output` mixes stdout and stderr in click 8.5, so `tests/test_click.py`
asserts on `result.stdout` and `result.stderr` separately — a teardown failure
must never appear in the command's own output.
