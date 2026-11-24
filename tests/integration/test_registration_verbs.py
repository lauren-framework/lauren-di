"""The registration verbs and their resolution contract.

``DIContainer`` exposes one verb per way a provider can enter the graph
(``register``, ``register_value``, ``register_instance``, ``register_class``,
``register_factory``, ``register_alias``) and every one of them shares the same
two obligations: the row must be findable under the token afterwards, and a
second registration of that token must be refused.

The container is also introspectable (``has_provider``, ``get_provider``,
``explain``, ``all_providers``), because a framework wrapper has to answer
"what is wired here?" without reaching into private state.  Those answers are
asserted here so a refactor of the internal registry cannot quietly change
them (PRD 03 §registration, PRD 06 §duplicate binding).
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from lauren_di import (
    DIContainer,
    DuplicateBindingError,
    MissingProviderError,
    Provider,
    ResolutionScope,
    Scope,
    injectable,
)

pytestmark = pytest.mark.integration


@injectable()
class Repository:
    """A decorated provider used by the class-registration cases."""

    def __init__(self) -> None:
        """Mark the instance so a test can tell copies apart."""
        self.tag = "repository"


class TestRegisterVerb:
    """``register`` accepts a declaration and derives the token from it.

    This is the verb a framework calls after scanning decorators, so it must
    refuse anything that was never declared — silently accepting a plain class
    would create a provider with no lifecycle metadata to honour.
    """

    def test_undecorated_class_is_refused(self) -> None:
        """A plain class has no ``InjectableMeta`` to build a row from."""

        class Plain:
            pass

        container = DIContainer()

        with pytest.raises(MissingProviderError) as excinfo:
            container.register(Plain)

        assert "not marked @injectable" in str(excinfo.value)

    def test_decorated_class_is_registered_under_its_own_token(self) -> None:
        """Declaration without an explicit ``provides`` binds the class."""
        container = DIContainer()
        container.register(Repository)
        container.compile()

        assert isinstance(asyncio.run(container.resolve(Repository)), Repository)


class TestRegisterInstance:
    """``register_instance`` adopts an object the caller already built.

    The same instance must come back for every resolve — the caller's object,
    not a reconstruction.
    """

    def test_instance_is_returned_verbatim(self) -> None:
        """Identity is the contract for instance rows."""
        instance = Repository()
        container = DIContainer()
        container.register_instance("repo", instance)
        container.compile()

        assert asyncio.run(container.resolve("repo")) is instance

    def test_second_instance_registration_is_refused(self) -> None:
        """Two instances for one token would make resolution non-deterministic."""
        container = DIContainer()
        container.register_instance("repo", Repository())

        with pytest.raises(DuplicateBindingError):
            container.register_instance("repo", Repository())


class TestRegisterClass:
    """``register_class`` builds the class at resolution time.

    This is the only verb that lets the token and the implementation differ,
    and the only one where the lifecycle scope is a per-row decision.
    """

    def test_scope_transient_builds_a_new_instance_each_time(self) -> None:
        """``Scope.TRANSIENT`` must not cache."""
        container = DIContainer()
        container.register_class(Repository, Repository, scope=Scope.TRANSIENT)
        container.compile()

        first = asyncio.run(container.resolve(Repository))
        second = asyncio.run(container.resolve(Repository))

        assert first is not second

    def test_scope_singleton_is_cached(self) -> None:
        """The default scope is one instance per container."""
        container = DIContainer()
        container.register_class(Repository, Repository)
        container.compile()

        assert asyncio.run(container.resolve(Repository)) is asyncio.run(
            container.resolve(Repository)
        )

    def test_duplicate_class_registration_is_refused(self) -> None:
        """Duplicate detection also covers class rows."""
        container = DIContainer()
        container.register_class(Repository, Repository)

        with pytest.raises(DuplicateBindingError):
            container.register_class(Repository, Repository)


class TestRegisterFactory:
    """``register_factory`` runs a callable once per scope."""

    def test_factory_is_called_for_the_singleton_once(self) -> None:
        """A singleton factory must not re-run per resolve."""
        calls: list[int] = []

        def make() -> int:
            calls.append(1)
            return len(calls)

        container = DIContainer()
        container.register_factory("counter", make)
        container.compile()

        first = asyncio.run(container.resolve("counter"))
        second = asyncio.run(container.resolve("counter"))

        assert (first, second) == (1, 1)
        assert len(calls) == 1

    def test_duplicate_factory_registration_is_refused(self) -> None:
        """Factory rows take part in the same duplicate gate."""
        container = DIContainer()
        container.register_factory("thing", lambda: 1)

        with pytest.raises(DuplicateBindingError):
            container.register_factory("thing", lambda: 2)


class TestRegisterAlias:
    """``register_alias`` publishes one provider under a second token.

    The alias must resolve to the target's instance (not a copy), and it must
    fail at build time when the target does not exist — an alias to nowhere is
    a wiring typo, not a runtime condition.
    """

    def test_alias_resolves_to_the_target_instance(self) -> None:
        """Both tokens yield the same singleton."""
        container = DIContainer()
        container.register_class(Repository, Repository)
        container.register_alias("repo", Repository)
        container.compile()

        assert asyncio.run(container.resolve("repo")) is asyncio.run(container.resolve(Repository))

    def test_alias_to_unknown_token_fails_the_build(self) -> None:
        """The compiler validates every alias target."""
        container = DIContainer()
        container.register_alias("repo", "missing")

        with pytest.raises(MissingProviderError):
            container.compile()


class TestCompileLifecycle:
    """``compile`` is idempotent and resolution works without calling it.

    Frameworks compile once, but tests often resolve directly; both must work,
    and a second compile must not rebuild the graph or re-raise validation
    errors.
    """

    def test_compile_twice_is_harmless(self) -> None:
        """A second compile is a no-op, not a second validation pass."""
        container = DIContainer()
        container.register_class(Repository, Repository)
        container.compile()
        container.compile()

        assert isinstance(asyncio.run(container.resolve(Repository)), Repository)

    def test_resolution_without_explicit_compile_succeeds(self) -> None:
        """``resolve`` validates lazily so a script can skip ``compile``."""
        container = DIContainer()
        container.register_class(Repository, Repository)

        assert isinstance(asyncio.run(container.resolve(Repository)), Repository)


class TestIntrospection:
    """The container answers questions about its own wiring.

    A host adapter has to explain a failed resolve, so these accessors are part
    of the public contract rather than debugging conveniences.
    """

    def test_has_provider_distinguishes_known_and_unknown_tokens(self) -> None:
        """``has_provider`` must not raise for an unknown token."""
        container = DIContainer()
        container.register_class(Repository, Repository)
        container.compile()

        assert container.has_provider(Repository) is True
        assert container.has_provider("nope") is False

    def test_get_provider_returns_the_row(self) -> None:
        """The row is what a wrapper reports in diagnostics."""
        container = DIContainer()
        container.register_class(Repository, Repository)
        container.compile()

        assert isinstance(container.get_provider(Repository), Provider)

    def test_get_provider_raises_for_an_unknown_token(self) -> None:
        """Asking for a row that does not exist is the same error as resolving
        it: the wrapper should not have to translate.
        """
        container = DIContainer()
        container.compile()

        with pytest.raises(MissingProviderError):
            container.get_provider("nope")

    def test_all_providers_lists_every_row(self) -> None:
        """The inventory size is what a wiring assertion checks."""
        container = DIContainer()
        container.register_class(Repository, Repository)
        container.register_value("config", {})
        container.compile()

        assert len(container.all_providers()) == 2

    def test_explain_describes_a_known_token(self) -> None:
        """``explain`` renders a human description for error messages."""
        container = DIContainer()
        container.register_class(Repository, Repository)
        container.compile()

        assert Repository.__name__ in container.explain(Repository)

    def test_explain_tolerates_an_unknown_token(self) -> None:
        """Diagnostics must work *before* the provider is registered, which is
        exactly when a user asks why resolution failed.
        """
        container = DIContainer()
        container.compile()

        assert "nope" in container.explain("nope")

    def test_instance_index_tracks_live_singletons(self) -> None:
        """The index is what teardown walks, so it must fill on resolve."""
        container = DIContainer()
        container.register_class(Repository, Repository)
        container.compile()
        asyncio.run(container.resolve(Repository))

        assert len(container.instance_index()) == 1


class TestResolveOverloads:
    """``resolve`` accepts per-call values and a request cache.

    Host adapters thread a request-local cache and explicit values through
    resolution instead of threading a scope object into every consumer.
    """

    def test_values_argument_overrides_a_registered_value(self) -> None:
        """``values`` is the per-call injection point (e.g. request data)."""
        container = DIContainer()
        container.register_value("user", "anonymous")
        container.compile()

        assert asyncio.run(container.resolve("user", values={"user": "ada"})) == "ada"

    def test_request_cache_is_honoured_for_the_duration_of_a_request(self) -> None:
        """A request-scoped instance resolved through the same cache is shared."""
        container = DIContainer()
        container.register_class(Repository, Repository, scope=Scope.REQUEST)
        container.compile()

        async def scenario() -> tuple[Any, Any]:
            async with ResolutionScope(container) as scope:
                return await scope.resolve(Repository), await scope.resolve(Repository)

        first, second = asyncio.run(scenario())

        assert first is second
