"""Scopes, lifecycle hooks and teardown (PRD 03).

Every test drives the async surface through :func:`helpers.run`, so the real
``ResolutionScope`` context manager, the request cache and ``shutdown()`` are
exercised rather than mocked.
"""

from __future__ import annotations

import pytest
from lauren_di import (
    DIContainer,
    DIScopeViolationError,
    LifecycleViolationError,
    Scope,
    injectable,
    post_construct,
    pre_destruct,
)

from helpers import run

pytestmark = pytest.mark.integration


# -- scope vocabulary -------------------------------------------------------


def test_scope_values_are_ordered_narrowest_to_widest() -> None:
    assert Scope.TRANSIENT == 0
    assert Scope.REQUEST == 1
    assert Scope.SINGLETON == 2
    assert Scope.TRANSIENT < Scope.REQUEST < Scope.SINGLETON


def test_scope_label_is_the_lowercase_name() -> None:
    assert Scope.TRANSIENT.label == "transient"
    assert Scope.REQUEST.label == "request"
    assert Scope.SINGLETON.label == "singleton"
    assert str(Scope.REQUEST) == "request"
    assert repr(Scope.REQUEST) == "<Scope.REQUEST: 1>"


# -- lifetimes --------------------------------------------------------------


def test_request_instance_is_cached_per_scope_and_teardown_runs_once() -> None:
    events: list[str] = []

    @injectable(scope=Scope.REQUEST)
    def resource() -> object:
        events.append("setup")
        yield object()
        events.append("teardown")

    container = DIContainer()
    container.register(resource)

    async def scenario() -> tuple[bool, bool, list[str], list[str]]:
        async with container.scope() as scope:
            first = await scope.resolve(resource)
            second = await scope.resolve(resource)
            inside = list(events)
        return first is second, scope.closed, inside, list(events)

    same, closed, inside, after = run(scenario())
    assert same is True
    assert closed is True
    assert inside == ["setup"]
    assert after == ["setup", "teardown"]


def test_two_scopes_have_independent_request_caches() -> None:
    @injectable(scope=Scope.REQUEST)
    class PerRequest:
        pass

    container = DIContainer()
    container.register(PerRequest)

    async def scenario() -> tuple[bool, bool]:
        async with container.scope() as one:
            first = await one.resolve(PerRequest)
        async with container.scope() as two:
            second = await two.resolve(PerRequest)
        return first is second, first is not second

    different, check = run(scenario())
    assert different is False
    assert check is True


def test_singleton_is_shared_across_scopes() -> None:
    @injectable()
    class Shared:
        pass

    container = DIContainer()
    container.register(Shared)

    async def scenario() -> bool:
        async with container.scope() as one:
            first = await one.resolve(Shared)
        async with container.scope() as two:
            second = await two.resolve(Shared)
        return first is second

    assert run(scenario()) is True


def test_transient_is_fresh_on_every_resolution() -> None:
    @injectable(scope=Scope.TRANSIENT)
    class Fresh:
        pass

    container = DIContainer()
    container.register(Fresh)

    async def scenario() -> bool:
        async with container.scope() as scope:
            return await scope.resolve(Fresh) is not await scope.resolve(Fresh)

    assert run(scenario()) is True


# -- the scope matrix -------------------------------------------------------


def test_singleton_consumer_of_request_dependency_is_rejected() -> None:
    @injectable(scope=Scope.REQUEST)
    class RequestDep:
        pass

    @injectable()
    class SingletonConsumer:
        def __init__(self, dep: RequestDep) -> None:
            self.dep = dep

    container = DIContainer()
    container.register(RequestDep)
    container.register(SingletonConsumer)

    with pytest.raises(DIScopeViolationError) as caught:
        container.compile()
    assert caught.value.detail["dependent"] == "SingletonConsumer"
    assert caught.value.detail["dependency"] == "RequestDep"
    assert caught.value.detail["dependent_scope"] == "singleton"
    assert caught.value.detail["dependency_scope"] == "request"


def test_transient_consumer_may_widen_to_request_and_singleton() -> None:
    @injectable(scope=Scope.REQUEST)
    class RequestDep:
        pass

    @injectable()
    class SingletonDep:
        pass

    @injectable(scope=Scope.TRANSIENT)
    class TransientConsumer:
        def __init__(self, request_dep: RequestDep, singleton_dep: SingletonDep) -> None:
            self.request_dep = request_dep
            self.singleton_dep = singleton_dep

    container = DIContainer()
    container.register(RequestDep)
    container.register(SingletonDep)
    container.register(TransientConsumer)
    container.compile()

    async def scenario() -> bool:
        async with container.scope() as scope:
            consumer = await scope.resolve(TransientConsumer)
            return isinstance(consumer.request_dep, RequestDep)

    assert run(scenario()) is True


# -- strict vs lax ----------------------------------------------------------


def test_strict_container_refuses_request_scope_outside_a_scope() -> None:
    @injectable(scope=Scope.REQUEST)
    class PerRequest:
        pass

    container = DIContainer(strict_request_scope=True)
    container.register(PerRequest)

    with pytest.raises(DIScopeViolationError) as caught:
        run(container.resolve(PerRequest))
    assert caught.value.detail["token"] == "PerRequest"
    assert caught.value.detail["reason"] == "no_active_scope"


def test_lax_container_builds_untracked_request_instances() -> None:
    @injectable(scope=Scope.REQUEST)
    class PerRequest:
        pass

    container = DIContainer()
    container.register(PerRequest)

    async def scenario() -> bool:
        return await container.resolve(PerRequest) is not await container.resolve(PerRequest)

    assert run(scenario()) is True


# -- scope values -----------------------------------------------------------


def test_scope_exposes_host_values_to_resolution() -> None:
    @injectable()
    class Handler:
        def __init__(self, request: str) -> None:
            self.request = request

    container = DIContainer(value_tokens=[str])
    container.register(Handler)

    async def scenario() -> tuple[str, str]:
        async with container.scope(values={str: "host-value"}) as scope:
            return await scope.resolve(str), (await scope.resolve(Handler)).request

    direct, injected = run(scenario())
    assert direct == "host-value"
    assert injected == "host-value"


# -- closing a scope --------------------------------------------------------


def test_resolving_inside_a_closed_scope_is_a_lifecycle_violation() -> None:
    @injectable(scope=Scope.REQUEST)
    class PerRequest:
        pass

    container = DIContainer()
    container.register(PerRequest)

    async def scenario() -> int:
        scope = container.scope()
        await scope.close()
        await scope.close()  # idempotent: closing twice is a no-op
        with pytest.raises(LifecycleViolationError) as caught:
            await scope.resolve(PerRequest)
        assert caught.value.detail["state"] == "closed"
        assert caught.value.detail["origin"] == "scope"
        return 1

    assert run(scenario()) == 1


# -- lifecycle hooks --------------------------------------------------------


def test_post_construct_runs_base_first_and_pre_destruct_subclass_first() -> None:
    events: list[str] = []

    @injectable()
    class Base:
        @post_construct
        def base_post(self) -> None:
            events.append("base.post")

        @pre_destruct
        def base_pre(self) -> None:
            events.append("base.pre")

    @injectable()
    class Sub(Base):
        @post_construct
        def sub_post(self) -> None:
            events.append("sub.post")

        @pre_destruct
        def sub_pre(self) -> None:
            events.append("sub.pre")

    container = DIContainer()
    container.register(Base)
    container.register(Sub)

    async def scenario() -> list[str]:
        await container.startup()
        assert events == ["base.post", "base.post", "sub.post"]
        events.clear()
        await container.shutdown()
        return events

    # Newest singleton first (Sub), then Base; each instance subclasses first.
    assert run(scenario()) == ["sub.pre", "base.pre", "base.pre"]


def test_singleton_post_construct_fires_at_startup_and_not_again() -> None:
    events: list[str] = []

    @injectable()
    class Single:
        @post_construct
        def ready(self) -> None:
            events.append("post")

    container = DIContainer()
    container.register(Single)

    async def scenario() -> int:
        await container.startup()
        await container.resolve(Single)
        await container.resolve(Single)
        return len(events)

    assert run(scenario()) == 1


def test_request_scoped_post_construct_fires_per_scope() -> None:
    events: list[str] = []

    @injectable(scope=Scope.REQUEST)
    class PerRequest:
        @post_construct
        def ready(self) -> None:
            events.append("post")

    container = DIContainer()
    container.register(PerRequest)

    async def scenario() -> int:
        async with container.scope() as one:
            await one.resolve(PerRequest)
            await one.resolve(PerRequest)
        async with container.scope() as two:
            await two.resolve(PerRequest)
        return len(events)

    assert run(scenario()) == 2


# -- teardown ordering and containment --------------------------------------


def test_scope_teardown_is_reverse_creation_order() -> None:
    events: list[str] = []

    @injectable(scope=Scope.REQUEST)
    class First:
        @pre_destruct
        def done(self) -> None:
            events.append("first")

    @injectable(scope=Scope.REQUEST)
    class Second:
        @pre_destruct
        def done(self) -> None:
            events.append("second")

    container = DIContainer()
    container.register(First)
    container.register(Second)

    async def scenario() -> list[str]:
        async with container.scope() as scope:
            await scope.resolve(First)
            await scope.resolve(Second)
        return events

    assert run(scenario()) == ["second", "first"]


def test_failing_pre_destruct_is_collected_and_teardown_continues() -> None:
    events: list[str] = []

    @injectable(scope=Scope.REQUEST)
    class Bad:
        @pre_destruct
        def boom(self) -> None:
            events.append("bad")
            raise RuntimeError("boom")

    @injectable(scope=Scope.REQUEST)
    class Good:
        @pre_destruct
        def done(self) -> None:
            events.append("good")

    container = DIContainer()
    container.register(Bad)
    container.register(Good)

    async def scenario() -> tuple[list[str], list[object]]:
        async with container.scope() as scope:
            await scope.resolve(Bad)
            await scope.resolve(Good)
        return events, list(scope.errors)

    order, errors = run(scenario())
    # Good was created last, so it tears down first; the failure in Bad is
    # recorded but does not abort the remaining teardown (newest -> oldest).
    assert order == ["good", "bad"]
    assert len(errors) == 1
    error = errors[0]
    assert type(error).__name__ == "DestructError"
    assert error.detail["stage"] == "pre_destruct"
    assert error.detail["origin"] == "scope"
    assert error.detail["cause"] == "boom"
    assert isinstance(error.__cause__, RuntimeError)


def test_shutdown_tears_singletons_down_newest_first() -> None:
    events: list[str] = []

    @injectable()
    class First:
        @pre_destruct
        def done(self) -> None:
            events.append("first")

    @injectable()
    class Second:
        @pre_destruct
        def done(self) -> None:
            events.append("second")

    container = DIContainer()
    container.register(First)
    container.register(Second)

    async def scenario() -> tuple[list[str], tuple[object, ...]]:
        await container.startup()
        errors = await container.shutdown()
        return events, errors

    order, errors = run(scenario())
    assert order == ["second", "first"]
    assert errors == ()


def test_transient_instances_are_never_tracked_for_teardown() -> None:
    events: list[str] = []

    @injectable(scope=Scope.TRANSIENT)
    class Transient:
        @pre_destruct
        def done(self) -> None:
            events.append("transient")

    container = DIContainer()
    container.register(Transient)

    async def scenario() -> list[str]:
        async with container.scope() as scope:
            await scope.resolve(Transient)
        await container.shutdown()
        return events

    assert run(scenario()) == []
