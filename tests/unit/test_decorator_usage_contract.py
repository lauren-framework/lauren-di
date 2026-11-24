"""Decorator-surface contracts: ``@injectable``, the hooks and metadata.

The decorators are the library's declarative face, so their failures must be
*declaration* errors with a fix in the message ("write ``@injectable()``")
rather than a ``TypeError`` at decoration time.  Two subtleties are pinned
here because they are easy to break while refactoring:

* ``@injectable`` without parentheses is the single most common misuse, and
* metadata is inherited by subclasses, which ``lauren_di`` deliberately
  refuses to guess about — a subclass must re-declare its own lifecycle or
  the reader would have to reason about ``__mro__`` to know its scope
  (PRD 02 §declarations, PRD 06 §MetadataInheritanceError).

``post_construct`` / ``pre_destruct`` are validated identically: decorating a
non-callable is a ``LifecycleConfigError``, not a silent no-op that would
leave a resource unclosed at shutdown.
"""

from __future__ import annotations

import pytest
from lauren_di import (
    DecoratorUsageError,
    InjectableMeta,
    LifecycleConfigError,
    MetadataInheritanceError,
    Scope,
    injectable,
    injectable_meta,
    post_construct,
    pre_destruct,
)

pytestmark = pytest.mark.unit


class TestInjectableCallSyntax:
    """``@injectable`` is a decorator *factory* and says so when misused.

    ``@injectable`` without the call would receive the class as its first
    positional argument and silently return a wrapped class; the guard turns
    that into an actionable error instead.
    """

    def test_bare_decorator_is_rejected(self) -> None:
        """``@injectable`` (no call) must raise at decoration time."""
        with pytest.raises(DecoratorUsageError) as excinfo:

            @injectable  # type: ignore[misc]
            class Service:
                pass

        assert "write @injectable()" in str(excinfo.value)

    def test_positional_argument_is_rejected(self) -> None:
        """``injectable`` takes keyword arguments only; a positional one means
        the caller forgot the parentheses.
        """
        with pytest.raises(DecoratorUsageError) as excinfo:
            injectable(5)  # type: ignore[call-arg]

        assert "write @injectable()" in str(excinfo.value)


class TestInjectableMetadata:
    """``injectable_meta`` is how ``register`` reads a declaration.

    It answers ``None`` for plain classes (the honest answer) and raises for a
    subclass that only *inherited* the marker, because that subclass has no
    declaration of its own to trust.
    """

    def test_undecorated_class_has_no_metadata(self) -> None:
        """A plain class is simply not a provider — not an error."""

        class Service:
            pass

        assert injectable_meta(Service) is None

    def test_decorated_class_exposes_its_scope(self) -> None:
        """The recorded scope is what the registry uses for the row."""

        @injectable(scope=Scope.REQUEST)
        class Service:
            pass

        meta = injectable_meta(Service)

        assert isinstance(meta, InjectableMeta)
        assert meta.scope is Scope.REQUEST
        assert meta.multi is False

    def test_provides_and_multi_are_normalised(self) -> None:
        """``provides`` arrives as any iterable and must be stored as a tuple
        so the metadata stays hashable and immutable.
        """

        @injectable(provides=["a", "b"], multi=True)
        def build() -> None:
            return None

        meta = injectable_meta(build)

        assert meta is not None
        assert meta.provides == ("a", "b")
        assert meta.multi is True

    def test_subclass_must_redeclare_its_metadata(self) -> None:
        """Inherited metadata is refused rather than guessed at: the reader
        would otherwise have to inspect ``__mro__`` to learn the scope.
        """

        @injectable(scope=Scope.REQUEST)
        class Base:
            pass

        class Child(Base):
            pass

        with pytest.raises(MetadataInheritanceError):
            injectable_meta(Child)

    def test_subclass_that_redeclares_keeps_its_own_metadata(self) -> None:
        """Re-decorating is the supported fix and must win over the parent."""

        @injectable(scope=Scope.REQUEST)
        class Base:
            pass

        @injectable(scope=Scope.TRANSIENT)
        class Child(Base):
            pass

        meta = injectable_meta(Child)

        assert meta is not None
        assert meta.scope is Scope.TRANSIENT


class TestLifecycleHookDecorators:
    """``@post_construct`` / ``@pre_destruct`` mark methods, nothing else.

    The container discovers hooks by attribute, so the decorator's only jobs
    are to write the marker and to refuse anything that is not a method —
    a mis-decorated attribute would otherwise be looked up and called at
    shutdown time with no arguments.
    """

    def test_post_construct_returns_the_same_function(self) -> None:
        """The decorator must not wrap: identity is what makes the marker
        discoverable from the class body.
        """

        def start(self: object) -> None:
            return None

        assert post_construct(start) is start

    def test_pre_destruct_returns_the_same_function(self) -> None:
        """Same contract for the teardown hook."""

        def stop(self: object) -> None:
            return None

        assert pre_destruct(stop) is stop

    def test_post_construct_rejects_a_non_callable(self) -> None:
        """A string or attribute is not a hook."""
        with pytest.raises(LifecycleConfigError) as excinfo:
            post_construct("open")  # type: ignore[arg-type]

        assert "not a method" in str(excinfo.value)

    def test_pre_destruct_rejects_a_non_callable(self) -> None:
        """A resource whose teardown silently never runs is the worst
        failure mode this library can have, so it must be loud.
        """
        with pytest.raises(LifecycleConfigError) as excinfo:
            pre_destruct(42)  # type: ignore[arg-type]

        assert "not a method" in str(excinfo.value)
