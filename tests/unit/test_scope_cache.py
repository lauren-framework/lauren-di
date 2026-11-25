"""The per-level cache contract behind nested scopes (PRD 15 §5.4, §6.2).

`_ScopeCache` is a `dict` subclass on purpose, and three properties are
load-bearing rather than incidental:

* **iteration is own-only** — `finalize_scope` tears down the entries it is
  given, so a mapping chain would hand a parent's instances to a child's
  teardown;
* **reads read through** — `in`, `[]` and `get` must see an ancestor's entry;
* **a miss is a `KeyError`**, so `resolve` constructs rather than returning a
  sentinel.

The class is private, but these are the contracts the public behaviour rests
on, and a regression here is invisible from the outside until an instance is
torn down at the wrong level.
"""

from __future__ import annotations

import gc

import pytest
from lauren_di import DIContainer, Scope, injectable
from lauren_di._scope import _ScopeCache

from helpers import run

pytestmark = pytest.mark.unit


def test_a_cache_reads_through_to_its_parent() -> None:
    parent = _ScopeCache()
    parent["k"] = "v"
    child = _ScopeCache(parent)

    assert "k" in child
    assert child["k"] == "v"
    assert child.get("k") == "v"
    assert child.get("missing") is None
    assert child.get("missing", 0) == 0


def test_iteration_len_and_membership_of_own_entries_are_own_only() -> None:
    parent = _ScopeCache()
    parent["parent"] = 1
    child = _ScopeCache(parent)
    child["child"] = 2

    assert list(child) == ["child"]
    assert len(child) == 1
    assert "parent" not in list(child)
    assert set(child) == {"child"}


def test_a_miss_raises_keyerror_rather_than_returning_none() -> None:
    empty = _ScopeCache()
    with pytest.raises(KeyError):
        _ = empty["nope"]


def test_a_write_in_the_child_does_not_reach_the_parent() -> None:
    parent = _ScopeCache()
    child = _ScopeCache(parent)
    child["k"] = "child"

    assert parent.get("k") is None
    assert child["k"] == "child"


# -- the weak-reference bookkeeping behind child_scopes() -------------------


def test_a_collected_child_is_pruned_from_child_scopes() -> None:
    @injectable(scope=Scope.REQUEST)
    class Session:
        pass

    async def main() -> None:
        container = DIContainer()
        container.register(Session)
        root = container.scope()
        child = root.scope()
        assert root.child_scopes() == (child,)

        del child
        gc.collect()

        assert root.child_scopes() == ()
        assert root._children == []  # the dead reference was pruned, not skipped
        await root.close()

    run(main())


def test_a_closed_child_is_pruned_from_child_scopes() -> None:
    async def main() -> None:
        root = DIContainer().scope()
        child = root.scope()
        await child.close()
        assert root.child_scopes() == ()
        assert root._children == []
        await root.close()

    run(main())
