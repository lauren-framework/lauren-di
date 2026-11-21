"""The exception hierarchy of :mod:`lauren_di` (private module).

Every error the library raises is a leaf of a single flat tree rooted at
:class:`DIError`.  A flat tree means two ``except`` statements matter:
``except DIError`` catches everything the library can raise, and
``except MissingProviderError`` catches one specific mistake.  There is no
intermediate ``StartupError`` / ``LifecycleError`` layer, because those
layers only distinguished HTTP-mapped errors from non-HTTP ones and this
library has no HTTP surface at all.

Two rules keep the catalogue useful in production:

* ``code`` is stable public API.  It is declared once on the class, so
  every instance of a leaf reports the same value and no two leaves share
  one.
* ``detail`` is a JSON-serialisable ``dict`` that is never ``None`` and
  never shared between instances.  It carries the machine-readable facts
  (tokens, module names, stages) that ``message`` renders for humans.

The module imports nothing from the rest of the package, so importing
:class:`DIError` never drags the container in.
"""

from __future__ import annotations

import json
from typing import Any

__all__ = [
    "CircularDependencyError",
    "DIError",
    "DIScopeViolationError",
    "DecoratorUsageError",
    "DestructError",
    "DuplicateBindingError",
    "InvalidProviderError",
    "LifecycleConfigError",
    "LifecycleViolationError",
    "MetadataInheritanceError",
    "MissingProviderError",
    "OverrideError",
    "ProtocolAmbiguityError",
    "UnresolvableParameterError",
]


class DIError(Exception):
    """Root of every error ``lauren_di`` raises.

    ``message`` is one human sentence; ``detail`` holds the facts.  The
    class is constructible by hand, which makes it usable as a catch-all
    in tests and in adapters that wrap the container.
    """

    #: Stable snake_case identifier, one per leaf.
    code: str = "lauren_di_error"

    def __init__(self, message: str = "", *, detail: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        # ``dict(...)`` guarantees no two errors share a mutable payload
        # and that a caller-supplied mapping is copied, not aliased.
        self.detail: dict[str, Any] = dict(detail) if detail else {}

    def __str__(self) -> str:
        return self.message

    def __repr__(self) -> str:
        return f"{type(self).__name__}({self.message!r}, detail={self.detail!r})"

    def to_payload(self) -> dict[str, Any]:
        """Return the canonical JSON-ready envelope for this error."""
        return {"error": {"code": self.code, "message": self.message, "detail": self.detail}}

    def as_json(self) -> str:
        """Render :meth:`to_payload` as a single line of JSON."""
        return json.dumps(self.to_payload(), sort_keys=True, default=str)


class InvalidProviderError(DIError):
    """The object passed to ``register`` is neither a class nor a callable."""

    code = "invalid_provider"


class DecoratorUsageError(DIError):
    """A declaration helper was called with an argument it cannot accept."""

    code = "decorator_usage"


class MissingProviderError(DIError):
    """No provider exists for a token, or none is visible from the caller's module."""

    code = "missing_provider"


class DuplicateBindingError(DIError):
    """A second non-``multi`` binding was registered for one token."""

    code = "duplicate_binding"


class ProtocolAmbiguityError(DIError):
    """More than one binding exists where exactly one was required."""

    code = "protocol_ambiguity"


class CircularDependencyError(DIError):
    """A provider (or alias) chain revisits a token."""

    code = "circular_dependency"


class DIScopeViolationError(DIError):
    """A consumer's lifetime outlives its dependency's, or a request scope is missing."""

    code = "di_scope_violation"


class UnresolvableParameterError(DIError):
    """A parameter or field cannot be turned into a dependency edge."""

    code = "unresolvable_parameter"


class MetadataInheritanceError(DIError):
    """A subclass inherited ``@injectable`` metadata instead of declaring it."""

    code = "metadata_inheritance"


class LifecycleConfigError(DIError):
    """A lifecycle declaration is structurally invalid (bad hook, transient generator)."""

    code = "lifecycle_config"


class LifecycleViolationError(DIError):
    """A ``ResolutionScope`` was used outside its ``async with`` block."""

    code = "lifecycle_violation"


class DestructError(DIError):
    """A teardown step failed.

    Teardown never raises this: :meth:`DIContainer.shutdown` and
    ``ResolutionScope.close`` *return* the errors they collected so that a
    failing hook cannot abort the teardown of the remaining instances.
    The leaf subclasses :class:`DIError` so a returned value can still be
    logged and caught like any other library error.
    """

    code = "destruct_error"


class OverrideError(DIError):
    """A build-time override cannot be applied to the graph."""

    code = "override_error"
