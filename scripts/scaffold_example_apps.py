"""Scaffold the per-app README/requirements files for ``examples/apps/``.

Run once while scaffolding the directory tree; it is not part of the test suite.
It exists so the sixteen apps share one wording for the contract and differ only
where the host does — the same discipline ``docs/guides/hosting.md`` follows.
"""

from __future__ import annotations

import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
APPS = ROOT / "examples" / "apps"

#: name, family, PRD, the host's install point, the host's close point, deps.
SPECS: list[dict[str, object]] = [
    {
        "name": "starlette",
        "family": "asgi",
        "prd": "10",
        "install": "a pure ASGI middleware added with `app.add_middleware(...)`",
        "close": "after `await app(scope, receive, send)` returns — after the last body chunk",
        "client": "`starlette.testclient.TestClient` (needs `httpx`)",
        "deps": ["starlette==1.10.0", "httpx==0.28.1"],
        "extra": "The lifespan handler runs `container.startup()` and `container.shutdown()`.",
    },
    {
        "name": "fastapi",
        "family": "asgi",
        "prd": "10",
        "install": "the same pure ASGI middleware, plus `app.state` for the container",
        "close": "after `await app(scope, receive, send)` returns — after the last body chunk",
        "client": "`fastapi.testclient.TestClient` (needs `httpx`)",
        "deps": ["fastapi==0.165.0", "httpx==0.28.1"],
        "extra": "`Depends` and `Annotated[T, Inject(token)]` are shown side by side.",
    },
    {
        "name": "litestar",
        "family": "async",
        "prd": "11",
        "install": "an `ASGIMiddleware` that owns the scope",
        "close": "after `await app(scope, receive, send)` returns inside the middleware",
        "client": "`litestar.testing.TestClient`",
        "deps": ["litestar==2.25.0", "httpx==0.28.1"],
        "extra": "Litestar passes `state` through the ASGI scope, so the scope object travels there.",
    },
    {
        "name": "quart",
        "family": "async",
        "prd": "11",
        "install": "a wrapper around `app.asgi_app` — never `teardown_request`",
        "close": "after the wrapped `asgi_app` returns, which is after the last chunk",
        "client": "`quart.testing.QuartClient`",
        "deps": ["quart==0.23.1"],
        "extra": "`teardown_request` fires before a streaming body is sent, so it is not a close point.",
    },
    {
        "name": "sanic",
        "family": "async",
        "prd": "11",
        "install": "the request middleware opens, the response middleware is *not* the close point",
        "close": "after the response has been sent, via Sanic's `response` middleware plus a body hook",
        "client": "`sanic_testing.TestClient` (needs `sanic-testing`)",
        "deps": ["sanic==25.12.1", "sanic-testing==24.6.0"],
        "extra": "PRD 11 §5.4 measures why the response middleware alone closes too early.",
    },
    {
        "name": "aiohttp",
        "family": "async",
        "prd": "11",
        "install": "a `@web.middleware` that opens the scope and closes it after the handler returns",
        "close": "in the middleware, after `await handler(request)` and before aiohttp writes the response",
        "client": "`aiohttp.test_utils.TestClient`",
        "deps": ["aiohttp==3.14.5"],
        "extra": "The handler owns the body; the middleware owns the scope (PRD 11 §5.5).",
    },
    {
        "name": "flask",
        "family": "wsgi",
        "prd": "12",
        "install": "a WSGI middleware wrapping `app.wsgi_app`",
        "close": "after the response iterable's `close()`, which the server calls after the last byte",
        "client": "`flask.testing.FlaskClient`",
        "deps": ["flask==3.1.3"],
        "extra": "`teardown_request` runs before the iterable is consumed; it is not the close point.",
    },
    {
        "name": "falcon",
        "family": "wsgi",
        "prd": "12",
        "install": "a WSGI middleware around the Falcon app",
        "close": "after the response iterable's `close()`",
        "client": "`falcon.testing.TestClient`",
        "deps": ["falcon==4.4.0"],
        "extra": "Falcon's `process_response` hook fires before the body is written.",
    },
    {
        "name": "pyramid",
        "family": "wsgi",
        "prd": "12",
        "install": "a WSGI middleware wrapped around the Pyramid application",
        "close": "after the response iterable's `close()`",
        "client": "`webtest.TestApp`",
        "deps": ["pyramid==2.1", "webtest==3.0.7"],
        "extra": "Pyramid's `request` object is per-request already; the scope rides on `environ`.",
    },
    {
        "name": "bottle",
        "family": "wsgi",
        "prd": "12",
        "install": "a WSGI middleware around the Bottle app's callable",
        "close": "after the response iterable's `close()`",
        "client": "the WSGI callable driven directly with an `environ` mapping",
        "deps": ["bottle==0.13.4"],
        "extra": "Bottle ships no test client, so the test drives the WSGI callable directly.",
    },
    {
        "name": "django",
        "family": "django",
        "prd": "13",
        "install": "a per-request decorator, not middleware — `AppConfig.ready()` starts the container",
        "close": "after the response's `close()`, so a streaming response outlives `get_response`",
        "client": "`django.test.Client`",
        "deps": ["django==6.1.3"],
        "extra": "`sync_to_async` does not copy a `ContextVar`, so the scope is passed explicitly.",
    },
    {
        "name": "argparse",
        "family": "cli",
        "prd": "14",
        "install": "the command function opens the scope after parsing and closes it before returning",
        "close": "after the command body, before the process exits",
        "client": "`argparse.ArgumentParser.parse_args` with an explicit `argv`",
        "deps": [],
        "extra": "Standard library only: this app proves the contract needs no third-party host.",
    },
    {
        "name": "click",
        "family": "cli",
        "prd": "14",
        "install": "a `@click.command` whose body opens the scope, with `finally` as the close point",
        "close": "after the command body returns, before `SystemExit`",
        "client": "`click.testing.CliRunner`",
        "deps": ["click==8.5.1"],
        "extra": "`click` 8.5.1 keeps the invocation object per run, so `ctx.obj` carries the unit.",
    },
    {
        "name": "typer",
        "family": "cli",
        "prd": "14",
        "install": "a Typer command with the same `finally` close point",
        "close": "after the command body returns, before the process exits",
        "client": "`typer.testing.CliRunner`",
        "deps": ["typer==0.29.2"],
        "extra": "Typer wraps click, so the adapter body is shared; only registration differs.",
    },
    {
        "name": "cyclopts",
        "family": "cli",
        "prd": "14",
        "install": "an `App(default=...)` command that opens the scope in its body",
        "close": "after the command body returns, before the process exits",
        "client": "`cyclopts.App.__call__` with an explicit `argv`",
        "deps": ["cyclopts==5.5.2"],
        "extra": "cyclopts raises its own exit exception, so the close point is still the command body.",
    },
    {
        "name": "fire",
        "family": "cli",
        "prd": "14",
        "install": "a plain function exposed through `fire.Fire`, with the scope opened in that function",
        "close": "after the function returns, before `fire` prints the result",
        "client": "calling the exposed function directly with an explicit argument list",
        "deps": ["fire==0.7.1"],
        "extra": "`fire` inspects signatures, so the function takes explicit arguments, not a request.",
    },
]

#: How each host is spelled in prose. The directory is the PyPI name; a README
#: that says "inside django" reads like a typo, so the display name is explicit.
DISPLAY = {
    "starlette": "Starlette",
    "fastapi": "FastAPI",
    "litestar": "Litestar",
    "quart": "Quart",
    "sanic": "Sanic",
    "aiohttp": "aiohttp",
    "flask": "Flask",
    "falcon": "Falcon",
    "pyramid": "Pyramid",
    "bottle": "Bottle",
    "django": "Django",
    "argparse": "argparse",
    "click": "click",
    "typer": "Typer",
    "cyclopts": "cyclopts",
    "fire": "Fire",
}

README = """# `{name}` — `lauren_di` with {display}

A single-file example of hosting `lauren_di` inside {display}. It is the {family} page of
PRD {prd}, reduced to one app, one adapter module and one test module.

## The adapter, in three lines

| Step | Where | What it does |
| --- | --- | --- |
| start | {start} | builds the container, registers providers, awaits `startup()` before the host accepts work (IC-1) |
| open | {install} | opens one `ResolutionScope` per unit of work, after the host bound its own state (IC-3) |
| close | {close} | closes it exactly once, in a `finally` (IC-7), and reports `scope.errors` (IC-8) |

## Files

| File | Purpose |
| --- | --- |
| `{name}_app.py` | the runnable app: providers, one route or command, and the entry point |
| `{name}_di.py` | the adapter: the host hook, nothing else |
| `tests/test_{name}.py` | the tests; they skip when {display} is not installed (D-62) |

Both modules import the shared, framework-free reference adapter from
[`../_host.py`](../_host.py): `build_container`, `HostUnit` and — for a
synchronous host — `SyncBridge`.

## What this host forces

{extra}

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
# the tests (skipped, with a reason, when {display} is absent)
python -m pytest examples/apps/{name} -q

# every app whose host can be installed here
nox -s examples

# install the host this app pins
pip install -r examples/apps/{name}/requirements.txt
```

The test client is {client}.
"""

REQUIREMENTS_HEADER = """# Host dependencies for examples/apps/{name} (PRD {prd}).
#
# Pinned exactly: these are the versions the example was run against, and the
# versions `nox -s examples` installs. `lauren_di` itself stays dependency-free
# (PRD 01 §6.2) — this file is the *example's* dependency list, not the
# library's, and it is never referenced from `pyproject.toml`.
"""

START = {
    "asgi": "the lifespan startup handler",
    "async": "the app factory, before the server accepts connections",
    "wsgi": "the process entry point, before the first request",
    "django": "`AppConfig.ready()`",
    "cli": "`main()`",
}


def main() -> None:
    """Write ``apps.json``, every app ``README.md`` and every ``requirements.txt``."""
    manifest: list[dict[str, str]] = []
    for spec in SPECS:
        name = str(spec["name"])
        directory = APPS / name
        (directory / "tests").mkdir(parents=True, exist_ok=True)
        readme = README.format(
            name=name,
            display=DISPLAY[name],
            family=str(spec["family"]),
            prd=spec["prd"],
            start=START[str(spec["family"])],
            install=spec["install"],
            close=spec["close"],
            extra=spec["extra"],
            client=spec["client"],
        )
        (directory / "README.md").write_text(readme, encoding="utf-8")
        deps = [str(dep) for dep in spec["deps"]]  # type: ignore[union-attr]
        body = REQUIREMENTS_HEADER.format(name=name, prd=spec["prd"])
        body += "\n".join(deps) + "\n" if deps else "# (standard library only)\n"
        (directory / "requirements.txt").write_text(body, encoding="utf-8")
        manifest.append(
            {
                "name": name,
                "family": str(spec["family"]),
                "prd": str(spec["prd"]),
                "app_module": f"{name}_app",
                "adapter_module": f"{name}_di",
                "test_module": f"test_{name}",
            }
        )
    document = {
        "contract": "prds/09-integration-contract-and-host-adapters.md",
        "description": (
            "One runnable example per host framework named in PRDs 10-14. The "
            "manifest is checked by tests/examples/test_apps_manifest.py and read "
            "by the `examples` nox session; add an app here or it does not exist."
        ),
        "apps": manifest,
    }
    (APPS / "apps.json").write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {len(manifest)} apps")


if __name__ == "__main__":
    main()
