"""Registration: the decorator, the ``Provider`` record, and its rejection rules."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from typing import Annotated, Protocol, runtime_checkable

import pytest
from lauren_di import (
    CircularDependencyError,
    DecoratorUsageError,
    DIContainer,
    DIScopeViolationError,
    DuplicateBindingError,
    Inject,
    LifecycleConfigError,
    MetadataInheritanceError,
    MissingProviderError,
    Provider,
    Scope,
    injectable,
    injectable_meta,
    post_construct,
    pre_destruct,
    use_class,
    use_existing,
    use_factory,
    use_value,
)

from helpers import run

pytestmark = pytest.mark.unit


# -- the decorator -----------------------------------------------------------


def test_injectable_records_default_metadata() -> None:
    @injectable()
    class Repo:
        pass

    meta = injectable_meta(Repo)
    assert meta is not None
    assert meta.scope is Scope.SINGLETON
    assert meta.provides == ()
    assert meta.multi is False


def test_injectable_records_explicit_metadata() -> None:
    @injectable(scope=Scope.REQUEST, provides=[bytes], multi=True)
    class Repo:
        pass

    meta = injectable_meta(Repo)
    assert meta is not None
    assert meta.scope is Scope.REQUEST
    assert meta.provides == (bytes,)
    assert meta.multi is True


def test_bare_injectable_is_rejected() -> None:
    with pytest.raises(DecoratorUsageError) as caught:
        injectable(str)  # type: ignore[call-arg]

    assert caught.value.detail["decorator"] == "injectable"
    assert caught.value.detail["target"] == "str"
    assert "must be called" in caught.value.message


def test_primitives_are_never_injectable() -> None:
    assert injectable_meta(str) is None
    assert injectable_meta(object()) is None


def test_inherited_metadata_is_rejected() -> None:
    @injectable()
    class Base:
        pass

    class Child(Base):
        pass

    with pytest.raises(MetadataInheritanceError) as caught:
        DIContainer().register(Child)

    assert caught.value.detail == {"target": "Child", "base": "Base"}
    assert "inherited injectable metadata" in caught.value.message


def test_lifecycle_decorators_mark_their_methods() -> None:
    class Service:
        @post_construct
        def up(self) -> None:
            pass

        @pre_destruct
        def down(self) -> None:
            pass

    assert Service.up.__lauren_di_post_construct__ is True  # type: ignore[attr-defined]
    assert Service.down.__lauren_di_pre_destruct__ is True  # type: ignore[attr-defined]


# -- the Provider record -----------------------------------------------------


def test_register_returns_a_finalised_value_record() -> None:
    @injectable()
    class Repo:
        pass

    provider = DIContainer().register(Repo)

    assert isinstance(provider, Provider)
    assert provider.cls is Repo
    assert provider.scope is Scope.SINGLETON
    assert provider.owning_module is None
    assert provider.multi is False
    assert provider.provides == ()
    # The container finalises the record: the factory is the class itself,
    # so the resolver never has to ask "class or function?" (PRD 02 §5.4).
    assert provider.factory is Repo
    assert provider.provider_kind is None


def test_register_records_the_owning_module() -> None:
    class Billing:
        pass

    @injectable()
    class Repo:
        pass

    provider = DIContainer().register(Repo, owning_module=Billing)
    assert provider.owning_module is Billing


def test_registering_the_same_token_twice_is_rejected() -> None:
    @injectable()
    class Repo:
        pass

    container = DIContainer()
    container.register(Repo)

    with pytest.raises(DuplicateBindingError) as caught:
        container.register(Repo)

    assert caught.value.detail == {"token": "Repo", "existing": "Repo", "new": "Repo"}
    assert caught.value.message == "Repo is already registered"


def test_register_requires_the_decorator() -> None:
    class Plain:
        pass

    with pytest.raises(MissingProviderError) as caught:
        DIContainer().register(Plain)

    assert caught.value.detail == {"token": "Plain"}
    assert caught.value.message == "Plain is not marked @injectable"


def test_non_class_owner_is_rejected_at_registration() -> None:
    @injectable()
    class Repo:
        pass

    with pytest.raises(DecoratorUsageError) as caught:
        DIContainer().register(Repo, owning_module="Billing")  # type: ignore[arg-type]

    assert caught.value.detail == {"token": "Repo", "owning_module": "'Billing'"}


def test_provider_records_constructor_dependencies() -> None:
    @injectable()
    class Repo:
        pass

    @injectable()
    class Service:
        def __init__(self, repo: Repo) -> None:
            self.repo = repo

    provider = DIContainer().register(Service)
    assert provider.deps == (("repo", Repo),)


def test_provider_records_field_dependencies() -> None:
    @injectable()
    class Repo:
        pass

    @injectable()
    class Service:
        repo: Repo

    provider = DIContainer().register(Service)
    assert provider.field_deps == (("repo", Repo),)
    assert provider.deps == ()


def test_structural_protocols_are_bindable_tokens() -> None:
    @runtime_checkable
    class Store(Protocol):
        def total(self) -> int: ...

    @injectable(provides=[Store])
    class DictStore:
        def total(self) -> int:
            return 1

    container = DIContainer()
    container.register(DictStore)

    assert container.has_provider(Store) is True
    assert type(run(container.resolve(Store))) is DictStore


# -- generator factories: ``use_factory`` and ``register`` agree (PRD 02 §5.8) --


def test_use_factory_drives_a_generator_and_tears_it_down() -> None:
    """A generator factory is driven, not handed over as a suspended generator.

    Before this rule the factory's return value was the *generator object*, so
    consumers received ``<generator object ...>`` and the ``finally`` never ran.
    """
    log: list[str] = []

    def pool() -> Iterator[str]:
        log.append("setup")
        try:
            yield "pool-value"
        finally:
            log.append("teardown")

    container = DIContainer()
    provider = container.register_custom(use_factory(provide="POOL", factory=pool))
    container.compile()

    assert provider.is_generator_provider is True
    assert run(container.resolve("POOL")) == "pool-value"
    assert log == ["setup"], "the code before `yield` is the setup half"
    assert run(container.shutdown()) == ()
    assert log == ["setup", "teardown"], "shutdown() must run the code after `yield`"


def test_use_factory_drives_an_async_generator() -> None:
    log: list[str] = []

    async def pool() -> AsyncIterator[str]:
        log.append("setup")
        try:
            yield "async-pool"
        finally:
            log.append("teardown")

    container = DIContainer()
    provider = container.register_custom(use_factory(provide="APOOL", factory=pool))
    container.compile()

    assert provider.is_generator_provider is True
    assert run(container.resolve("APOOL")) == "async-pool"
    run(container.shutdown())
    assert log == ["setup", "teardown"]


def test_use_factory_generator_tears_down_at_scope_exit() -> None:
    log: list[str] = []

    def session() -> Iterator[str]:
        log.append("setup")
        try:
            yield "session-obj"
        finally:
            log.append("teardown")

    container = DIContainer()
    container.register_custom(use_factory(provide="SESSION", factory=session, scope=Scope.REQUEST))
    container.compile()

    async def body() -> str:
        async with container.scope() as scope:
            value = await scope.resolve("SESSION")
            assert log == ["setup"], "a request-scoped record tears down at scope exit"
            return str(value)

    assert run(body()) == "session-obj"
    assert log == ["setup", "teardown"]


def test_use_factory_rejects_a_transient_generator() -> None:
    def pool() -> Iterator[str]:
        yield "pool"

    with pytest.raises(LifecycleConfigError) as caught:
        DIContainer().register_custom(
            use_factory(provide="POOL", factory=pool, scope=Scope.TRANSIENT)
        )

    assert caught.value.detail == {"provider": "pool"}
    assert "cannot use Scope.TRANSIENT" in caught.value.message


def test_use_factory_leaves_a_plain_factory_alone() -> None:
    """The generator rule must not turn an ordinary factory into a generator."""

    async def build() -> int:
        return 7

    container = DIContainer()
    plain = container.register_custom(use_factory(provide="N", factory=lambda: 42))
    coro = container.register_custom(use_factory(provide="C", factory=build))
    container.compile()

    assert plain.is_generator_provider is False
    assert coro.is_generator_provider is False
    assert run(container.resolve("N")) == 42
    assert run(container.resolve("C")) == 7


def test_use_factory_docstring_states_the_generator_rule() -> None:
    """The docstring and the behaviour above must state one rule, not two.

    A rule that only lives in a test is a rule a reader never sees; a rule that
    only lives in prose is a rule nothing checks. This pins the wording the
    tests above rely on.
    """
    doc = use_factory.__doc__ or ""
    assert "setup/teardown" in doc
    assert "Scope.TRANSIENT" in doc
    assert "LifecycleConfigError" in doc
    assert "register_factory" in doc


# -- the other three recipes: what ``register_custom`` dispatches to (PRD 04 §5.4) --


def test_use_value_returns_the_very_object_it_was_given() -> None:
    """``useValue`` binds a literal: identity, not a copy, and never rebuilt.

    The value is placed in the singleton cache at registration time (PRD 04
    §5.4 rule 2), so a resolve cannot construct a second object. That is what
    makes the recipe usable for mocks and for objects built outside the
    container, and it is why ``use_value`` takes no ``scope``.
    """
    settings = {"debug": True}
    container = DIContainer()
    provider = container.register_custom(use_value(provide="SETTINGS", value=settings))
    container.compile()

    assert provider.provider_kind == "value"
    assert provider.scope is Scope.SINGLETON
    assert "SETTINGS" in container.singletons(), "a value is already a cache entry"

    first = run(container.resolve("SETTINGS"))
    second = run(container.resolve("SETTINGS"))
    assert first is settings
    assert second is settings


def test_use_value_multi_collects_every_value_in_registration_order() -> None:
    """``multi=True`` gives each value its own cache slot (PRD 04 §5.4 rule 3).

    Keyed by token, the first value would claim the slot and every sibling
    would return it; the container keys a multi row by ``id(provider)``
    instead, so both values stay reachable in the order they were registered.
    """

    class Plugin:
        pass

    first, second = Plugin(), Plugin()
    container = DIContainer()
    container.register_custom(use_value(provide=Plugin, value=first, multi=True))
    container.register_custom(use_value(provide=Plugin, value=second, multi=True))
    container.compile()

    collected = run(container.resolve(list[Plugin]))
    assert len(collected) == 2, f"both multi values must be collected, got {collected!r}"
    assert collected[0] is first
    assert collected[1] is second


def test_use_class_binds_a_class_the_token_does_not_name() -> None:
    """``useClass`` binds ``provide`` to ``use``, and ``use`` needs no decorator.

    Swapping the implementation per environment is the whole point of the
    recipe, so the bound class is auto-marked with the record's scope on the
    way in (PRD 04 §5.4 rule 6) rather than demanding ``@injectable`` from a
    caller who already opted in by naming it.
    """

    class Clock:
        pass

    class FixedClock:
        def read(self) -> str:
            return "12:00"

    container = DIContainer()
    provider = container.register_custom(use_class(provide=Clock, use=FixedClock))
    container.compile()

    assert provider.provider_kind == "class"
    assert provider.cls is Clock, "the token owns the row, not the bound class"
    assert injectable_meta(FixedClock) is not None, "the bound class is marked on the way in"

    clock = run(container.resolve(Clock))
    assert type(clock) is FixedClock
    assert run(container.resolve(Clock)) is clock, "the default scope is singleton"


def test_use_class_honours_the_scope_it_was_given() -> None:
    """``scope`` reaches the row: a request-scoped binding is rebuilt per scope.

    Dropping the argument in ``register_custom`` would leave every ``use_class``
    registration a singleton, so the scope is asserted twice — on the provider
    and on the instances the scopes produce.
    """

    class Clock:
        pass

    class FixedClock:
        pass

    container = DIContainer()
    provider = container.register_custom(
        use_class(provide=Clock, use=FixedClock, scope=Scope.REQUEST)
    )
    container.compile()

    assert provider.scope is Scope.REQUEST

    async def body() -> tuple[object, object]:
        async with container.scope() as first_scope:
            first = await first_scope.resolve(Clock)
        async with container.scope() as second_scope:
            second = await second_scope.resolve(Clock)
        return first, second

    first, second = run(body())
    assert type(first) is FixedClock
    assert type(second) is FixedClock
    assert first is not second, "a request-scoped binding is one instance per scope"


def test_use_class_constructs_through_the_di_pipeline() -> None:
    """The bound class's own ``__init__`` parameters resolve like any provider's.

    ``use_class`` is a rebinding, not an escape hatch: a class that takes a
    collaborator still gets it injected, which is what separates it from
    ``use_value(provide=..., value=instance)``.
    """

    @injectable()
    class Logger:
        pass

    class Service:
        def __init__(self, logger: Logger) -> None:
            self.logger = logger

    container = DIContainer()
    container.register(Logger)
    container.register_custom(use_class(provide="SERVICE", use=Service))
    container.compile()

    service = run(container.resolve("SERVICE"))
    assert type(service) is Service
    assert isinstance(service.logger, Logger)


def test_use_existing_forwards_one_instance_through_a_chain() -> None:
    """An alias owns no instance: every link resolves to the target's object.

    The chain is followed to the terminal provider (PRD 04 §5.5 rule 3), and
    ``singletons()`` gains no entry for either alias (rule 1) — which is what
    distinguishes forwarding from registering the value a second time.
    """

    @injectable()
    class Logger:
        pass

    container = DIContainer()
    container.register(Logger)
    provider = container.register_custom(use_existing(provide="LOGGER", existing=Logger))
    container.register_custom(use_existing(provide="LEGACY", existing="LOGGER"))
    container.compile()

    direct = run(container.resolve(Logger))
    alias = run(container.resolve("LOGGER"))
    chained = run(container.resolve("LEGACY"))

    assert isinstance(direct, Logger)
    assert direct is alias, "one link must forward, not rebuild"
    assert direct is chained, "a chain of two links must forward too"
    assert provider.provider_kind == "existing"
    assert "LOGGER" not in container.singletons(), "an alias never owns an instance"
    assert "LEGACY" not in container.singletons()


def test_use_existing_refuses_a_cycle_at_compile() -> None:
    """A two-token alias cycle is caught by validation, not by recursing.

    ``compile()`` alone must raise: the alias chain joins the same cycle
    detection as provider edges (PRD 04 §5.5 rule 5), so the failure names the
    loop instead of ending in a ``RecursionError`` on the first resolve.
    """
    container = DIContainer()
    container.register_custom(use_existing(provide="A", existing="B"))
    container.register_custom(use_existing(provide="B", existing="A"))

    with pytest.raises(CircularDependencyError) as caught:
        container.compile()

    assert caught.value.detail == {"cycle": ["A", "B", "A"]}


def test_use_existing_refuses_a_target_nobody_registered() -> None:
    """An alias forwards to ``existing``; it never resolves its own token.

    If the ``existing`` branch were lowered to ``register_value`` — or resolved
    ``provide`` instead of ``existing`` — this registration would bind
    something instead of reporting the broken link (PRD 04 §5.5 rule 4).
    """
    container = DIContainer()
    container.register_custom(use_existing(provide="X", existing="NOPE"))

    with pytest.raises(MissingProviderError) as caught:
        container.compile()

    assert caught.value.detail == {"alias": "X", "target": "NOPE"}


# -- D-27: an alias is transparent to scope validation -----------------------


def test_alias_edge_is_checked_against_the_terminal_scope() -> None:
    """D-27: an alias must not smuggle a request dependency into a singleton.

    The edge is judged by the **terminal** target's scope, not by the alias
    row's placeholder, so this wiring raises exactly as the direct
    ``dep: RequestScoped`` parameter does (PRD 04 §5.5 rule 2, §6.4 row 3).
    Before D-27 the row stayed ``SINGLETON`` and ``compile()`` returned quietly,
    which is the captured-dependency bug PRD 03's narrowing rule exists to
    prevent -- reachable by adding one indirection.
    """

    @injectable(scope=Scope.REQUEST)
    class RequestScoped:
        pass

    @injectable()
    class Consumer:
        def __init__(self, dep: Annotated[RequestScoped, Inject("ALIAS")]) -> None:
            self.dep = dep

    container = DIContainer()
    container.register(RequestScoped)
    container.register_alias("ALIAS", RequestScoped)
    container.register(Consumer)

    with pytest.raises(DIScopeViolationError) as caught:
        container.compile()

    detail = caught.value.detail
    assert detail["dependent"] == "Consumer"
    assert detail["dependent_scope"] == "singleton"
    assert detail["dependency_scope"] == "request", "the terminal target's scope, not the row's"
    assert detail["dependency"] == "RequestScoped"
    assert detail["alias"] == "ALIAS"


def test_alias_chain_is_walked_to_the_terminal_provider() -> None:
    """A two-link chain is judged by the provider at the end of it.

    The consumer is registered **first** on purpose: ``compile`` visits
    providers in registration order, so an implementation that read the row's
    stamped scope instead of walking the chain would pass or fail depending on
    registration order. The answer must not depend on it (PRD 04 §5.5 rules 2-3).
    """

    @injectable(scope=Scope.REQUEST)
    class RequestScoped:
        pass

    @injectable()
    class Consumer:
        def __init__(self, dep: Annotated[RequestScoped, Inject("LINK2")]) -> None:
            self.dep = dep

    container = DIContainer()
    container.register(Consumer)
    container.register(RequestScoped)
    container.register_alias("LINK1", RequestScoped)
    container.register_alias("LINK2", "LINK1")

    with pytest.raises(DIScopeViolationError) as caught:
        container.compile()

    detail = caught.value.detail
    assert detail["dependency"] == "RequestScoped"
    assert detail["dependency_scope"] == "request"
    assert detail["alias"] == "LINK2"


def test_alias_edge_may_widen_like_a_direct_one() -> None:
    """The rule is the same comparison, so a widening stays allowed.

    A ``TRANSIENT`` consumer may depend on a request-scoped provider whether the
    edge is direct or goes through an alias; a fix that rejected every alias
    edge would fail here (PRD 03 §5.2's nine cells).
    """

    @injectable(scope=Scope.REQUEST)
    class RequestScoped:
        pass

    @injectable(scope=Scope.TRANSIENT)
    class TransientConsumer:
        def __init__(self, dep: Annotated[RequestScoped, Inject("ALIAS")]) -> None:
            self.dep = dep

    container = DIContainer()
    container.register(RequestScoped)
    container.register_alias("ALIAS", RequestScoped)
    container.register(TransientConsumer)
    container.compile()

    async def scenario() -> bool:
        async with container.scope() as scope:
            consumer = await scope.resolve(TransientConsumer)
            return isinstance(consumer.dep, RequestScoped)

    assert run(scenario()) is True


def test_alias_row_reports_the_terminal_scope_after_validation() -> None:
    """``get_provider(alias).scope`` reports the terminal scope (PRD 04 §5.5 rule 2).

    Before validation the row still carries its placeholder, which is what makes
    "after validation" a contract rather than a hope; after it, a diagnostic
    cannot claim ``SINGLETON`` for a request-scoped target (the failure probe
    E-49 recorded).
    """

    @injectable(scope=Scope.REQUEST)
    class RequestScoped:
        pass

    @injectable(scope=Scope.TRANSIENT)
    class TransientConsumer:
        def __init__(self, dep: Annotated[RequestScoped, Inject("LINK2")]) -> None:
            self.dep = dep

    container = DIContainer()
    container.register(RequestScoped)
    container.register_alias("LINK1", RequestScoped)
    container.register_alias("LINK2", "LINK1")
    container.register(TransientConsumer)

    assert container.get_provider("LINK2").scope is Scope.SINGLETON, "placeholder until validated"

    container.compile()

    assert container.get_provider("LINK1").scope is Scope.REQUEST
    assert container.get_provider("LINK2").scope is Scope.REQUEST
    assert {p.cls for p in container.all_providers() if p.provider_kind == "existing"} == {
        "LINK1",
        "LINK2",
    }


def test_register_custom_records_the_kind_the_prd_table_names() -> None:
    """One dispatcher, four recipes: ``provider_kind`` names which one ran.

    PRD 04 §5.4's lowering table specifies this column, so a branch that
    dispatches to the wrong ``register_*`` method fails here rather than
    silently behaving like a neighbouring recipe.
    """

    class Thing:
        pass

    container = DIContainer()
    rows = {
        "value": container.register_custom(use_value(provide="V", value=1)),
        "class": container.register_custom(use_class(provide="C", use=Thing)),
        "factory": container.register_custom(use_factory(provide="F", factory=lambda: 2)),
        "existing": container.register_custom(use_existing(provide="E", existing="V")),
    }

    assert {kind: row.provider_kind for kind, row in rows.items()} == {kind: kind for kind in rows}
