"""The custom-provider helper layer: ``Token``, ``Inject`` and the four recipes.

``lauren_di/_custom.py`` is the library's most error-dense module: every helper
is a thin front door whose whole job is to reject bad input *before* a registry
row exists, so the failure a user sees names the argument they typed rather
than a downstream ``TypeError``.  These tests pin that front door — the
``Token`` value semantics (PRD 03 §custom providers), the ``Inject`` marker
payload, and each ``DecoratorUsageError`` leaf in ``use_value`` /
``use_class`` / ``use_factory`` / ``use_existing``.

Kept at the unit level: nothing here builds or compiles a container, so a
regression in the helpers is reported as a helper regression and not as a
resolution failure several layers away.
"""

from __future__ import annotations

from typing import Any

import pytest
from lauren_di import (
    CustomProvider,
    DecoratorUsageError,
    Inject,
    OptionalDep,
    Scope,
    Token,
    use_class,
    use_existing,
    use_factory,
    use_value,
)
from lauren_di._custom import is_custom_provider, normalise_provider_token

pytestmark = pytest.mark.unit


class TestTokenIdentity:
    """``Token`` has two comparison regimes and callers rely on both.

    A ``Token("X")`` is a *fresh* handle each time it is written — the whole
    point of the class is that two registration sites cannot accidentally
    collide on a string.  ``unique=False`` opts back into string-like
    equality so framework code can key a registry by name.  Both regimes are
    pinned here because flipping the default would silently merge providers.
    """

    def test_unique_tokens_compare_by_identity_not_name(self) -> None:
        """Two ``Token("X")`` calls must stay distinct DI keys."""
        first = Token("X")
        second = Token("X")

        assert first != second
        # Reflexivity, asked of the protocol directly: the identity regime must
        # still say a token equals itself, or a registry keyed by the very token
        # it was registered with would never find it.  ``first == first`` states
        # the same thing but reads as a tautology to every linter -- and to a
        # reader skimming for a real assertion.
        assert first.__eq__(first) is True
        assert hash(first) != hash(second)

    def test_shared_tokens_compare_by_name(self) -> None:
        """``unique=False`` makes the token a stable, hashable name — the
        contract modules rely on to publish a token without an import cycle.
        """
        first = Token("config", unique=False)
        second = Token("config", unique=False)

        assert first == second
        assert hash(first) == hash(second)
        assert first != Token("other", unique=False)

    def test_token_never_equals_a_bare_string(self) -> None:
        """``Token.__eq__`` returns ``NotImplemented`` for foreign types so a
        shared token cannot alias a plain string registration by accident.
        """
        assert Token("config", unique=False) != "config"
        assert Token("config") != "config"

    def test_name_and_unique_are_read_only_properties(self) -> None:
        """The registry reads both properties, so they must reflect the
        constructor arguments exactly.
        """
        token = Token("db", unique=False)

        assert token.name == "db"
        assert token.unique is False
        assert Token("db").unique is True

    def test_repr_marks_the_uniqueness_regime(self) -> None:
        """``repr`` is the only debugging affordance a provider row keeps, so
        it must show whether the token is unique or shared.
        """
        assert repr(Token("db")) == "Token('db', unique)"
        assert repr(Token("db", unique=False)) == "Token('db', shared)"

    def test_dunder_name_is_a_human_label(self) -> None:
        """Error messages interpolate ``__name__`` the way they would a class,
        so a ``Token`` key must render as ``Token(<name>)``.
        """
        assert Token("db").__name__ == "Token(db)"

    @pytest.mark.parametrize("bad", ["", 5, None, b"db"])
    def test_construction_rejects_a_non_empty_string_violation(self, bad: Any) -> None:
        """The annotation says ``str``; untyped callers get a ``ValueError``
        instead of a token that would break the registry's ``__name__``
        formatting much later.
        """
        with pytest.raises(ValueError):
            Token(bad)


class TestInjectMarker:
    """``Inject`` wraps a token for :class:`Annotated` dependencies.

    The marker is the only place a bare token reaches the annotation
    collector, so the collector needs one predictable attribute (``token``)
    and a readable ``repr`` for the ``explain`` output.
    """

    def test_marker_carries_the_token_verbatim(self) -> None:
        """The dependency graph is keyed by the *exact* object passed in."""
        token = Token("mailer", unique=False)
        marker = Inject(token)

        assert marker.token is token

    def test_marker_repr_names_the_token(self) -> None:
        """``explain`` renders annotations through ``repr``."""
        assert repr(Inject("mailer")) == "Inject('mailer')"


class TestTokenValidation:
    """``_validate_token`` is shared by all four recipes.

    It is the single gate that guarantees every registry key is hashable and
    non-``None``; each recipe delegates to it, so both leaves are exercised
    through the public helpers rather than through the private function.
    """

    def test_unhashable_token_is_rejected_with_the_helper_name(self) -> None:
        """A list token would blow up inside the registry dict; the guard
        converts that into a ``DecoratorUsageError`` that names the helper.
        """
        with pytest.raises(DecoratorUsageError) as excinfo:
            use_value(provide=["not", "hashable"], value=1)

        assert "not hashable" in str(excinfo.value)
        assert excinfo.value.detail["token"] == repr(["not", "hashable"])

    def test_none_token_is_rejected(self) -> None:
        """``None`` is hashable, so it would slip past the hash probe and
        register a provider nobody can ask for.
        """
        with pytest.raises(DecoratorUsageError) as excinfo:
            use_value(provide=None, value=1)

        assert "must not be None" in str(excinfo.value)


class TestUseValue:
    """``use_value`` binds an already-built object.

    It is the recipe behind configuration values and test doubles, so its
    only jobs are: normalise the arguments onto a ``CustomProvider`` and
    propagate ``multi`` unchanged.
    """

    def test_builds_a_value_provider(self) -> None:
        """The registry dispatches on ``kind``; a value recipe must say so."""
        provider = use_value(provide="db.url", value="sqlite://")

        assert isinstance(provider, CustomProvider)
        assert provider.provide == "db.url"
        assert provider.kind == "value"
        assert provider.value == "sqlite://"
        assert provider.multi is False

    def test_multi_flag_is_propagated(self) -> None:
        """``multi=True`` is how a token collects several contributions for
        ``list[T]`` injection.
        """
        provider = use_value(provide="handlers", value="a", multi=True)

        assert provider.multi is True


class TestUseClass:
    """``use_class`` binds a token to an implementation class.

    This is the NestJS ``useClass`` recipe: the token and the implementation
    differ, which is exactly what interface-style injection needs.
    """

    def test_builds_a_class_provider_with_the_requested_scope(self) -> None:
        """Scope is per-provider, so the recipe must carry it verbatim."""

        class InMemoryStore:
            pass

        provider = use_class(provide="store", use=InMemoryStore, scope=Scope.TRANSIENT)

        assert provider.kind == "class"
        assert provider.use_class is InMemoryStore
        assert provider.scope is Scope.TRANSIENT

    def test_defaults_to_singleton_like_nestjs(self) -> None:
        """A class provider without a scope must be a singleton, otherwise
        every resolution would allocate a new instance.
        """

        class InMemoryStore:
            pass

        assert use_class(provide="store", use=InMemoryStore).scope is Scope.SINGLETON

    def test_non_class_payload_is_rejected(self) -> None:
        """``use`` must be a class: an *instance* here would silently
        register a fresh copy per build instead of the object the caller
        built.
        """
        instance = object()
        with pytest.raises(DecoratorUsageError) as excinfo:
            use_class(provide="store", use=instance)  # type: ignore[arg-type]

        assert "use_class.use must be a class" in str(excinfo.value)
        assert excinfo.value.detail["use"] == repr(instance)


class TestUseFactory:
    """``use_factory`` binds a token to a callable plus its injected tokens.

    The ``injects`` tuple is positional and validated eagerly: a factory whose
    dependency cannot be a DI key is a build-time error, not a runtime
    ``KeyError`` on the first resolve.
    """

    def test_builds_a_factory_provider(self) -> None:
        """The factory, its injects and its scope must survive intact."""

        def make() -> str:
            return "built"

        provider = use_factory(provide="thing", factory=make)

        assert provider.kind == "factory"
        assert provider.factory is make
        assert provider.inject == ()
        assert provider.scope is Scope.SINGLETON

    def test_inject_tokens_are_preserved_in_order(self) -> None:
        """The call is positional over ``injects``, so order is semantic."""

        def make(a: Any, b: Any) -> tuple[Any, Any]:
            return (a, b)

        provider = use_factory(provide="thing", factory=make, injects=["a", "b"])

        assert provider.inject == ("a", "b")

    def test_non_callable_factory_is_rejected(self) -> None:
        """A string or instance is not a factory; the guard reports the value
        the caller actually passed.
        """
        with pytest.raises(DecoratorUsageError) as excinfo:
            use_factory(provide="thing", factory="not callable")  # type: ignore[arg-type]

        assert "must be callable" in str(excinfo.value)
        assert excinfo.value.detail["factory"] == "'not callable'"

    def test_optional_inject_validates_the_wrapped_token(self) -> None:
        """``OptionalDep`` is unwrapped before validation, so a bad token
        inside it must fail at declaration time just like a bare token.
        """
        with pytest.raises(DecoratorUsageError) as excinfo:
            use_factory(provide="thing", factory=lambda: 1, injects=[OptionalDep(["bad"])])

        assert "not hashable" in str(excinfo.value)

    def test_plain_inject_tokens_are_validated(self) -> None:
        """A bare (non-``OptionalDep``) inject takes the other validation
        branch and must reject the same way.
        """
        with pytest.raises(DecoratorUsageError) as excinfo:
            use_factory(provide="thing", factory=lambda: 1, injects=[["bad"]])

        assert "not hashable" in str(excinfo.value)

    def test_valid_optional_inject_is_accepted(self) -> None:
        """The positive path of the ``OptionalDep`` branch must not raise —
        an optional dependency is a legal declaration.
        """
        provider = use_factory(
            provide="thing",
            factory=lambda: 1,
            injects=[OptionalDep("missing"), "present"],
        )

        assert len(provider.inject) == 2


class TestUseExisting:
    """``use_existing`` aliases one token to another.

    Both sides are DI keys, so both are validated; and the alias cannot be
    its own source, which would be an unsatisfiable cycle of length one.
    """

    def test_builds_an_existing_provider(self) -> None:
        """The alias payload is the *existing* token, not a value."""
        provider = use_existing(provide="alias", existing="real")

        assert provider.kind == "existing"
        assert provider.existing == "real"

    def test_self_alias_is_rejected(self) -> None:
        """``provide == existing`` would resolve into itself forever; the
        guard names the token instead of hanging the resolver.
        """
        with pytest.raises(DecoratorUsageError) as excinfo:
            use_existing(provide="same", existing="same")

        assert "cannot alias itself" in str(excinfo.value)
        assert excinfo.value.detail["token"] == "same"

    def test_existing_token_is_validated(self) -> None:
        """A bad ``existing`` token is as fatal as a bad ``provide``."""
        with pytest.raises(DecoratorUsageError):
            use_existing(provide="alias", existing=["bad"])


class TestCustomProviderPayload:
    """``CustomProvider`` validates its own payload shape on construction.

    Recipes build it, but the registry also accepts hand-built instances, so
    the dataclass itself has to refuse a payload that does not match its
    ``kind`` — otherwise the error would surface during compilation far away
    from the declaration.
    """

    def test_unknown_kind_is_rejected(self) -> None:
        """``kind`` selects the build strategy and has a closed vocabulary."""
        with pytest.raises(ValueError):
            CustomProvider(provide="x", kind="bogus", value=1)


class TestProviderTokenNormalisation:
    """Registration accepts either a raw token or a ``CustomProvider``.

    ``register`` needs one canonical key before it can detect duplicates, so
    it normalises through ``normalise_provider_token``.  Both branches are
    pinned here because a miss would make duplicate detection asymmetric.
    """

    def test_custom_provider_normalises_to_its_token(self) -> None:
        """The DI key of a custom provider is its ``provide`` field."""
        provider = use_value(provide="db.url", value="x")

        assert normalise_provider_token(provider) == "db.url"

    def test_raw_token_normalises_to_itself(self) -> None:
        """A plain token is already the key — no wrapping, no copying."""

        class Service:
            pass

        assert normalise_provider_token(Service) is Service

    def test_is_custom_provider_discriminates(self) -> None:
        """``register`` branches on this predicate before touching fields."""
        assert is_custom_provider(use_value(provide="k", value=1)) is True
        assert is_custom_provider("k") is False
