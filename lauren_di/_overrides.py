"""Build-time provider overrides — the seam behind test-time substitution.

An :class:`OverridePlan` describes replacements that apply to **one** build:
provider rows to swap, keyed by the DI token being replaced. Whatever builds
the container reads the plan that is active in the current context (see
:func:`active_override_plan`) and hands it to :class:`~lauren_di.DIContainer`.
Nothing else consults it, so production builds — which never activate a plan
— pay nothing.

The plan is activated by :func:`building_with` for the duration of one
synchronous build. A :class:`~contextvars.ContextVar` scopes it to that call
(and thread / task), so concurrent builds — and other containers in the same
process — never see each other's overrides.
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable, Iterator, Mapping
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

from ._errors import OverrideError
from .types import Scope

#: The override kinds a :class:`ProviderOverride` can carry.
OVERRIDE_KINDS: frozenset[str] = frozenset({"value", "values", "class", "factory", "existing"})


@dataclass(frozen=True)
class ProviderOverride:
    """Replacement for every registration of ``token`` in one app build.

    ``kind`` selects the payload: ``"value"`` (``value``), ``"values"``
    (``values`` — replaces a whole multi-binding set), ``"class"``
    (``use_class``), ``"factory"`` (``factory`` + ``injects``) or
    ``"existing"`` (an alias to ``existing``). ``scope`` is ``None`` when the
    override should inherit the replaced provider's scope. An ``optional``
    override that never matches a registration is ignored instead of failing
    the build.
    """

    token: Any
    kind: str
    value: Any = None
    values: tuple[Any, ...] = ()
    use_class: type | None = None
    factory: Callable[..., Any] | None = None
    injects: tuple[Any, ...] = ()
    existing: Any = None
    scope: Scope | None = None
    optional: bool = False
    #: What the override replaces, for error messages ("provider", "guard", …).
    role: str = "provider"

    def __post_init__(self) -> None:
        """Reject an unusable override at construction time (PRD 05 row 33).

        A malformed plan is a programming error in the *test* that built it, so
        it fails before a container exists rather than producing a row the
        resolver cannot use.
        """
        if self.kind not in OVERRIDE_KINDS:
            raise OverrideError(
                f"cannot override {self.kind} {self.token!r}: bad_payload",
                detail={"token": str(self.token), "kind": self.kind, "reason": "bad_payload"},
            )
        if self.kind == "class" and not isinstance(self.use_class, type):
            raise OverrideError(
                f"cannot override class {self.token!r}: bad_payload",
                detail={"token": str(self.token), "kind": self.kind, "reason": "bad_payload"},
            )
        if self.kind == "factory" and not callable(self.factory):
            raise OverrideError(
                f"cannot override factory {self.token!r}: bad_payload",
                detail={"token": str(self.token), "kind": self.kind, "reason": "bad_payload"},
            )
        if self.kind == "existing" and self.existing is None:
            raise OverrideError(
                f"cannot override existing {self.token!r}: bad_payload",
                detail={"token": str(self.token), "kind": self.kind, "reason": "bad_payload"},
            )
        if self.kind == "values" and not self.values:
            raise OverrideError(
                f"cannot override values {self.token!r}: bad_payload",
                detail={"token": str(self.token), "kind": self.kind, "reason": "bad_payload"},
            )
        # Payload check: ``injects`` is a tuple by annotation, but an override
        # built by hand can carry a list or a scalar.
        if not isinstance(self.injects, tuple):  # pyright: ignore[reportUnnecessaryIsInstance]
            raise OverrideError(
                f"cannot override {self.kind} {self.token!r}: bad_payload",
                detail={"token": str(self.token), "kind": self.kind, "reason": "bad_payload"},
            )
        for inject in self.injects:
            try:
                hash(inject)
            except TypeError:
                raise OverrideError(
                    f"cannot override {self.kind} {self.token!r}: bad_payload",
                    detail={"token": str(self.token), "kind": self.kind, "reason": "bad_payload"},
                ) from None


class OverridePlan:
    """Immutable set of provider overrides and module substitutions for one build."""

    __slots__ = ("providers", "modules")

    def __init__(
        self,
        providers: Mapping[Any, ProviderOverride] | None = None,
        modules: Mapping[type, type] | None = None,
    ) -> None:
        self.providers: dict[Any, ProviderOverride] = dict(providers or {})
        self.modules: dict[type, type] = dict(modules or {})

    def get(self, token: Any) -> ProviderOverride | None:
        """Return the override registered for ``token``, if any."""
        try:
            return self.providers.get(token)
        except TypeError:  # unhashable token — cannot have been overridden
            return None


_ACTIVE_PLAN: ContextVar[OverridePlan | None] = ContextVar("lauren_override_plan", default=None)


def active_override_plan() -> OverridePlan | None:
    """Return the plan activated for the build running in this context, if any."""
    return _ACTIVE_PLAN.get()


@contextlib.contextmanager
def building_with(plan: OverridePlan) -> Iterator[None]:
    """Activate ``plan`` for the container build that happens inside the block."""
    reset_token = _ACTIVE_PLAN.set(plan)
    try:
        yield
    finally:
        _ACTIVE_PLAN.reset(reset_token)


__all__ = [
    "OVERRIDE_KINDS",
    "OverridePlan",
    "ProviderOverride",
    "active_override_plan",
    "building_with",
]
