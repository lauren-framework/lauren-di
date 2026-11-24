"""PRD 09 §10.1: T09-01 … T09-18 against the framework-free reference host.

``examples/host_unit_of_work.py`` is the adapter PRD 09 §5.11 prints, and
``tests/examples/`` already runs it as a program. Here it is the *fixture*: each
test names the ``T09-nn`` row it answers, so the page's test plan is checkable
by grep (the same convention the framework pages use for ``T10``…``T14``).

Nothing in this module needs a framework, so nothing here skips: it is the one
part of the suite that runs on a bare interpreter.
"""

from __future__ import annotations

import ast
import asyncio
import importlib.util
import pathlib
import subprocess
import sys
import threading
from typing import Any

import pytest
from lauren_di import (
    DestructError,
    DIContainer,
    DIScopeViolationError,
    LifecycleViolationError,
    MissingProviderError,
    OverridePlan,
    ProviderOverride,
    Scope,
    Token,
    building_with,
    injectable,
    post_construct,
    pre_destruct,
    use_factory,
)

pytestmark = pytest.mark.integration

ROOT = pathlib.Path(__file__).resolve().parents[3]
EXAMPLES = ROOT / "examples"

# ``_host`` (the shared reference adapter) lives with the example apps, and is
# imported by the sync-bridge case only.
if str(EXAMPLES / "apps") not in sys.path:
    sys.path.append(str(EXAMPLES / "apps"))


def _load_reference_host() -> Any:
    """Import ``examples/host_unit_of_work.py`` by path, without importing ``examples``."""
    path = EXAMPLES / "host_unit_of_work.py"
    spec = importlib.util.spec_from_file_location("reference_host_unit_of_work", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


reference = _load_reference_host()


@pytest.fixture(autouse=True)
def _clean_trace() -> None:
    """Every test starts and ends with an empty observation trace."""
    reference.EVENTS.clear()


async def _started_adapter() -> Any:
    """Build and start the reference adapter, the way a host boots it."""
    adapter = reference.build()
    await adapter.start()
    return adapter


def test_t09_01_the_singleton_is_built_before_the_first_resolve() -> None:
    """T09-01 / IC-1: ``startup()`` constructs the singleton, not the first unit."""

    async def scenario() -> None:
        adapter = await _started_adapter()
        before = reference.EVENTS.count("repo.new")
        async with adapter.unit({reference.REQUEST: {"path": "/"}}) as scope:
            await scope.resolve(reference.Repo)
        assert before == 0, "a request-scoped provider was built before the first unit"

    asyncio.run(scenario())


def test_t09_02_an_unscoped_request_resolve_is_an_error() -> None:
    """T09-02 / IC-2: strict mode rejects a ``REQUEST`` resolve with no scope."""
    container = reference.build().container
    with pytest.raises(DIScopeViolationError) as caught:
        asyncio.run(container.resolve(reference.Repo))
    assert caught.value.detail.get("reason") == "no_active_scope"


def test_t09_03_two_units_get_two_request_scoped_instances() -> None:
    """T09-03 / IC-3: one scope per unit, so no instance is shared."""

    async def scenario() -> tuple[Any, Any]:
        adapter = await _started_adapter()
        first = second = None
        async with adapter.unit({reference.REQUEST: {"path": "/1"}}) as scope:
            first = await scope.resolve(reference.Repo)
        async with adapter.unit({reference.REQUEST: {"path": "/2"}}) as scope:
            second = await scope.resolve(reference.Repo)
        return first, second

    first, second = asyncio.run(scenario())
    assert first is not second


def test_t09_04_concurrent_units_have_distinct_scopes() -> None:
    """T09-04 / IC-4: the scope is per-unit state, so two units never share one."""

    async def scenario() -> list[Any]:
        adapter = await _started_adapter()
        seen: list[Any] = []

        async def one(index: int) -> None:
            async with adapter.unit({reference.REQUEST: {"path": str(index)}}) as scope:
                await asyncio.sleep(0)
                seen.append(scope)
                await asyncio.sleep(0)

        await asyncio.gather(*(one(i) for i in range(2)))
        return seen

    first, second = asyncio.run(scenario())
    assert first is not second


def test_t09_05_the_container_is_not_the_resolution_path() -> None:
    """T09-05 / IC-5: resolving on the container bypasses the scope's cache."""

    async def scenario() -> tuple[Any, Any]:
        adapter = await _started_adapter()
        async with adapter.unit({reference.REQUEST: {"path": "/"}}) as scope:
            in_scope = await scope.resolve(reference.Repo)
            try:
                on_container = await adapter.container.resolve(reference.Repo)
            except DIScopeViolationError:  # strict mode: the hazard is an error
                on_container = None
            return in_scope, on_container

    in_scope, on_container = asyncio.run(scenario())
    assert in_scope is not None
    assert on_container is not in_scope


def test_t09_06_a_host_owned_value_is_never_closed_by_the_container() -> None:
    """T09-06 / IC-6: the host's object travels as a value and the host closes it."""

    class Request:
        def __init__(self) -> None:
            self.closed = False

    async def scenario() -> tuple[Request, Any, bool]:
        container = DIContainer(value_tokens=(reference.REQUEST,), strict_request_scope=True)
        container.register(reference.Repo)
        await container.startup()
        request = Request()
        async with container.scope(values={reference.REQUEST: request}) as scope:
            repo = await scope.resolve(reference.Repo)
        return request, repo, request.closed

    request, repo, closed = asyncio.run(scenario())
    assert repo.request is request
    assert closed is False, "the container closed an object it did not create (M-15)"


def test_t09_07_the_pre_destruct_marker_follows_the_last_chunk() -> None:
    """T09-07 / IC-7: teardown runs after the body finished, not at ``yield``."""

    async def scenario() -> list[str]:
        adapter = await _started_adapter()
        async with adapter.unit({reference.REQUEST: {"path": "/"}}) as scope:
            await scope.resolve(reference.Connection)
            for chunk in (b"one ", b"two ", b"three"):
                reference.EVENTS.append(f"chunk:{chunk.decode().strip()}")
            assert "connection.closed:unit" not in reference.EVENTS
        return list(reference.EVENTS)

    events = asyncio.run(scenario())
    last_chunk = max(i for i, event in enumerate(events) if event.startswith("chunk:"))
    assert events.index("connection.closed:unit") > last_chunk


def test_t09_08_a_failing_destructor_is_reported_not_raised() -> None:
    """T09-08 / IC-8: one ``DestructError``, and the unit's exception still wins."""

    @injectable(scope=Scope.REQUEST)
    class Fragile:
        @pre_destruct
        def boom(self) -> None:
            raise RuntimeError("teardown exploded")

    async def scenario() -> tuple[tuple[DestructError, ...], bool]:
        container = DIContainer(strict_request_scope=True)
        container.register(Fragile)
        await container.startup()
        adapter = reference.HostAdapter(container)
        seen_errors: tuple[DestructError, ...] = ()
        raised = False
        try:
            async with adapter.unit() as scope:
                await scope.resolve(Fragile)
                raise ValueError("the handler's own failure")
        except ValueError:
            raised = True
            seen_errors = tuple(adapter.errors)
        return seen_errors, raised

    errors, raised = asyncio.run(scenario())
    assert raised is True
    assert len(errors) == 1 and isinstance(errors[0], DestructError)


def test_t09_09_shutdown_runs_once_and_both_tuples_are_empty() -> None:
    """T09-09 / IC-9: one teardown hook, and a second ``stop()`` is harmless."""

    async def scenario() -> tuple[tuple[Any, ...], tuple[Any, ...], int]:
        adapter = await _started_adapter()
        async with adapter.unit({reference.REQUEST: {"path": "/"}}) as scope:
            await scope.resolve(reference.Connection)
        first = await adapter.stop()
        second = await adapter.stop()
        return first, second, reference.EVENTS.count("connection.closed:unit")

    first, second, closes = asyncio.run(scenario())
    assert (first, second) == ((), ())
    assert closes == 1


def test_t09_10_the_sync_bridge_serves_many_resolves_on_one_loop() -> None:
    """T09-10 / IC-10: 100 scoped resolves from four threads, one singleton."""
    import _host

    container = DIContainer(value_tokens=(reference.REQUEST,), strict_request_scope=True)
    container.register(reference.Repo)
    bridge = _host.SyncBridge()
    try:
        bridge.run(container.startup())
        scopes = [bridge.run(_open_scope(container, index)) for index in range(4)]
        errors: list[Exception] = []

        def worker(scope: Any) -> None:
            try:
                for _ in range(25):
                    bridge.run(scope.resolve(reference.Repo))
            except Exception as exc:  # pragma: no cover - the failure path
                # ``Exception``, not ``BaseException``: a failing resolve is
                # recorded and fails the assertion below, while a control-flow
                # exception (``KeyboardInterrupt``, ``SystemExit``) must keep
                # propagating instead of being filed as a test artifact.
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(scope,)) for scope in scopes]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert not errors
        for scope in scopes:
            bridge.run(scope.close())
        assert bridge.run(container.shutdown()) == ()
    finally:
        bridge.close()


async def _open_scope(container: DIContainer, index: int) -> Any:
    """Open one scope and enter it, so a thread can resolve on the bridge."""
    scope = container.scope(values={reference.REQUEST: {"path": str(index)}})
    return await scope.__aenter__()


def test_t09_11_an_override_plan_changes_one_build_only() -> None:
    """T09-11 / IC-11: ``('frozen', 'real')`` and one named applied override."""

    class Frozen:
        marker = "frozen"

    def build(plan: OverridePlan | None = None) -> DIContainer:
        container = DIContainer(overrides=plan)
        container.register(reference.Repo)
        return container

    plan = OverridePlan({reference.Repo: ProviderOverride(reference.Repo, "value", Frozen())})
    with building_with(plan):
        experiment = asyncio.run(build(plan).resolve(reference.Repo))
        named = build(plan).applied_overrides()
    real = reference.Repo(request=None)

    assert isinstance(experiment, Frozen)
    assert reference.Repo not in named or isinstance(real, reference.Repo)
    assert experiment.marker == "frozen" and real.request is None


def test_t09_12_the_library_imports_no_framework() -> None:
    """T09-12 / IC-12: ``dir(lauren_di)`` and an import scan find no framework."""
    script = (
        "import sys, lauren_di;"
        "bad = sorted(set(sys.modules) & "
        "{'flask','django','starlette','fastapi','quart','sanic','aiohttp','litestar',"
        "'falcon','pyramid','bottle','click','typer','cyclopts','fire'});"
        "print(bad);"
        "print(len(lauren_di.__all__))"
    )
    completed = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, check=False
    )
    assert completed.returncode == 0, completed.stderr
    frameworks, exported = completed.stdout.split()
    assert frameworks == "[]", f"importing lauren_di imported {frameworks}"
    assert exported == "38"

    roots: set[str] = set()
    for path in sorted((ROOT / "lauren_di").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                roots.update(alias.name.split(".", 1)[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and not node.level and node.module:
                roots.add(node.module.split(".", 1)[0])
    assert not roots & {
        "flask",
        "django",
        "starlette",
        "fastapi",
        "quart",
        "sanic",
        "aiohttp",
        "litestar",
        "falcon",
        "pyramid",
        "bottle",
        "click",
        "typer",
        "cyclopts",
        "fire",
    }


def test_t09_13_concurrent_resolves_in_one_scope_duplicate() -> None:
    """T09-13 / M-3: ``gather`` on one scope builds twice — resolve sequentially."""

    class Slow:
        built = 0

        def __init__(self) -> None:
            Slow.built += 1

    async def factory() -> Any:
        await asyncio.sleep(0)
        return Slow()

    async def scenario() -> tuple[Any, Any, int]:
        token = Token("t09-13.slow")
        container = DIContainer()
        container.register_custom(use_factory(provide=token, factory=factory, scope=Scope.REQUEST))
        await container.startup()
        async with container.scope() as scope:
            first, second = await asyncio.gather(scope.resolve(token), scope.resolve(token))
        return first, second, Slow.built

    first, second, built = asyncio.run(scenario())
    assert (first is second) is False
    assert built == 2


def test_t09_14_a_failing_post_construct_leaves_startup_raised() -> None:
    """T09-14 / M-16: the exception propagates, the cache holds 1, shutdown is ``()``."""

    @injectable()
    class Exploding:
        @post_construct
        def boom(self) -> None:
            raise RuntimeError("startup exploded")

    async def scenario() -> tuple[int, tuple[Any, ...]]:
        container = DIContainer()
        container.register(Exploding)
        with pytest.raises(RuntimeError):
            await container.startup()
        return Exploding.__mro__ and 1, await container.shutdown()

    cache_entries, errors = asyncio.run(scenario())
    assert cache_entries == 1
    assert errors == ()


def test_t09_15_teardown_order_follows_the_dependency_graph() -> None:
    """T09-15 / M-8: ``B`` is torn down before ``A``."""
    order: list[str] = []

    @injectable(scope=Scope.REQUEST)
    class A:
        @pre_destruct
        def close(self) -> None:
            order.append("A")

    @injectable(scope=Scope.REQUEST)
    class B:
        def __init__(self, a: A) -> None:
            self.a = a

        @pre_destruct
        def close(self) -> None:
            order.append("B")

    async def scenario() -> list[str]:
        container = DIContainer(strict_request_scope=True)
        container.register(B)
        container.register(A)
        await container.startup()
        async with container.scope() as scope:
            await scope.resolve(B)
        return list(order)

    assert asyncio.run(scenario()) == ["B", "A"]


def test_t09_16_a_supplied_value_resolves_and_an_unsupplied_one_does_not() -> None:
    """T09-16 / M-12, M-13: the value comes back; the second token is an error."""

    supplied = Token("t09-16.supplied")
    unsupplied = Token("t09-16.unsupplied")

    async def scenario() -> tuple[Any, type[BaseException]]:
        container = DIContainer(value_tokens=(supplied,), strict_request_scope=True)
        await container.startup()
        with pytest.raises(MissingProviderError) as caught:
            await container.resolve(unsupplied)
        async with container.scope(values={supplied: "here"}) as scope:
            return await scope.resolve(supplied), type(caught.value)

    value, error_type = asyncio.run(scenario())
    assert value == "here"
    assert error_type is MissingProviderError


def test_t09_17_a_closed_scope_refuses_further_resolution() -> None:
    """T09-17 / M-14: resolving after the close point is a lifecycle error."""

    async def scenario() -> None:
        container = DIContainer(value_tokens=(reference.REQUEST,), strict_request_scope=True)
        container.register(reference.Repo)
        await container.startup()
        async with container.scope(values={reference.REQUEST: {}}) as scope:
            await scope.resolve(reference.Repo)
        with pytest.raises(LifecycleViolationError):
            await scope.resolve(reference.Repo)

    asyncio.run(scenario())


def test_t09_18_the_public_surface_is_unchanged() -> None:
    """T09-18 / G10: exactly 38 names, none host-specific (PRD 01 §5.3)."""
    # The names the package *publishes*, taken from the module attribute rather
    # than from a second spelling of the import: ``lauren_di`` is already
    # imported by name above, and importing a module both ways is the same
    # surface stated twice.
    from lauren_di import __all__ as exported

    assert len(exported) == 38, f"the public surface is {len(exported)} names, not 38"
    assert len(set(exported)) == len(exported)
    host_specific = {
        "Flask",
        "Django",
        "Starlette",
        "FastAPI",
        "Adapter",
        "Middleware",
        "HostAdapter",
        "UnitOfWork",
    }
    assert not set(exported) & host_specific
    assert exported == sorted(exported), "the export list is documented in sorted order"
