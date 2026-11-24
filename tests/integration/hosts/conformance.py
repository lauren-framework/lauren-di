"""The twelve invariants of PRD 09 §5.10, written once and checked per adapter.

Each ``cN_*`` function is one row of the contract table, expressed as an
*observation that fails loudly when the adapter skips its obligation*. The
functions take an app name, open that app's conformance adapter (``adapters.py``)
and drive it; the test module is then twelve parametrised one-liners.

Two shape differences are stated in code rather than hidden:

* ``C-9`` — a long-lived host stops its container *after* the last unit, while a
  CLI host stops it *with* each invocation (one container per command). Both
  must stop it exactly once.
* ``C-10`` — a synchronous host keeps one loop for the process; an asynchronous
  host (and a CLI, whose bridge lives for one invocation) relies on
  loop-agnostic providers instead, which is the invariant's own second clause.
"""

from __future__ import annotations

import ast
import asyncio
from pathlib import Path

import adapters
import probe
import pytest
from lauren_di import DIScopeViolationError, MissingProviderError

#: The library whose framework-freedom C-12 asserts.
LIBRARY = adapters.ROOT / "lauren_di"

#: Every host framework this branch demonstrates: none may appear in the library.
HOST_FRAMEWORKS = frozenset(
    {
        "aiohttp",
        "bottle",
        "click",
        "cyclopts",
        "django",
        "falcon",
        "fastapi",
        "fire",
        "flask",
        "litestar",
        "pyramid",
        "quart",
        "sanic",
        "starlette",
        "typer",
    }
)


def _library_import_roots() -> set[str]:
    """Every top-level module ``lauren_di`` imports, from its own source."""
    roots: set[str] = set()
    for path in sorted(LIBRARY.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                roots.update(alias.name.split(".", 1)[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and not node.level and node.module:
                roots.add(node.module.split(".", 1)[0])
    return roots


def c1_container_started_before_the_host_serves(app: str) -> None:
    """C-1: the container starts before the first unit, not on its first resolve."""
    with adapters.session(app) as adapter:
        adapter.start()
        if adapter.lifecycle == "container":
            assert probe.EVENTS[:1] == ["config.started"], (
                f"{app}: startup() did not construct the singleton before the first "
                f"unit (observed {probe.EVENTS[:3]!r})"
            )
        unit = adapter.request()
        assert "unit" in unit.events, f"{app}: the unit never ran"
        assert probe.EVENTS.index("config.started") < probe.EVENTS.index("unit"), (
            f"{app}: the first unit ran before the container was started"
        )


def c2_request_scope_is_strict(app: str) -> None:
    """C-2: resolving a ``REQUEST`` provider with no scope is an error, not a cache."""
    with adapters.session(app) as adapter:
        adapter.start()
        with pytest.raises(DIScopeViolationError) as caught:
            adapter.resolve_on_container(probe.Scoped)
        assert getattr(caught.value, "reason", "no_active_scope") == "no_active_scope", (
            f"{app}: the scope violation did not name its reason"
        )


def c3_one_scope_per_unit_of_work(app: str) -> None:
    """C-3: two units get two unrelated request-scoped instances."""
    with adapters.session(app) as adapter:
        adapter.start()
        first = adapter.request()
        second = adapter.request()
        assert first.first is first.second, f"{app}: one unit built two instances"
        assert first.first is not second.first, (
            f"{app}: two units shared a request-scoped instance, so the scope is cached "
            "across units"
        )


def c4_the_scope_lives_in_the_host_state(app: str) -> None:
    """C-4: each unit's scope is a different object, published by the host."""
    with adapters.session(app) as adapter:
        adapter.start()
        first = adapter.request()
        second = adapter.request()
        assert first.scope is not None and second.scope is not None, (
            f"{app}: the adapter published no scope in the host's per-unit state"
        )
        assert first.scope is not second.scope, (
            f"{app}: two units resolved the same scope object, which means it is "
            "ambient state rather than per-unit state"
        )


def c5_every_resolution_goes_through_the_scope(app: str) -> None:
    """C-5: the unit's cache is the scope's; ``container.resolve`` bypasses it."""
    with adapters.session(app) as adapter:
        adapter.start()
        unit = adapter.request()
        assert unit.first is unit.second, f"{app}: the scope built the token twice"
        # The documented hazard: on a strict container this is an error, never a
        # cached hit, so an adapter that resolved on the container would have
        # crashed its own request instead of quietly double-building.
        with pytest.raises(DIScopeViolationError):
            adapter.resolve_on_container(probe.Scoped)


def c6_host_objects_are_values(app: str) -> None:
    """C-6: the host's object is supplied as a value and never a provider."""
    with adapters.session(app) as adapter:
        adapter.start()
        unit = adapter.request()
        assert unit.host_value is not None, (
            f"{app}: the adapter's value token resolved to nothing inside a unit"
        )
        with pytest.raises(MissingProviderError):
            adapter.resolve_on_container(adapter.host_token)


def c7_closes_after_the_last_chunk(app: str) -> None:
    """C-7: teardown runs after the body finished, not when the handler returned."""
    with adapters.session(app) as adapter:
        adapter.start()
        unit = adapter.request()
        assert unit.events.count("chunk") == 3, (
            f"{app}: the probe body should have produced three chunks, saw "
            f"{unit.events.count('chunk')}"
        )
        assert "scoped.closed" in unit.events, f"{app}: the scope never closed"
        last_chunk = max(i for i, event in enumerate(unit.events) if event == "chunk")
        assert unit.events.index("scoped.closed") > last_chunk, (
            f"{app}: the scope closed before the last chunk was produced (M-18)"
        )


def c8_teardown_errors_are_reported(app: str) -> None:
    """C-8: a failing destructor is reported, and it does not replace the body."""
    with adapters.session(app) as adapter:
        adapter.start()
        unit = adapter.request(fragile=True)
        assert unit.body, f"{app}: the unit produced no body"
        assert len(unit.errors) == 1, (
            f"{app}: expected exactly one reported teardown failure, got {unit.errors!r}"
        )
        if adapter.lifecycle == "invocation":
            assert unit.exit_code == 70, (
                f"{app}: a teardown failure must be a non-zero exit, not {unit.exit_code}"
            )
            assert str(unit.errors[0]).startswith("destruct_error:"), (
                f"{app}: the teardown failure never reached stderr"
            )


def c9_shutdown_runs_once_and_its_tuple_is_read(app: str) -> None:
    """C-9: one ``@pre_destruct`` at exit, and a second stop is harmless."""
    with adapters.session(app) as adapter:
        adapter.start()
        unit = adapter.request()
        if adapter.lifecycle == "invocation":
            assert unit.events.count("config.stopped") == 1, (
                f"{app}: an invocation should shut its container down exactly once"
            )
        else:
            assert unit.events.count("config.stopped") == 0, (
                f"{app}: the container stopped while the host was still serving"
            )
        assert adapter.stop() == (), f"{app}: shutdown() reported a failure for a clean unit"
        assert probe.EVENTS.count("config.stopped") == 1, (
            f"{app}: the container was torn down {probe.EVENTS.count('config.stopped')} times"
        )
        assert adapter.stop() == (), f"{app}: a second stop did not return an empty tuple"
        assert probe.EVENTS.count("config.stopped") == 1, (
            f"{app}: stopping twice tore the singleton down twice"
        )


def c10_one_loop_or_loop_agnostic_providers(app: str) -> None:
    """C-10: a sync host keeps one loop; an async host needs loop-agnostic providers."""
    with adapters.session(app) as adapter:
        adapter.start()
        first = adapter.request()
        second = adapter.request()
        assert first.first is not None and second.first is not None, (
            f"{app}: a unit failed to construct its request-scoped provider"
        )
        if adapter.single_loop:
            assert first.first.loop is second.first.loop, (
                f"{app}: two units ran on different loops, so a loop-bound singleton "
                "would break on unit 2 (M-6)"
            )
        else:
            assert first.first.loop is not second.first.loop, (
                f"{app}: this host shape is one loop per unit; the driver's model is stale"
            )


def c11_overrides_are_applied_at_build(app: str) -> None:
    """C-11: a plan is a build-time decision, so another build never sees it."""
    with adapters.session(app):  # the fixture is only here for a consistent skip guard
        real = asyncio.run(probe.build().resolve(probe.Config))
        frozen = asyncio.run(probe.build(with_override=True).resolve(probe.Config))
        assert not isinstance(real, probe.Frozen), f"{app}: the override leaked into a plain build"
        assert isinstance(frozen, probe.Frozen), f"{app}: the plan did not replace the provider"


def c12_the_adapter_lives_outside_the_library(app: str) -> None:
    """C-12: the adapter is outside ``lauren_di``, and the library knows no framework."""
    with adapters.session(app) as adapter:
        module_path = Path(adapter.module.__file__).resolve()
        assert adapters.APPS in module_path.parents, (
            f"{app}: the adapter is not in examples/apps, it is at {module_path}"
        )
        assert LIBRARY not in module_path.parents

    imported = _library_import_roots()
    assert not imported & HOST_FRAMEWORKS, (
        f"lauren_di imports host framework module(s): {sorted(imported & HOST_FRAMEWORKS)}"
    )
    exported = set(__import__("lauren_di").__all__)
    assert len(exported) == 38, f"lauren_di exports {len(exported)} names, not 38"
    assert not exported & HOST_FRAMEWORKS, (
        f"__all__ gained a host-specific name: {sorted(exported & HOST_FRAMEWORKS)}"
    )
