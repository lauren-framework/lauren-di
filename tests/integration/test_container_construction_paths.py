"""How the container actually *calls* constructors.

``_construct_class`` inspects a signature and decides, per parameter, whether
to inject a dependency or to let a default stand.  Getting that decision wrong
is invisible in the happy path and catastrophic in the field: injecting a
dependency that has a default turns an optional collaborator into a required
one, and ignoring a ``**kwargs`` parameter breaks decorator-style providers.

The cases below pin each branch of that decision, plus the two graph-shape
errors a constructor can produce (a cycle, and a multi-binding collection), and
the lifecycle chain of an inherited hook (PRD 03 §construction,
PRD 06 §CircularDependencyError).
"""

from __future__ import annotations

import asyncio

import pytest
from lauren_di import (
    CircularDependencyError,
    DIContainer,
    Scope,
    injectable,
    post_construct,
    pre_destruct,
)

pytestmark = pytest.mark.integration


@injectable()
class _CycleA:
    """One half of a deliberately unsatisfiable dependency cycle."""

    def __init__(self, other: _CycleB) -> None:
        """Store the collaborator that closes the cycle.

        Args:
            other: The other half of the cycle.
        """
        self.other = other


@injectable()
class _CycleB:
    """The other half of the cycle."""

    def __init__(self, other: _CycleA) -> None:
        """Store the collaborator that closes the cycle.

        Args:
            other: The other half of the cycle.
        """
        self.other = other


class TestSignatureDefaults:
    """A parameter with a default is only injected when a provider exists.

    Configuration classes are written with real defaults so they can be built
    without wiring; the container must respect that instead of failing the
    build for an "unregistered" ``timeout``.
    """

    def test_positional_default_is_used_when_unregistered(self) -> None:
        """No provider for ``timeout`` means the default stands."""

        @injectable()
        class Settings:
            def __init__(self, timeout: int = 30) -> None:
                self.timeout = timeout

        container = DIContainer()
        container.register(Settings)
        container.compile()

        assert asyncio.run(container.resolve(Settings)).timeout == 30

    def test_keyword_only_default_is_used_when_unregistered(self) -> None:
        """Keyword-only parameters take the other signature branch."""

        @injectable()
        class Settings:
            def __init__(self, *, debug: bool = False) -> None:
                self.debug = debug

        container = DIContainer()
        container.register(Settings)
        container.compile()

        assert asyncio.run(container.resolve(Settings)).debug is False

    def test_registered_provider_wins_over_the_default(self) -> None:
        """A default never shadows a real provider — that would make the
        registration dead code and hide a wiring bug.
        """

        @injectable()
        class Settings:
            def __init__(self, timeout: int = 30) -> None:
                self.timeout = timeout

        container = DIContainer()
        container.register_value(int, 5)
        container.register(Settings)
        container.compile()

        assert asyncio.run(container.resolve(Settings)).timeout == 5

    def test_var_keyword_constructor_receives_no_injection(self) -> None:
        """A ``**kwargs`` constructor advertises "pass what you like"; the
        container must not invent arguments for it.
        """

        @injectable()
        class Loose:
            def __init__(self, **kwargs: object) -> None:
                self.kwargs = kwargs

        container = DIContainer()
        container.register(Loose)
        container.compile()

        assert asyncio.run(container.resolve(Loose)).kwargs == {}


class TestFunctionProviderParameters:
    """A decorated function's parameters are injected like a constructor's.

    ``@injectable(provides=[...])`` on a function is the module-style
    declaration, so its signature is the dependency declaration.
    """

    def test_function_parameters_are_resolved(self) -> None:
        """The provider is called with resolved dependencies."""

        @injectable(provides=["service"])
        def build(base: str) -> str:
            return base + "!"

        container = DIContainer()
        container.register_value(str, "base")
        container.register(build)
        container.compile()

        assert asyncio.run(container.resolve("service")) == "base!"

    def test_function_provider_scope_is_honoured(self) -> None:
        """A transient function provider re-runs on every resolve."""
        calls: list[int] = []

        @injectable(provides=["fresh"], scope=Scope.TRANSIENT)
        def build() -> int:
            calls.append(1)
            return len(calls)

        container = DIContainer()
        container.register(build)
        container.compile()

        assert asyncio.run(container.resolve("fresh")) == 1
        assert asyncio.run(container.resolve("fresh")) == 2


class TestGraphShapeErrors:
    """The compiler refuses graphs it cannot linearise.

    A cycle is a wiring bug that would otherwise manifest as unbounded
    recursion at request time, so it must be reported with the cycle path
    (PRD 06 §CircularDependencyError).
    """

    def test_circular_dependency_is_reported_with_its_path(self) -> None:
        """The message names both ends so the user can find the edge."""
        container = DIContainer()
        container.register_class(_CycleA, _CycleA)
        container.register_class(_CycleB, _CycleB)

        with pytest.raises(CircularDependencyError):
            container.compile()

    def test_multi_binding_collects_for_list_injection(self) -> None:
        """``list[T]`` is the collection form of a multi-binding token."""

        @injectable(multi=True, provides=[str])
        class First(str):
            pass

        @injectable(multi=True, provides=[str])
        class Second(str):
            pass

        container = DIContainer()
        container.register(First)
        container.register(Second)
        container.compile()

        resolved = asyncio.run(container.resolve(list[str]))

        assert len(resolved) == 2


class TestLifecycleChains:
    """Inherited hooks run in a defined order and unwind in reverse.

    A provider subclassing another provider inherits its hooks; startup runs
    base-first and teardown runs subclass-first, which is the only ordering
    that lets a subclass rely on the base's resource being open.
    """

    def test_inherited_hooks_run_base_first_and_unwind_last(self) -> None:
        """Startup: base → child.  Teardown: child → base."""
        events: list[str] = []

        @injectable()
        class Base:
            @post_construct
            def base_start(self) -> None:
                events.append("base-start")

            @pre_destruct
            def base_stop(self) -> None:
                events.append("base-stop")

        @injectable()
        class Child(Base):
            @post_construct
            def child_start(self) -> None:
                events.append("child-start")

            @pre_destruct
            def child_stop(self) -> None:
                events.append("child-stop")

        container = DIContainer()
        container.register_class(Child, Child)
        container.compile()

        async def scenario() -> None:
            await container.resolve(Child)

        asyncio.run(scenario())
        asyncio.run(container.shutdown())

        assert events == ["base-start", "child-start", "child-stop", "base-stop"]

    def test_singleton_with_aclose_is_closed_at_shutdown(self) -> None:
        """The duck-typed ``aclose`` hook is honoured for singletons too."""
        closed: list[int] = []

        @injectable()
        class Client:
            async def aclose(self) -> None:
                closed.append(1)

        container = DIContainer()
        container.register_class(Client, Client)
        container.compile()

        async def scenario() -> None:
            await container.resolve(Client)

        asyncio.run(scenario())
        asyncio.run(container.shutdown())

        assert closed == [1]
