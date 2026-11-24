"""End-to-end behaviour of the four custom-provider recipes.

The helpers in ``lauren_di/_custom.py`` only *describe* a provider; the
registry has to honour that description when it builds the row and the
resolver has to honour it again at resolution time.  These tests therefore
run against a compiled container and assert on resolved objects, which is the
only place the recipe contract (PRD 03 §custom providers, MV-11) is actually
observable.

They are integration tests because each one compiles a container and resolves
through the real dependency graph; a unit double of the registry would let a
recipe that is structurally valid but semantically wrong pass unnoticed.
"""

from __future__ import annotations

import asyncio
from typing import Annotated

import pytest
from lauren_di import (
    CustomProvider,
    DIContainer,
    DuplicateBindingError,
    Inject,
    InjectableMeta,
    MissingProviderError,
    OptionalDep,
    ResolutionScope,
    Scope,
    Token,
    injectable,
    injectable_meta,
    use_class,
    use_existing,
    use_factory,
    use_value,
)

pytestmark = pytest.mark.integration

# Module-level tokens: the annotations below are evaluated lazily (PEP 563),
# so the names must live in module globals to be resolvable.
_PRIMARY_DB = Token("primary-db", unique=False)
_REPLICA_DB = Token("replica-db", unique=False)


def _build(*providers: CustomProvider, **kwargs: object) -> DIContainer:
    """Register ``providers`` on a fresh container and compile it.

    Args:
        *providers: Custom providers to register in declaration order.
        **kwargs: Extra :class:`DIContainer` constructor arguments.

    Returns:
        The compiled container.
    """
    container = DIContainer(**kwargs)  # type: ignore[arg-type]
    for provider in providers:
        container.register_custom(provider)
    container.compile()
    return container


class TestUseValueRecipe:
    """``use_value`` must hand back the *same* object for every resolve.

    A value provider is how configuration and test doubles enter the graph;
    rebuilding the value per resolve would silently discard state that the
    caller populated before the build.
    """

    def test_value_is_resolved_verbatim(self) -> None:
        """The resolved object is the registered object, not a copy."""
        payload = {"dsn": "sqlite://"}
        container = _build(use_value(provide="config", value=payload))

        assert asyncio.run(container.resolve("config")) is payload

    def test_value_is_available_through_a_token_key(self) -> None:
        """``Token`` keys must behave exactly like string keys, otherwise a
        shared token could not publish a value across modules.
        """
        token = Token("dsn", unique=False)
        container = _build(use_value(provide=token, value="sqlite://"))

        assert asyncio.run(container.resolve(token)) == "sqlite://"

    def test_multi_values_collect_for_list_injection(self) -> None:
        """Two ``multi=True`` registrations of one token must collect into
        the ``list[T]`` injection point (MV-11 multiple binding).
        """

        class Plugin:
            pass

        first, second = Plugin(), Plugin()
        container = _build(
            use_value(provide="plugins", value=first, multi=True),
            use_value(provide="plugins", value=second, multi=True),
        )

        assert asyncio.run(container.resolve("plugins")) == [first, second]


class TestUseClassRecipe:
    """``use_class`` binds an abstract token to a concrete implementation.

    This is the recipe that makes interface-style injection possible, so the
    container must build the *implementation* while the consumer asks for the
    token.
    """

    def test_implementation_is_constructed_and_injected(self) -> None:
        """The consumer depends on the token; the instance is the impl."""

        class Store:
            pass

        class InMemoryStore(Store):
            pass

        @injectable()
        class Service:
            def __init__(self, store: Store) -> None:
                self.store = store

        container = DIContainer()
        container.register_custom(use_class(provide=Store, use=InMemoryStore))
        container.register_class(Service, Service)
        container.compile()

        resolved = asyncio.run(container.resolve(Service))

        assert isinstance(resolved.store, InMemoryStore)

    def test_request_scoped_class_provider_is_cached_per_scope(self) -> None:
        """``scope`` on the recipe is what keeps one instance per request;
        losing it would defeat ``ResolutionScope`` caching entirely.
        """

        class Store:
            pass

        class InMemoryStore(Store):
            pass

        container = DIContainer()
        container.register_custom(use_class(provide=Store, use=InMemoryStore, scope=Scope.REQUEST))
        container.compile()

        async def scenario() -> tuple[object, object]:
            async with ResolutionScope(container) as scope:
                return await scope.resolve(Store), await scope.resolve(Store)

        first, second = asyncio.run(scenario())

        assert first is second


class TestUseFactoryRecipe:
    """``use_factory`` runs a callable with a positional inject list.

    The recipe is the escape hatch for objects the DI graph cannot construct
    itself (third-party clients, values requiring ``await`` inside a factory),
    so the container must call it exactly once per scope and pass the
    resolved dependencies positionally.
    """

    def test_factory_receives_injected_tokens_in_order(self) -> None:
        """``injects`` is positional; a reordering would mis-wire the call."""
        calls: list[tuple[int, int]] = []

        def make(left: int, right: int) -> int:
            calls.append((left, right))
            return left + right

        container = DIContainer()
        container.register_value("left", 1)
        container.register_value("right", 41)
        container.register_custom(
            use_factory(provide="total", factory=make, injects=["left", "right"])
        )
        container.compile()

        assert asyncio.run(container.resolve("total")) == 42
        assert calls == [(1, 41)]

    def test_optional_inject_yields_none_when_absent(self) -> None:
        """``OptionalDep`` is the only way to express "resolve if present";
        a missing optional dependency must not fail the build.
        """
        container = DIContainer()
        container.register_custom(
            use_factory(
                provide="client",
                factory=lambda base_url: f"client({base_url})",
                injects=[OptionalDep("base_url")],
            )
        )
        container.compile()

        assert asyncio.run(container.resolve("client")) == "client(None)"

    def test_optional_inject_resolves_when_present(self) -> None:
        """The same declaration must pick the real dependency up once it is
        registered — the two branches of the feature are one contract.
        """
        container = DIContainer()
        container.register_value("base_url", "https://example.test")
        container.register_custom(
            use_factory(
                provide="client",
                factory=lambda base_url: f"client({base_url})",
                injects=[OptionalDep("base_url")],
            )
        )
        container.compile()

        assert asyncio.run(container.resolve("client")) == "client(https://example.test)"

    def test_factory_provider_is_cached_for_singleton_scope(self) -> None:
        """A singleton factory must run once, not once per resolve."""
        counter = {"n": 0}

        def make() -> object:
            counter["n"] += 1
            return object()

        container = _build(use_factory(provide="thing", factory=make))

        first = asyncio.run(container.resolve("thing"))
        second = asyncio.run(container.resolve("thing"))

        assert first is second
        assert counter["n"] == 1

    def test_missing_injected_dependency_fails_the_build(self) -> None:
        """A factory whose injects cannot be satisfied is a build-time error:
        discovering it on the first request would be far too late.
        """
        container = DIContainer()
        container.register_custom(
            use_factory(provide="client", factory=lambda x: x, injects=[object])
        )

        with pytest.raises(MissingProviderError):
            container.compile()


class TestUseExistingRecipe:
    """``use_existing`` registers a second name for one provider.

    Aliasing is how a module exposes a provider under a public token without
    re-declaring its lifecycle, so both names must resolve to one instance.
    """

    def test_alias_resolves_to_the_same_instance(self) -> None:
        """An alias is not a copy: lifecycle and state must be shared."""

        class Store:
            pass

        container = DIContainer()
        container.register_class(Store, Store)
        container.register_custom(use_existing(provide="store", existing=Store))
        container.compile()

        assert asyncio.run(container.resolve("store")) is asyncio.run(container.resolve(Store))


class TestRegistrationErrors:
    """The registry rejects a second binding for a singleton token.

    Duplicate detection is what makes "one provider per token" a build-time
    guarantee instead of a race between two modules (PRD 06 §duplicate
    binding).  Custom providers must go through the same gate as classes.
    """

    def test_duplicate_custom_binding_is_rejected(self) -> None:
        """Two ``use_value`` rows for one token cannot both win."""
        container = DIContainer()
        container.register_custom(use_value(provide="config", value=1))

        with pytest.raises(DuplicateBindingError):
            container.register_custom(use_value(provide="config", value=2))

    def test_unregistered_token_reports_missing_provider(self) -> None:
        """Resolving an unknown token must name the token, not raise
        ``KeyError`` from the internal dict.
        """
        container = DIContainer()
        container.compile()

        with pytest.raises(MissingProviderError):
            asyncio.run(container.resolve("nope"))


class TestAnnotatedInjection:
    """``Annotated[T, Inject(token)]`` redirects one parameter to a token.

    This is the only way to inject two instances of the same type into one
    constructor, so the marker must win over the plain annotation.
    """

    def test_inject_marker_overrides_the_annotation(self) -> None:
        """The parameter type is the token only when the marker is absent."""

        @injectable()
        class Service:
            def __init__(
                self,
                first: Annotated[str, Inject(_PRIMARY_DB)],
                second: Annotated[str, Inject(_REPLICA_DB)],
            ) -> None:
                self.first = first
                self.second = second

        container = DIContainer()
        container.register_value(_PRIMARY_DB, "primary")
        container.register_value(_REPLICA_DB, "replica")
        container.register_class(Service, Service)
        container.compile()

        resolved = asyncio.run(container.resolve(Service))

        assert (resolved.first, resolved.second) == ("primary", "replica")


class TestProviderMetadata:
    """A function provider published with ``provides=`` is keyed by the token.

    ``@injectable(provides=[...])`` lets a factory function claim tokens that
    are not its own signature, which is what makes module-style declarations
    possible.
    """

    def test_function_provider_is_reachable_by_its_provided_token(self) -> None:
        """``provides`` entries, not the function, become the DI keys."""

        @injectable(provides=["answer"])
        def build_answer() -> int:
            return 42

        container = DIContainer()
        container.register(build_answer)
        container.compile()

        assert asyncio.run(container.resolve("answer")) == 42

    def test_function_provider_metadata_is_attached(self) -> None:
        """The marker is attached to the callable so ``register`` can read it
        without a second argument list.
        """

        @injectable(provides=["answer"], scope=Scope.TRANSIENT)
        def build_answer() -> int:
            return 42

        meta = injectable_meta(build_answer)

        assert isinstance(meta, InjectableMeta)
        assert meta.scope is Scope.TRANSIENT
        assert meta.provides == ("answer",)
