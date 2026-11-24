"""``ProviderOverride`` / ``OverridePlan`` payload validation.

An override is the one place where a test *replaces* production wiring, so a
malformed override that was accepted at construction time would produce a
build that looks fine and behaves wrongly.  Every ``kind`` therefore validates
its own payload eagerly in ``__post_init__`` and raises :class:`OverrideError`
naming the offending field (PRD 07 §override plan, PRD 06 §errors).

The unit level is right for this: no container is built, so each rejection is
attributed to the validation leaf that owns it rather than to a later
compile step.
"""

from __future__ import annotations

from typing import Any

import pytest
from lauren_di import OverrideError, OverridePlan, ProviderOverride, Scope
from lauren_di._overrides import OVERRIDE_KINDS, active_override_plan, building_with

pytestmark = pytest.mark.unit


class TestProviderOverridePayloads:
    """Each override ``kind`` owns a payload and refuses a mismatched one.

    The registry dispatches on ``kind`` at build time and assumes the payload
    is present.  Validating here keeps that assumption from becoming a
    ``TypeError`` deep inside a build.
    """

    def test_unknown_kind_is_rejected(self) -> None:
        """``kind`` is a closed vocabulary shared with the registry."""
        with pytest.raises(OverrideError):
            ProviderOverride(token="x", kind="singleton-of-nothing")

    def test_declared_kinds_are_the_supported_set(self) -> None:
        """The advertised vocabulary is what the validator accepts; a new
        kind added to one and not the other would be dead on arrival.
        """
        assert frozenset({"value", "values", "class", "factory", "existing"}) == OVERRIDE_KINDS

    def test_class_kind_requires_a_class_payload(self) -> None:
        """``use_class`` must be a type: an instance would be re-built per
        consumer instead of shared.
        """
        with pytest.raises(OverrideError):
            ProviderOverride(token="x", kind="class", use_class="not-a-class")  # type: ignore[arg-type]

    def test_factory_kind_requires_a_callable_payload(self) -> None:
        """A non-callable factory would fail at the first resolve."""
        with pytest.raises(OverrideError):
            ProviderOverride(token="x", kind="factory", factory=42)  # type: ignore[arg-type]

    def test_existing_kind_requires_a_target_token(self) -> None:
        """``existing=None`` is indistinguishable from "not supplied"."""
        with pytest.raises(OverrideError):
            ProviderOverride(token="x", kind="existing")

    def test_values_kind_requires_at_least_one_value(self) -> None:
        """An empty multi-binding collection would hijack ``list[T]``
        injection and hand the consumer an empty list.
        """
        with pytest.raises(OverrideError):
            ProviderOverride(token="x", kind="values", values=())

    def test_injects_must_be_a_tuple(self) -> None:
        """The resolver indexes ``injects`` positionally; a list would be
        mutated by a caller and change the wiring mid-build.
        """
        with pytest.raises(OverrideError):
            ProviderOverride(token="x", kind="factory", factory=list, injects=["a"])  # type: ignore[arg-type]

    def test_injects_must_be_hashable(self) -> None:
        """Injects become DI keys, so an unhashable one cannot be looked up."""
        with pytest.raises(OverrideError):
            ProviderOverride(token="x", kind="factory", factory=list, injects=(["bad"],))

    def test_valid_payloads_are_accepted(self) -> None:
        """The positive path for every kind keeps the validator honest."""
        override = ProviderOverride(
            token="x",
            kind="class",
            use_class=dict,
            scope=Scope.TRANSIENT,
            injects=("a",),
        )

        assert override.use_class is dict
        assert override.scope is Scope.TRANSIENT
        assert override.role == "provider"


class TestOverridePlanLookup:
    """``OverridePlan.get`` is the container's only override lookup.

    It must be tolerant of exotic tokens: a token that cannot be hashed can
    never have been overridden, so returning ``None`` is correct and keeps the
    registration path from raising for an unrelated reason.
    """

    def test_known_token_returns_its_override(self) -> None:
        """A registered override is found by identity of the token key."""
        override = ProviderOverride(token="db", kind="value", value="sqlite://")
        plan = OverridePlan({"db": override})

        assert plan.get("db") is override

    def test_unknown_token_returns_none(self) -> None:
        """No override is not an error — most tokens have none."""
        assert OverridePlan().get("db") is None

    def test_unhashable_token_returns_none(self) -> None:
        """An unhashable token cannot be a dict key, so it cannot be
        overridden; swallowing the ``TypeError`` keeps registration alive.
        """
        plan = OverridePlan({"db": ProviderOverride(token="db", kind="value", value=1)})

        assert plan.get(["un", "hashable"]) is None

    def test_empty_plan_has_empty_payloads(self) -> None:
        """``OverridePlan()`` must be usable with no arguments at all."""
        plan = OverridePlan()

        assert plan.providers == {}
        assert plan.modules == {}

    def test_module_remapping_is_stored(self) -> None:
        """Module overrides let a test swap a whole module implementation."""

        class Original:
            pass

        class Doubled:
            pass

        plan = OverridePlan(modules={Original: Doubled})

        assert plan.modules == {Original: Doubled}


class TestActivePlanContext:
    """``building_with`` publishes the plan to code that only sees a token.

    Registration code deep inside a factory needs to know which overrides are
    in force without threading a parameter through every call, so the plan
    lives in a ``ContextVar`` for the duration of the build.
    """

    def test_plan_is_visible_inside_the_context_and_gone_after(self) -> None:
        """The plan must not leak past the ``with`` block, or a later build
        would inherit another build's overrides.
        """
        plan = OverridePlan()

        assert active_override_plan() is None
        with building_with(plan):
            assert active_override_plan() is plan
        assert active_override_plan() is None

    def test_plan_is_restored_when_the_block_raises(self) -> None:
        """A build that fails must still restore the previous context."""
        outer = OverridePlan()
        inner = OverridePlan()

        with building_with(outer):
            with pytest.raises(RuntimeError), building_with(inner):
                raise RuntimeError("build failed")
            assert active_override_plan() is outer

    def test_plan_is_restored_for_a_supplied_mapping(self) -> None:
        """A plan built from a mapping must expose the same lookup as a
        literal one — the constructor normalises both forms.
        """
        override: Any = ProviderOverride(token="db", kind="value", value=1)
        plan = OverridePlan({"db": override}, None)

        with building_with(plan):
            assert active_override_plan() is plan
            assert active_override_plan().get("db") is override
