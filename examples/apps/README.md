# `lauren_di` with a host framework

Sixteen runnable apps, one per host, each showing the same three-step adapter:
**start the container once, open one scope per unit of work, close it once at the
transport's finished point.**

These are the executable form of PRD 09 (`prds/09-integration-contract-and-host-adapters.md`)
and its five host pages — [PRD 10](../../prds/10-asgi-web-frameworks.md) (ASGI),
[PRD 11](../../prds/11-async-web-frameworks.md) (async), [PRD 12](../../prds/12-wsgi-web-frameworks.md)
(WSGI), [PRD 13](../../prds/13-django-integration.md) (Django) and
[PRD 14](../../prds/14-cli-frameworks.md) (CLI). Every app is small on purpose: one
route or one command, one provider that is request-scoped, one that is a
singleton, and a teardown that proves it ran at the right moment.

`lauren_di` itself stays dependency-free: nothing here is imported by the
library, and no app is a runtime extra. The adapter is host code because the
contract says so (IC-12) — the library never learns a framework's name.

## The apps

| Host | Family | PRD | App | Install point |
| --- | --- | --- | --- | --- |
| [starlette](starlette/) | ASGI | 10 | pure ASGI middleware | `app.add_middleware(...)` — closes after the last body chunk |
| [fastapi](fastapi/) | ASGI | 10 | pure ASGI middleware + `app.state` | same, with `Depends` and `Inject` side by side |
| [litestar](litestar/) | async | 11 | `ASGIMiddleware` | owns the scope in the middleware |
| [quart](quart/) | async | 11 | wrapper around `app.asgi_app` | never `teardown_request`: it fires before the body |
| [sanic](sanic/) | async | 11 | request middleware + body hook | the response middleware alone is not a close point |
| [aiohttp](aiohttp/) | async | 11 | `@web.middleware` | closes after the handler, before the response is written |
| [flask](flask/) | WSGI | 12 | WSGI middleware around `app.wsgi_app` | closes on the response iterable's `close()` |
| [falcon](falcon/) | WSGI | 12 | WSGI middleware | closes on the response iterable's `close()` |
| [pyramid](pyramid/) | WSGI | 12 | WSGI middleware | closes on the response iterable's `close()` |
| [bottle](bottle/) | WSGI | 12 | WSGI middleware | closes on the response iterable's `close()` |
| [django](django/) | Django | 13 | `AppConfig.ready()` + a per-request decorator | after the response's `close()` |
| [argparse](argparse/) | CLI | 14 | one `parser.parse_args` per invocation | closes after the command body, before exit |
| [click](click/) | CLI | 14 | `@click.command` body | `finally`, before `SystemExit` |
| [typer](typer/) | CLI | 14 | Typer command body | the same body; only registration differs |
| [cyclopts](cyclopts/) | CLI | 14 | `App(default=...)` | after the command body, before exit |
| [fire](fire/) | CLI | 14 | `fire.Fire(fn)` | after the function returns, before Fire prints |

## Layout

Every app has the same four files, and the names never collide across apps:

```text
examples/apps/<host>/
├── README.md              # what this host forces, and its close point
├── requirements.txt       # the host, pinned exactly (comment-only for argparse)
├── <host>_app.py          # providers, one route or command, the entry point
├── <host>_di.py           # the adapter: the host hook, and nothing else
└── tests/
    └── test_<host>.py     # skip-guarded tests (D-62)
```

The shared, framework-free pieces live one directory up:

| File | What it is |
| --- | --- |
| [`_host.py`](_host.py) | the reference adapter — `build_container`, `HostUnit`, `SyncBridge`, `SCOPE_KEY` |
| [`_testing.py`](_testing.py) | `require(distribution)` (skip when absent) and `load(app, module)` (import after the guard) |
| [`_pins.py`](_pins.py) | the recorded dependency resolution: `--write` regenerates `constraints.txt`, `--verify` checks the venv against it |
| [`constraints.txt`](constraints.txt) | every app's pins **and** the closure they resolve to, one version each, with the apps that reach it |
| [`conftest.py`](conftest.py) | puts the repository root and this directory on `sys.path`, appended never inserted |
| [`apps.json`](apps.json) | the manifest the tests and `nox -s examples` read |

## Why the resolution is recorded

Each app's `requirements.txt` names the host it demonstrates. That is not the
whole of what a test measures: the framework's *transitive* dependencies decide
the behaviour. The T14-08 failure is the case in point — Typer does not constrain
Click, so an unpinned Click in the shared `examples` venv silently chose whether
an injected parameter raised at build time or became a positional argument, and
the test had recorded a `RuntimeError` that no installed version raises. A green
test whose meaning floats on somebody else's pin is a test whose meaning is
unknown.

So [`constraints.txt`](constraints.txt) records the closure as measured, `nox -s
examples` installs with `-c` it, `_pins.py --verify` fails the session if the venv
drifted from it, and `tests/examples/test_apps_manifest.py` re-checks the same
relation from a bare checkout. An app that needs a transitive *named* — Werkzeug
for the close point, `multidict` for aiohttp's headers, `termcolor` for Fire's
output — says so in its own `requirements.txt` with the reason.

## Running them

```bash
# one app, once its host is installed
pip install -c examples/apps/constraints.txt -r examples/apps/flask/requirements.txt
python -m pytest examples/apps/flask -q

# every app that can be installed here, installed and run in one venv
nox -s examples

# a single host through the session
nox -s examples -- -k flask
```

Without the host installed, an app's tests **skip with a reason** that names its
`requirements.txt` — never silently pass, never fail on an `ImportError`.

## The rules every app keeps

These are the obligations of PRD 09 §5.1, and they are the same in all sixteen
apps. The [hosting guide](../../docs/guides/hosting.md) states them as prose;
`tests/integration/hosts/` checks them per adapter.

| # | Rule | Why |
| --- | --- | --- |
| IC-1 | one container per process, `await container.startup()` before the host accepts work | lazy first touch from two threads constructs twice |
| IC-2 | `strict_request_scope=True` unless the host has no `REQUEST` provider, and say which | the lax fallback resolves outside any scope and never tears down |
| IC-3 | one `ResolutionScope` per unit of work, opened after the host bound its state | values are fixed at open; an early scope cannot see the request |
| IC-4 | the scope lives in the host's own per-unit state under `SCOPE_KEY` | a module global is overwritten by the next unit |
| IC-5 | resolve through `scope.resolve`, never `container.resolve` | `container.resolve` bypasses the scope cache and builds a second instance |
| IC-6 | host-owned objects are `value_tokens=` supplied through `values=` | a singleton would capture the first request's object |
| IC-7 | close once, in a `finally`, at the finished point | "after the handler returns" is early for a streaming body |
| IC-8 | read `scope.errors` and report them; never raise during teardown | the response may already be committed |
| IC-9 | `await container.shutdown()` once, and report its tuple | a failed `@post_construct` leaves the instance cached |
| IC-10 | a sync host runs every container call on one long-lived loop (`SyncBridge`) | `asyncio.run` per request orphans loop-bound resources |
| IC-11 | overrides are applied at build time | mutating a live container is not a supported operation |
| IC-12 | the adapter imports the host; the library never imports the host | `dependencies = []` is a claim a user can verify |
