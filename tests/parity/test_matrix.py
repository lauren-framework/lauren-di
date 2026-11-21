"""PRD 08 §5.9's twelve invariants, written once and run against both implementations.

Each test is one row of the matrix and carries the row id in its name, so a
failure reads as "P-7 failed" — the id the PRD uses. The bodies touch only the
``backend`` fixture (MC-28): no import of ``lauren`` or ``lauren_di`` appears in
this file, which is what lets the same assertion measure both sides.

Three rows diverge on purpose. Their expected outcome is not an ``xfail`` — it is
a value from :data:`spec.RECORDED`, compared exactly, so an implementation that
changes its mind fails the run instead of quietly passing (MC-31).

This module deliberately has no ``from __future__ import annotations``: several
invariants annotate a field with a *runtime* class object (``token``,
``Depends[Repo]``, ``Annotated[str, marker]``), and postponed evaluation would
turn those into strings the container cannot resolve — the test would then
measure a different program than the one a user writes.
"""

import pathlib
import sys

import pytest
from backends import Backend
from spec import RECORDED

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from helpers import run

pytestmark = pytest.mark.integration

#: MC-32: without the pinned checkout there is nothing to compare against, and a
#: partial run would be mistaken for parity. Skip the module and name the path.
REFERENCE_ROOT = pathlib.Path(__file__).resolve().parents[2].parent / "lauren-framework"
if not REFERENCE_ROOT.is_dir():
    pytest.skip(
        f"the reference checkout is not present at {REFERENCE_ROOT}; "
        "clone it (or restore the path) to run the parity matrix",
        allow_module_level=True,
    )


# ---------------------------------------------------------------------------
# P-1 … P-5: the shared surface both implementations promise
# ---------------------------------------------------------------------------


def test_p1_constructor_injection_resolves_by_annotation(backend: Backend) -> None:
    """P-1: a constructor parameter annotated with a registered class is injected."""

    @backend.injectable()
    class Dep:
        pass

    @backend.injectable()
    class User:
        def __init__(self, dep: Dep) -> None:
            self.dep = dep

    container = backend.container()
    container.register(Dep)
    container.register(User)

    assert isinstance(run(backend.resolve(container, User)).dep, Dep)


def test_p2_a_singleton_is_cached_per_container(backend: Backend) -> None:
    """P-2: same object twice in one container; a second container builds its own."""

    @backend.injectable()
    class Single:
        pass

    container = backend.container()
    container.register(Single)
    first = run(backend.resolve(container, Single))
    second = run(backend.resolve(container, Single))

    other = backend.container()
    other.register(Single)

    assert first is second, "a singleton must be cached for the life of its container"
    assert first is not run(backend.resolve(other, Single)), (
        "a second container must build its own singleton; caching is per container, not global"
    )


def test_p3_a_declared_value_token_is_supplied_out_of_band(backend: Backend) -> None:
    """P-3: the declared token resolves to the caller's object, identity intact."""
    token = backend.value_token("p3")
    backend.declare_value_type(token)

    @backend.injectable()
    class Needs:
        def __init__(self, supplied: token) -> None:
            self.supplied = supplied

    container = backend.container(value_tokens=(token,))
    container.register(Needs)
    sentinel = object()

    resolved = run(backend.resolve(container, Needs, values={token: sentinel}))
    assert resolved.supplied is sentinel, (
        "the value supplied out of band must be the object itself, not a copy or a wrapper"
    )


def test_p4_a_missing_provider_names_the_token(backend: Backend) -> None:
    """P-4: the failure is `MissingProviderError` and `detail["token"]` names the dep."""

    @backend.injectable()
    class Missing:
        pass

    @backend.injectable()
    class NeedsMissing:
        def __init__(self, dep: Missing) -> None:
            self.dep = dep

    container = backend.container()
    container.register(NeedsMissing)

    with pytest.raises(backend.error("MissingProviderError")) as caught:
        container.compile()
        run(backend.resolve(container, NeedsMissing))

    detail = caught.value.detail
    assert "token" in detail, f"detail must name the missing token, got {sorted(detail)}"
    assert "Missing" in str(detail["token"]), (
        f"detail['token'] names {detail['token']!r}, not the missing dependency"
    )


def test_p5_class_body_field_injection_honours_the_annotations_token(backend: Backend) -> None:
    """P-5: a class-body field is populated from the token its annotation names.

    The fixture annotates the field `repo: Repo`, which is the form PRD 08's P-5 row
    states and the form PRD 01 §5.5 says a typed codebase writes. `Depends[Repo]` is
    the *same* token (D-25), but it is a runtime spelling and not a type form, so
    writing it in annotation position would make this fixture untypable — and, on a
    function token, silently uninjected. The spelling's identity is pinned by
    `tests/unit/test_public_api.py` for the library; it is not a shared behaviour,
    because the reference's `Depends` is an `Annotated` marker (PRD 04 M-51).
    """

    @backend.injectable()
    class Repo:
        pass

    @backend.injectable()
    class Uses:
        repo: Repo

    container = backend.container()
    container.register(Repo)
    container.register(Uses)

    assert isinstance(run(backend.resolve(container, Uses)).repo, Repo)


# ---------------------------------------------------------------------------
# P-6: the first recorded divergence — HTTP vocabulary is not DI vocabulary
# ---------------------------------------------------------------------------


def test_p6_an_http_marker_is_not_a_di_declaration(backend: Backend) -> None:
    """P-6 (divergent): the reference reads `Annotated[str, Path]` as a dep on `str`.

    `lauren_di` has no HTTP vocabulary at all, so the same field is not a
    dependency and the class resolves. Both outcomes are recorded, so neither
    side can drift into the other's behaviour unnoticed (D-25).
    """
    from typing import Annotated

    marker = backend.http_marker()

    @backend.injectable()
    class Uses:
        where: Annotated[str, marker]  # type: ignore[valid-type]

    container = backend.container()
    container.register(Uses)
    recorded = RECORDED["P-6"][backend.name]

    try:
        container.compile()
        instance = run(backend.resolve(container, Uses))
    except backend.error("MissingProviderError") as exc:
        observed = f"raises:MissingProviderError:{exc.detail['token']}"
        assert observed == recorded, (
            f"{backend.name} raised for an HTTP-marked field with {observed!r}, "
            f"but the register records {recorded!r}"
        )
        return

    assert recorded == "ignored", (
        f"{backend.name} resolved an HTTP-marked field, but the register records {recorded!r}; "
        "the divergence in backends.py is stale"
    )
    assert not hasattr(instance, "where"), (
        "a field carrying a foreign marker must be left untouched, not populated"
    )


# ---------------------------------------------------------------------------
# P-7 … P-9: the guarantees that catch real wiring mistakes
# ---------------------------------------------------------------------------


def test_p7_a_scope_narrowing_violation_is_rejected(backend: Backend) -> None:
    """P-7: a singleton may not depend on a request-scoped provider."""
    Scope = backend.attribute("Scope")

    @backend.injectable(scope=Scope.REQUEST)
    class Request:
        pass

    @backend.injectable()
    class Singleton:
        def __init__(self, request: Request) -> None:
            self.request = request

    container = backend.container()
    container.register(Request)
    container.register(Singleton)

    with pytest.raises(backend.error("DIScopeViolationError")) as caught:
        container.compile()

    detail = caught.value.detail
    assert {"dependent_scope", "dependency_scope"} <= set(detail), (
        f"detail must report both scopes, got {sorted(detail)}"
    )


def test_p8_multi_bindings_collect_in_registration_order(backend: Backend) -> None:
    """P-8: two `multi=True` registrations of one token resolve as `list[T]`, in order."""

    @backend.injectable()
    class Iface:
        pass

    @backend.injectable(provides=[Iface], multi=True)
    class First(Iface):
        pass

    @backend.injectable(provides=[Iface], multi=True)
    class Second(Iface):
        pass

    container = backend.container()
    container.register(First)
    container.register(Second)

    resolved = run(backend.resolve(container, list[Iface]))
    assert [type(item).__name__ for item in resolved] == ["First", "Second"]


def test_p9_alias_chains_resolve_and_cycles_fail(backend: Backend) -> None:
    """P-9: `use_existing` forwards the value through a chain; a cycle is refused."""
    use_existing = backend.use_existing

    @backend.injectable()
    class Real:
        pass

    container = backend.container()
    container.register(Real)
    container.register_custom(use_existing(provide="ALIAS", existing=Real))
    container.register_custom(use_existing(provide="ALIAS2", existing="ALIAS"))
    container.compile()

    direct = run(backend.resolve(container, "ALIAS"))
    forwarded = run(backend.resolve(container, "ALIAS2"))
    assert isinstance(direct, Real)
    assert direct is forwarded, "an alias chain must forward the same instance"

    cycle = backend.container()
    cycle.register_custom(use_existing(provide="A", existing="B"))
    cycle.register_custom(use_existing(provide="B", existing="A"))
    with pytest.raises(backend.error("CircularDependencyError")):
        cycle.compile()
        run(backend.resolve(cycle, "A"))


# ---------------------------------------------------------------------------
# P-10 … P-12: lifecycle, which is where the second divergence lives
# ---------------------------------------------------------------------------


def test_p10_generator_providers_tear_down_in_reverse_order(backend: Backend) -> None:
    """P-10 (divergent): teardown runs in reverse creation order — where there is one.

    The reference's container has no `shutdown()` at all (a `LifecycleScheduler`
    owns teardown, PRD 03), so on that side the recorded outcome is that nothing
    has run yet. The library owns it and must run `b` then `a`.
    """
    order: list[str] = []

    @backend.injectable()
    def make_a():
        yield "a"
        order.append("a")

    @backend.injectable()
    def make_b():
        yield "b"
        order.append("b")

    container = backend.container()
    container.register(make_a)
    container.register(make_b)
    container.compile()

    assert run(backend.resolve(container, make_a)) == "a"
    assert run(backend.resolve(container, make_b)) == "b"

    teardown = backend.shutdown(container)
    if teardown is None:
        assert RECORDED["P-11"][backend.name] == "no-container-lifecycle", (
            f"{backend.name} has no container-owned shutdown, but the register says otherwise"
        )
        assert order == [], f"nothing may have been torn down without a shutdown, yet {order} ran"
        return

    errors = run(teardown)
    assert order == ["b", "a"], f"teardown must run in reverse creation order, got {order}"
    assert errors == (), f"a clean teardown reports no errors, got {errors!r}"


def test_p11_singleton_hooks_fire_at_startup_request_hooks_at_resolve(backend: Backend) -> None:
    """P-11 (divergent): who fires `@post_construct`, and when.

    The library owns startup, so a singleton's hook fires there and a request
    hook at resolve. The reference's container owns neither, so its singleton
    hook never fires in this harness — recorded, not tolerated (D-53).
    """
    events: list[str] = []
    post_construct = backend.attribute("post_construct")
    Scope = backend.attribute("Scope")

    @backend.injectable()
    class Singleton:
        @post_construct
        def hook(self) -> None:
            events.append("singleton")

    @backend.injectable(scope=Scope.REQUEST)
    class Request:
        @post_construct
        def hook(self) -> None:
            events.append("request")

    container = backend.container()
    container.register(Singleton)
    container.register(Request)
    container.compile()

    startup = backend.startup(container)
    if startup is None:
        assert RECORDED["P-11"][backend.name] == "no-container-lifecycle", (
            f"{backend.name} has no container-owned startup, but the register says otherwise"
        )
        assert events == [], f"no hook may fire without a startup, yet {events} ran"
    else:
        run(startup)
        assert events == ["singleton"], (
            f"startup must fire singleton hooks and only those, got {events}"
        )

    async def resolve_one_request() -> None:
        async with backend.scope(container) as scoped:
            await scoped.resolve(Request)

    run(resolve_one_request())
    assert events[-1] == "request", f"a request hook must fire at resolve, got {events}"
    assert events.count("request") == 1, f"the request hook fired more than once: {events}"


def test_p12_one_builds_overrides_never_leak_into_another(backend: Backend) -> None:
    """P-12: two containers with different plans disagree, and neither is order-dependent."""

    @backend.injectable()
    class Config:
        value = "real"

    def plan(value: str) -> object:
        return backend.plan({Config: backend.provider_override(Config, value)})

    first = backend.container(overrides=plan("one"))
    second = backend.container(overrides=plan("two"))
    first.register(Config)
    second.register(Config)

    assert run(backend.resolve(first, Config)) == "one"
    assert run(backend.resolve(second, Config)) == "two"
    assert run(backend.resolve(first, Config)) == "one", (
        "resolving the second container must not change what the first one builds"
    )


# ---------------------------------------------------------------------------
# The adapter's one substitution, asserted from both sides
# ---------------------------------------------------------------------------


def test_the_values_keyword_is_the_only_supply_route(backend: Backend) -> None:
    """The `values=` divergence is absorbed by the adapter — and only there.

    If both sides ever accepted the same keyword this test fails, which is the
    signal that the ``values`` entry in the register can be deleted.
    """
    import inspect

    parameters = inspect.signature(backend.container().resolve).parameters
    assert backend.value_keyword in parameters, (
        f"{backend.name}.resolve no longer accepts {backend.value_keyword}=; "
        "the adapter in backends.py calls it"
    )
    assert backend.other_keyword not in parameters, (
        f"{backend.name}.resolve now accepts {backend.other_keyword}= too, so the "
        "`values` divergence no longer exists"
    )
