# `argparse` — `lauren_di` with argparse

A single-file example of hosting `lauren_di` inside argparse. It is the cli page of
PRD 14, reduced to one app, one adapter module and one test module.

## The adapter, in three lines

| Step | Where | What it does |
| --- | --- | --- |
| start | `main()` | builds the container, registers providers, awaits `startup()` before the host accepts work (IC-1) |
| open | the command function opens the scope after parsing and closes it before returning | opens one `ResolutionScope` per unit of work, after the host bound its own state (IC-3) |
| close | after the command body, before the process exits | closes it exactly once, in a `finally` (IC-7), and reports `scope.errors` (IC-8) |

## Files

| File | Purpose |
| --- | --- |
| `argparse_app.py` | the runnable app: providers, one route or command, and the entry point |
| `argparse_di.py` | the adapter: the host hook, nothing else |
| `tests/test_argparse.py` | the tests; they skip when argparse is not installed (D-62) |

Both modules import the shared, framework-free reference adapter from
[`../_host.py`](../_host.py): `build_container`, `HostUnit` and — for a
synchronous host — `SyncBridge`.

## What this host forces

Standard library only: this app proves the contract needs no third-party host.

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
# the tests (skipped, with a reason, when argparse is absent)
python -m pytest examples/apps/argparse -q

# every app whose host can be installed here
nox -s examples

# install the host this app pins
pip install -c examples/apps/constraints.txt -r examples/apps/argparse/requirements.txt
```

The test client is `argparse.ArgumentParser.parse_args` with an explicit `argv`.

## Run the app

```bash
python examples/apps/argparse/argparse_app.py greet ada    # 0: prints the greeting
python examples/apps/argparse/argparse_app.py fragile      # 70: scope teardown failed
python examples/apps/argparse/argparse_app.py stubborn     # 70: shutdown() teardown failed
python examples/apps/argparse/argparse_app.py boom         # 1: traceback, scope still closed
python examples/apps/argparse/argparse_app.py              # 2: usage error, no container
python examples/apps/argparse/argparse_app.py hold 10      # SIGTERM here: 143, scope closed
```

## Where the close point sits

`main(argv)` returns the exit code instead of raising it, because argparse's own
exit has two halves and only one of them may start a container:

| exit path | what runs first |
|---|---|
| `parse_args` fails (`main(["prog"])`) | nothing: the code is returned as 2 and no container is built |
| the handler returns | the scope closes and `shutdown()` runs, then `sys.exit(main())` |
| the handler raises | the scope closes, then the traceback escapes `main` (rc 1) |
| a teardown raises | the error is captured, one `destruct_error:` line goes to **stderr**, and the handler's `SystemExit(70)` is turned back into a return value |
| Ctrl-C / SIGTERM | `KeyboardInterrupt` / `Terminated` unwinds the `finally` and is returned as 130 / 143 |

`main` takes the *full* argv (program name included), which is why
`main(["prog"])` means "prog with no command" — the T14-01 usage error.
