"""Build-time overrides: the five kinds, validation and isolation (PRD 07)."""

from __future__ import annotations

import asyncio

import pytest
from lauren_di import (
    DIContainer,
    OptionalDep,
    OverrideError,
    OverridePlan,
    ProviderOverride,
    Scope,
    active_override_plan,
    building_with,
    injectable,
)

from helpers import run

pytestmark = pytest.mark.integration


@injectable()
class Clock:
    def now(self) -> str:
        return "real"


class FrozenClock:
    def now(self) -> str:
        return "frozen"


@injectable()
class Repo:
    pass


# -- the five kinds ---------------------------------------------------------


def test_value_override_replaces_the_row() -> None:
    plan = OverridePlan({Clock: ProviderOverride(token=Clock, kind="value", value=FrozenClock())})
    container = DIContainer(overrides=plan)
    container.register(Clock)
    container.compile()

    assert run(container.resolve(Clock)).now() == "frozen"
    assert container.applied_overrides() == frozenset({Clock})


def test_values_override_replaces_a_whole_multi_binding() -> None:
    @injectable(multi=True)
    class Plugin:
        def __init__(self, name: str = "real") -> None:
            self.name = name

    plan = OverridePlan(
        {
            Plugin: ProviderOverride(
                token=Plugin,
                kind="values",
                values=(Plugin("A"), Plugin("B")),
            )
        }
    )
    container = DIContainer(overrides=plan)
    container.register(Plugin)
    container.compile()

    assert [plugin.name for plugin in run(container.resolve(list[Plugin]))] == ["A", "B"]


def test_class_override_is_built_through_di() -> None:
    class FakeRepo:
        def __init__(self, clock: Clock) -> None:
            self.clock = clock

    plan = OverridePlan({Repo: ProviderOverride(token=Repo, kind="class", use_class=FakeRepo)})
    container = DIContainer(overrides=plan)
    container.register(Clock)
    container.register(Repo)
    container.compile()

    resolved = run(container.resolve(Repo))
    assert isinstance(resolved, FakeRepo)
    assert isinstance(resolved.clock, Clock)


def test_factory_override_passes_injects_positionally() -> None:
    def make(clock: Clock, optional: object | None) -> tuple[str, object]:
        return (clock.now(), optional)

    plan = OverridePlan(
        {
            Repo: ProviderOverride(
                token=Repo,
                kind="factory",
                factory=make,
                injects=(Clock, OptionalDep("MISSING")),
            )
        }
    )
    container = DIContainer(overrides=plan)
    container.register(Clock)
    container.register(Repo)
    container.compile()

    assert run(container.resolve(Repo)) == ("real", None)


def test_existing_override_forwards_to_the_named_token() -> None:
    @injectable()
    class Impl:
        pass

    @injectable()
    class Service:
        pass

    plan = OverridePlan({Service: ProviderOverride(token=Service, kind="existing", existing=Impl)})
    container = DIContainer(overrides=plan)
    container.register(Impl)
    container.register(Service)
    container.compile()

    assert isinstance(run(container.resolve(Service)), Impl)


def test_value_override_keeps_the_multi_flag_and_provides_binding() -> None:
    class Sender:
        pass

    class FakeSender:
        pass

    @injectable(multi=True, provides=[Sender])
    class Smtp:
        pass

    plan = OverridePlan({Sender: ProviderOverride(token=Sender, kind="value", value=FakeSender())})
    container = DIContainer(overrides=plan)
    container.register(Smtp)
    container.compile()

    assert isinstance(run(container.resolve(Sender)), FakeSender)


# -- validation -------------------------------------------------------------


def test_values_on_a_single_row_is_refused() -> None:
    plan = OverridePlan({Repo: ProviderOverride(token=Repo, kind="values", values=(Repo(),))})
    container = DIContainer(overrides=plan)

    with pytest.raises(OverrideError) as caught:
        container.register(Repo)
    assert caught.value.detail["reason"] == "non_multi_values"


def test_scope_may_narrow_but_never_widen() -> None:
    plan = OverridePlan(
        {Repo: ProviderOverride(token=Repo, kind="class", use_class=Repo, scope=Scope.REQUEST)}
    )
    container = DIContainer(overrides=plan)
    container.register(Repo)

    async def scenario() -> bool:
        async with container.scope() as scope:
            return isinstance(await scope.resolve(Repo), Repo)

    assert run(scenario()) is True

    @injectable(scope=Scope.REQUEST)
    class RequestRow:
        pass

    widening = OverridePlan(
        {
            RequestRow: ProviderOverride(
                token=RequestRow, kind="value", value=object(), scope=Scope.SINGLETON
            )
        }
    )
    widened_container = DIContainer(overrides=widening)
    with pytest.raises(OverrideError) as caught:
        widened_container.register(RequestRow)
    assert caught.value.detail["reason"] == "scope_widen"


def test_unmatched_non_optional_override_fails_compile() -> None:
    class Unused:
        pass

    plan = OverridePlan({Unused: ProviderOverride(token=Unused, kind="value", value=object())})
    container = DIContainer(overrides=plan)
    container.register(Repo)

    with pytest.raises(OverrideError) as caught:
        container.compile()
    assert caught.value.detail["reason"] == "target_missing"
    assert caught.value.detail["token"] == "Unused"


def test_optional_override_that_matches_nothing_is_ignored() -> None:
    class Unused:
        pass

    plan = OverridePlan(
        {Unused: ProviderOverride(token=Unused, kind="value", value=object(), optional=True)}
    )
    container = DIContainer(overrides=plan)
    container.register(Repo)
    container.compile()

    assert container.applied_overrides() == frozenset()
    assert isinstance(run(container.resolve(Repo)), Repo)


# -- activation and isolation ----------------------------------------------


def test_building_with_activates_the_plan_for_the_build() -> None:
    plan = OverridePlan({Clock: ProviderOverride(token=Clock, kind="value", value=FrozenClock())})
    assert active_override_plan() is None
    with building_with(plan):
        assert active_override_plan() is plan
        container = DIContainer()
        container.register(Clock)
    assert active_override_plan() is None
    assert run(container.resolve(Clock)).now() == "frozen"


def test_nested_building_with_restores_the_outer_plan() -> None:
    outer = OverridePlan({Clock: ProviderOverride(token=Clock, kind="value", value=FrozenClock())})
    inner = OverridePlan({Repo: ProviderOverride(token=Repo, kind="value", value="inner")})

    with building_with(outer):
        assert active_override_plan() is outer
        with building_with(inner):
            assert active_override_plan() is inner
        assert active_override_plan() is outer
    assert active_override_plan() is None


def test_concurrent_tasks_do_not_see_each_others_plans() -> None:
    @injectable()
    class Value:
        pass

    async def build(tag: str) -> str:
        plan = OverridePlan({Value: ProviderOverride(token=Value, kind="value", value=tag)})
        with building_with(plan):
            await asyncio.sleep(0)
            container = DIContainer()
            container.register(Value)
            await asyncio.sleep(0)
            return await container.resolve(Value)

    async def scenario() -> list[str]:
        return list(await asyncio.gather(build("one"), build("two")))

    assert run(scenario()) == ["one", "two"]


def test_one_builds_overrides_never_leak_into_the_next() -> None:
    first = OverridePlan({Clock: ProviderOverride(token=Clock, kind="value", value=FrozenClock())})
    with building_with(first):
        overridden = DIContainer()
        overridden.register(Clock)

    plain = DIContainer()
    plain.register(Clock)

    assert run(overridden.resolve(Clock)).now() == "frozen"
    assert run(plain.resolve(Clock)).now() == "real"
    assert plain.applied_overrides() == frozenset()
