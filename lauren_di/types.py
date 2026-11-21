"""Public value types shared by the container and its consumers.

This module deliberately holds only data: :class:`Scope`, the
:data:`Depends` spelling alias and two structural aliases used by module
visibility.  Keeping them here means ``from lauren_di import Scope`` works
without importing the container.
"""

from __future__ import annotations

import enum
from typing import Any, TypeAlias

__all__ = ["Depends", "Grant", "Owner", "Scope"]


class Scope(enum.IntEnum):
    """DI scope values, ordered from narrowest to widest lifetime.

    The numeric order is load-bearing: the compiler rejects an edge whose
    consumer scope value is **greater** than its dependency's, because
    that consumer would outlive the instance it received.

    >>> Scope.TRANSIENT < Scope.REQUEST < Scope.SINGLETON
    True
    >>> Scope.SINGLETON > Scope.REQUEST   # singleton -> request: rejected
    True

    :attr:`label` returns the stable lowercase name (``"singleton"``,
    ``"request"``, ``"transient"``) that messages and logs use, instead of
    the ``IntEnum.__str__`` formatting, which differs across Python
    versions.
    """

    TRANSIENT = 0
    REQUEST = 1
    SINGLETON = 2

    @property
    def label(self) -> str:
        """The stable lowercase name of this scope."""
        return {
            Scope.TRANSIENT: "transient",
            Scope.REQUEST: "request",
            Scope.SINGLETON: "singleton",
        }[self]

    def __str__(self) -> str:
        """The label, not ``IntEnum``'s numeric rendering (PRD 03 L-03).

        ``IntEnum.__str__`` changed across Python versions (``"1"`` on 3.11+,
        ``"Scope.REQUEST"`` before) and every message this library renders goes
        through ``label``; routing ``str()`` through the same table keeps log
        lines stable on every supported interpreter.
        """
        return self.label


class _DependsAlias:
    """Spelling-only alias for a dependency annotation.

    ``Depends[T]`` evaluates to ``T`` itself, so no marker object and no
    ``Annotated`` wrapper reaches the container.  The spelling exists for
    readers coming from FastAPI and for symmetry with ``Inject``; the
    runtime token is always the bare type.

    It is a *runtime* spelling: a subscripted value is not a type expression,
    so neither mypy nor pyright accepts ``x: Depends[T]``.  A class cannot
    stand in either — ``Depends[T]`` would then mean the wrapper rather than
    ``T``, and a function provider is not a valid type argument — so a typed
    codebase annotates the resolved type, which is the same token (PRD 01
    §5.5).
    """

    __slots__ = ()

    def __class_getitem__(cls, item: Any) -> Any:
        return item

    def __getitem__(self, item: Any) -> Any:  # pragma: no cover - instance form
        return item

    def __repr__(self) -> str:
        return "Depends"


Depends = _DependsAlias()
"""``Depends[T] is T`` — a spelling alias, not a marker (D-25)."""

#: The module class that declared a row, or ``None`` for a global row.
Owner: TypeAlias = type | None

#: One visible-set entry: a token (all rows bound to it) or a
#: ``(token, owner)`` row address.
Grant: TypeAlias = Any | tuple[Any, type]
