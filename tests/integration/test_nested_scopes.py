"""Nested resolution scopes: read-through, teardown order and the depth limit (PRD 15).

A child scope is opened from a live scope (``scoped.scope()``), reads its
ancestors' ``REQUEST`` instances, caches only what it creates, and is closed by
its parent. Every case here is driven through the public surface, so the tests
say what PRD 15 §5 promises rather than what the implementation happens to do.

The tests use :func:`helpers.run` (``asyncio.run``) instead of a pytest-asyncio
marker, like the rest of the suite.
"""

from __future__ import annotations

import pytest
from lauren_di import (
    DIContainer,
    LifecycleConfigError,
    LifecycleViolationError,
    Scope,
    injectable,
    pre_destruct,
)

from helpers import run

pytestmark = pytest.mark.integration


# -- fixtures ---------------------------------------------------------------


@injectable(scope=Scope.REQUEST)
class Session:
    """A request-scoped object with no dependencies."""


@injectable(scope=Scope.REQUEST)
class Repo:
    """A request-scoped object that depends on the level's ``Session``."""

    def __init__(self, session: Session) -> None:
        self.session = session


@injectable(scope=Scope.SINGLETON)
class Settings:
    """A container-wide object: shared by every level."""


def container(**kwargs: object) -> DIContainer:
    """A container with the three providers above registered."""
    built = DIContainer(**kwargs)  # type: ignore[arg-type]
    built.register(Session)
    built.register(Repo)
    built.register(Settings)
    return built


# -- shape ------------------------------------------------------------------


def test_a_scope_can_open_a_child_scope() -> None:
    async def main() -> None:
        async with container().scope() as outer:
            child = outer.scope()
            assert child.parent is outer
            assert outer.parent is None
            assert (outer.depth, child.depth) == (1, 2)
            await child.close()

    run(main())


def test_a_child_is_an_async_context_manager_of_itself() -> None:
    async def main() -> None:
        async with container().scope() as outer:
            async with outer.scope() as child:
                assert child is not outer
                assert child.depth == 2
                assert child.closed is False
            assert child.closed is True
            assert outer.closed is False

    run(main())


# -- read-through -----------------------------------------------------------


def test_a_child_reuses_the_instance_its_parent_created() -> None:
    async def main() -> None:
        async with container().scope() as outer:
            session = await outer.resolve(Session)
            async with outer.scope() as child:
                assert await child.resolve(Session) is session

    run(main())


def test_a_child_caches_only_what_it_creates() -> None:
    async def main() -> None:
        async with container().scope() as outer:
            session = await outer.resolve(Session)
            async with outer.scope() as child:
                repo = await child.resolve(Repo)
                assert repo.session is session  # the parent's Session, not a new one
                assert await child.resolve(Repo) is repo
            # The child created Repo alone, so the parent has no Repo yet.
            parent_repo = await outer.resolve(Repo)
            assert parent_repo is not repo
            assert parent_repo.session is session

    run(main())


def test_read_through_is_transitive_through_every_ancestor() -> None:
    async def main() -> None:
        async with container().scope() as root:
            session = await root.resolve(Session)
            middle = root.scope()
            leaf = middle.scope()
            assert await leaf.resolve(Session) is session
            assert leaf.depth == 3
            await leaf.close()
            await middle.close()

    run(main())


def test_a_singleton_is_the_same_object_at_every_level() -> None:
    async def main() -> None:
        async with container().scope() as outer:
            settings = await outer.resolve(Settings)
            async with outer.scope() as child:
                assert await child.resolve(Settings) is settings

    run(main())


def test_a_transient_is_fresh_at_every_level() -> None:
    @injectable(scope=Scope.TRANSIENT)
    class Scratch:
        pass

    async def main() -> None:
        built = DIContainer()
        built.register(Scratch)
        async with built.scope() as outer:
            first = await outer.resolve(Scratch)
            async with outer.scope() as child:
                second = await child.resolve(Scratch)
                third = await child.resolve(Scratch)
            assert first is not second
            assert second is not third

    run(main())


# -- values -----------------------------------------------------------------


def test_values_are_copied_from_parent_to_child() -> None:
    async def main() -> None:
        async with container().scope(values={str: "root"}) as outer:
            async with outer.scope(values={int: 1}) as child:
                assert child.values == {str: "root", int: 1}
                async with child.scope() as leaf:
                    assert leaf.values == {str: "root", int: 1}
            assert outer.values == {str: "root"}

    run(main())


def test_a_child_overrides_a_parent_value_without_mutating_it() -> None:
    async def main() -> None:
        async with container().scope(values={str: "root"}) as outer:
            async with outer.scope(values={str: "child"}) as child:
                assert child.values[str] == "child"
                child.values[bytes] = b"only here"
            assert outer.values == {str: "root"}

    run(main())


def test_a_value_token_can_be_supplied_at_the_root_and_read_by_a_child() -> None:
    class RequestId:
        """A host-supplied token: declared, never registered as a provider."""

    @injectable(scope=Scope.REQUEST)
    class Needy:
        def __init__(self, request_id: RequestId) -> None:
            self.request_id = request_id

    async def main() -> None:
        built = DIContainer(value_tokens=(RequestId,))
        built.register(Needy)
        async with built.scope(values={RequestId: "r-1"}) as outer, outer.scope() as child:
            assert (await child.resolve(Needy)).request_id == "r-1"

    run(main())


# -- teardown ---------------------------------------------------------------


def test_closing_a_child_tears_down_only_what_the_child_created() -> None:
    order: list[str] = []

    @injectable(scope=Scope.REQUEST)
    class ParentPart:
        @pre_destruct
        async def bye(self) -> None:
            order.append("parent")

    @injectable(scope=Scope.REQUEST)
    class ChildPart:
        @pre_destruct
        async def bye(self) -> None:
            order.append("child")

    async def main() -> None:
        built = DIContainer()
        built.register(ParentPart)
        built.register(ChildPart)
        async with built.scope() as outer:
            await outer.resolve(ParentPart)
            async with outer.scope() as child:
                await child.resolve(ChildPart)
            assert order == ["child"]
            assert child.errors == ()
            assert outer.closed is False
        assert order == ["child", "parent"]

    run(main())


def test_a_generator_provider_is_torn_down_at_the_level_that_created_it() -> None:
    closed: list[str] = []

    @injectable(scope=Scope.REQUEST)
    def parent_resource() -> str:
        yield "value"
        closed.append("parent")

    async def main() -> None:
        built = DIContainer()
        built.register(parent_resource)
        async with built.scope() as outer:
            value = await outer.resolve(parent_resource)
            async with outer.scope() as child:
                assert await child.resolve(parent_resource) is value  # read-through
            assert closed == []
        assert closed == ["parent"]

    run(main())


def test_closing_a_parent_closes_its_open_children_first() -> None:
    order: list[str] = []

    @injectable(scope=Scope.REQUEST)
    class ParentPart:
        @pre_destruct
        async def bye(self) -> None:
            order.append("parent")

    @injectable(scope=Scope.REQUEST)
    class ChildPart:
        @pre_destruct
        async def bye(self) -> None:
            order.append("child")

    async def main() -> None:
        built = DIContainer()
        built.register(ParentPart)
        built.register(ChildPart)
        parent = built.scope()
        await parent.resolve(ParentPart)
        child = parent.scope()
        await child.resolve(ChildPart)
        grandchild = child.scope()
        assert [scope.depth for scope in parent.child_scopes()] == [2]
        await parent.close()
        # The grandchild had created nothing; the child's instance goes first.
        assert order == ["child", "parent"]
        assert child.closed is True
        assert grandchild.closed is True
        assert parent.child_scopes() == ()

    run(main())


def test_resolving_in_a_child_after_the_parent_closed_raises() -> None:
    async def main() -> None:
        parent = container().scope()
        child = parent.scope()
        await parent.close()
        assert child.closed is True
        with pytest.raises(LifecycleViolationError) as caught:
            await child.resolve(Session)
        assert caught.value.detail["state"] == "closed"

    run(main())


def test_opening_a_child_from_a_closed_scope_raises() -> None:
    async def main() -> None:
        parent = container().scope()
        await parent.close()
        with pytest.raises(LifecycleViolationError) as caught:
            parent.scope()
        assert caught.value.detail["state"] == "closed"
        assert caught.value.detail["depth"] == 1

    run(main())


# -- depth: unlimited by default, configurable when it matters -------------


def test_nesting_is_unlimited_by_default() -> None:
    async def main() -> None:
        async with container().scope() as root:
            session = await root.resolve(Session)
            scope = root
            for _ in range(100):
                scope = scope.scope()
            assert scope.depth == 101
            assert await scope.resolve(Session) is session
            await root.close()

    run(main())


def test_closing_a_very_deep_chain_does_not_recurse() -> None:
    """A 2,000-level chain closes without touching the interpreter's stack.

    The first implementation recursed one call per level and raised
    ``RecursionError`` at ~1,000 levels, which makes "unlimited" a lie told by
    the default. ``close()`` now walks the chain iteratively.
    """

    async def main() -> None:
        root = container().scope()
        scope = root
        for _ in range(1999):
            scope = scope.scope()
        await scope.resolve(Session)
        assert scope.depth == 2000
        errors = await root.close()
        assert errors == ()
        assert scope.closed is True
        assert scope.errors == ()

    run(main())


def test_a_container_can_cap_the_depth() -> None:
    async def main() -> None:
        root = container(max_scope_depth=2).scope()
        level_two = root.scope()
        with pytest.raises(LifecycleViolationError) as caught:
            level_two.scope()
        detail = caught.value.detail
        assert detail == {"depth": 3, "max_depth": 2, "reason": "depth_exceeded"}
        await root.close()

    run(main())


def test_a_root_scope_can_cap_the_depth_for_its_subtree() -> None:
    async def main() -> None:
        root = container().scope(max_depth=1)
        assert root.depth == 1
        with pytest.raises(LifecycleViolationError):
            root.scope()
        await root.close()

    run(main())


def test_a_child_may_narrow_the_limit() -> None:
    async def main() -> None:
        root = container(max_scope_depth=5).scope()
        child = root.scope(max_depth=2)
        assert (root.depth, child.depth) == (1, 2)
        with pytest.raises(LifecycleViolationError):
            child.scope()
        await root.close()

    run(main())


def test_a_child_may_not_widen_the_limit() -> None:
    async def main() -> None:
        root = container(max_scope_depth=2).scope()
        child = root.scope()
        with pytest.raises(LifecycleConfigError) as caught:
            child.scope(max_depth=10)
        assert caught.value.detail["reason"] == "widened_max_depth"
        await root.close()

    run(main())


def test_an_invalid_limit_is_a_configuration_error() -> None:
    with pytest.raises(LifecycleConfigError) as caught:
        DIContainer(max_scope_depth=0)
    assert caught.value.detail == {"max_depth": 0, "reason": "invalid_max_depth"}

    root = container().scope()
    with pytest.raises(LifecycleConfigError):
        root.scope(max_depth=0)
    assert root.closed is False


# -- the public seam is unchanged ------------------------------------------


def test_a_plain_dict_still_works_as_a_request_cache() -> None:
    async def main() -> None:
        built = container()
        cache: dict[object, object] = {}
        session = await built.resolve(Session, request_cache=cache)
        assert await built.resolve(Session, request_cache=cache) is session
        assert cache  # the caller owns the dict and it holds the instance

    run(main())


def test_strict_mode_accepts_a_child_scope_as_an_active_scope() -> None:
    async def main() -> None:
        built = container(strict_request_scope=True)
        async with built.scope() as outer, outer.scope() as child:
            assert await child.resolve(Session) is not None

    run(main())
