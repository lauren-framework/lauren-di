"""Overrides applied to a real build.

``OverridePlan`` is only useful if a compiled container honours it: the row
that reaches the resolver must be the override row, not the production row it
replaced.  These tests build a container per case and resolve through it,
because an override that is recorded but never applied is indistinguishable
from a working one until a test quietly asserts against the production object
(PRD 07 §overrides).

Overrides attach to the *declaration* of a provider, so every case here
registers the production provider through ``container.register(...)`` on an
``@injectable`` declaration — that is the seam ``OverridePlan`` replaces.
"""

from __future__ import annotations

import asyncio

import pytest
from lauren_di import (
    DIContainer,
    OverrideError,
    OverridePlan,
    ProviderOverride,
    ResolutionScope,
    Scope,
    injectable,
)

pytestmark = pytest.mark.integration


@injectable()
class Clock:
    """The production provider's token in every case below."""

    def now(self) -> str:
        """Report the wall-clock source in use.

        Returns:
            ``"real"`` for the production clock.
        """
        return "real"


class Frozen:
    """A stand-in that consumers must not need to know about."""

    def now(self) -> str:
        """Report the fake clock.

        Returns:
            ``"frozen"`` for the frozen clock.
        """
        return "frozen"


class TestValueOverride:
    """A ``value`` override swaps the object a token resolves to.

    This is the canonical "replace the clock with a frozen one" move: the
    consumer's constructor still asks for ``Clock``.
    """

    def test_override_replaces_the_registered_row(self) -> None:
        """The resolved object is the override, not the production instance."""
        plan = OverridePlan({Clock: ProviderOverride(token=Clock, kind="value", value=Frozen())})
        container = DIContainer(overrides=plan)
        container.register(Clock)
        container.compile()

        resolved = asyncio.run(container.resolve(Clock))

        assert isinstance(resolved, Frozen)
        assert resolved.now() == "frozen"

    def test_applied_overrides_report_the_token(self) -> None:
        """``applied_overrides`` is the audit trail a fixture asserts on, so
        it must name every token whose row was replaced.
        """
        plan = OverridePlan({Clock: ProviderOverride(token=Clock, kind="value", value=Frozen())})
        container = DIContainer(overrides=plan)
        container.register(Clock)
        container.compile()
        asyncio.run(container.resolve(Clock))

        assert Clock in container.applied_overrides()


class TestClassOverride:
    """A ``class`` override redirects construction to another implementation.

    Consumers keep their annotation; only the built type changes.
    """

    def test_override_class_is_constructed_instead(self) -> None:
        """The override's ``use_class`` is what gets instantiated."""
        plan = OverridePlan({Clock: ProviderOverride(token=Clock, kind="class", use_class=Frozen)})
        container = DIContainer(overrides=plan)
        container.register(Clock)
        container.compile()

        assert isinstance(asyncio.run(container.resolve(Clock)), Frozen)

    def test_override_class_honours_the_requested_scope(self) -> None:
        """Scope travels with the override row, so a request-scoped fake stays
        one instance per request.
        """
        plan = OverridePlan(
            {
                Clock: ProviderOverride(
                    token=Clock,
                    kind="class",
                    use_class=Frozen,
                    scope=Scope.REQUEST,
                )
            }
        )
        container = DIContainer(overrides=plan)
        container.register(Clock)
        container.compile()

        async def scenario() -> tuple[object, object]:
            async with ResolutionScope(container) as scope:
                return await scope.resolve(Clock), await scope.resolve(Clock)

        first, second = asyncio.run(scenario())

        assert first is second


class TestFactoryOverride:
    """A ``factory`` override supplies the callable that builds the instance.

    It exists for objects the graph cannot construct itself, so the factory
    must actually run.
    """

    def test_factory_is_called_instead_of_the_class(self) -> None:
        """The resolved object comes from the override factory."""
        plan = OverridePlan(
            {Clock: ProviderOverride(token=Clock, kind="factory", factory=lambda: Frozen())}
        )
        container = DIContainer(overrides=plan)
        container.register(Clock)
        container.compile()

        assert isinstance(asyncio.run(container.resolve(Clock)), Frozen)


class TestValuesOverride:
    """A ``values`` override replaces a multi-binding collection wholesale.

    Multi-bindings are how plugins are registered, so a partial replacement
    would leave production plugins installed alongside the fakes.
    """

    def test_registered_multi_values_are_replaced(self) -> None:
        """Only the override values remain for the token."""

        @injectable(multi=True, provides=["handlers"])
        def production_handler() -> str:
            return "production"

        plan = OverridePlan(
            {"handlers": ProviderOverride(token="handlers", kind="values", values=("fake",))}
        )
        container = DIContainer(overrides=plan)
        container.register(production_handler)
        container.compile()

        assert asyncio.run(container.resolve("handlers")) == "fake"


class TestExistingOverride:
    """An ``existing`` override re-points a token at another provider.

    Useful when a test wants the real dependency under a different name
    instead of a fake.
    """

    def test_alias_override_resolves_through_the_new_target(self) -> None:
        """The override's ``existing`` token is what gets resolved."""

        @injectable(provides=["alias"])
        def production() -> str:
            return "production"

        plan = OverridePlan(
            {"alias": ProviderOverride(token="alias", kind="existing", existing="staging")}
        )
        container = DIContainer(overrides=plan)
        container.register(production)
        container.register_value("staging", "staging://")
        container.compile()

        assert asyncio.run(container.resolve("alias")) == "staging://"


class TestOverrideErrors:
    """An override for a token nobody declared is a build-time error.

    Silently ignoring it would leave the production provider in place while
    the test believes it is using a double — the worst possible outcome, so
    the build refuses instead.
    """

    def test_override_without_a_target_is_rejected(self) -> None:
        """A plan may only replace rows that exist."""
        plan = OverridePlan({Clock: ProviderOverride(token=Clock, kind="value", value=Frozen())})
        container = DIContainer(overrides=plan)

        with pytest.raises(OverrideError):
            container.compile()

    def test_override_keeps_the_consumer_wiring_intact(self) -> None:
        """The override changes *what* is built, not how it is injected."""

        class Repository:
            def __init__(self) -> None:
                self.source = "real"

        class FakeRepository(Repository):
            def __init__(self) -> None:
                self.source = "fake"

        @injectable()
        class Service:
            def __init__(self, repository: Repository) -> None:
                self.repository = repository

        plan = OverridePlan(
            {Repository: ProviderOverride(token=Repository, kind="class", use_class=FakeRepository)}
        )
        container = DIContainer(overrides=plan)
        container.register_class(Repository, Repository)
        container.register(Service)
        container.compile()

        resolved = asyncio.run(container.resolve(Service))

        assert resolved.repository.source == "fake"
