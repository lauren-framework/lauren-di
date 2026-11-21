"""The declarative surface of :mod:`lauren_di`.

Four names live here — :func:`injectable`, :func:`injectable_meta`,
:func:`post_construct` and :func:`pre_destruct` — plus the marker attribute
names they write.  Everything else about the library is the container.

Markers are plain string attributes so they survive inheritance, pickling
of the class object and ``functools.wraps`` without any registration
machinery.  The names are namespaced ``__lauren_di_*__`` so a class can be
used by ``lauren`` and ``lauren_di`` side by side without either library
reading the other's metadata.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, TypeVar

from ._container import (
    INJECTABLE_META,
    InjectableMeta,
    # Intra-package split: ``_container`` owns the metadata reader, and this
    # module is where the public decorators live.
    _get_injectable_meta,  # pyright: ignore[reportPrivateUsage]
)
from ._errors import DecoratorUsageError, LifecycleConfigError
from .types import Scope

__all__ = [
    "INJECTABLE_META",
    "POST_CONSTRUCT",
    "PRE_DESTRUCT",
    "InjectableMeta",
    "injectable",
    "injectable_meta",
    "post_construct",
    "pre_destruct",
]

#: Attribute set by :func:`injectable`; holds an :class:`InjectableMeta`.
# ``X = X`` is the PEP 484 explicit-re-export idiom mypy's ``--strict`` needs
# (``no_implicit_reexport``); pyright reads it as a constant redefinition, so the
# directive below says "yes, on purpose".
INJECTABLE_META = INJECTABLE_META  # pyright: ignore[reportConstantRedefinition]

#: Attribute set by :func:`post_construct` on a method.
POST_CONSTRUCT = "__lauren_di_post_construct__"

#: Attribute set by :func:`pre_destruct` on a method.
PRE_DESTRUCT = "__lauren_di_pre_destruct__"

F = TypeVar("F", bound=Callable[..., Any])
_T = TypeVar("_T")


def injectable(
    *args: Any,
    scope: Scope = Scope.SINGLETON,
    provides: list[type] | tuple[type, ...] | None = None,
    multi: bool = False,
) -> Callable[[_T], _T]:
    """Mark a class or a function as a DI provider.

    **Class form** — instances are produced by the class itself::

        @injectable()
        class UserRepo:
            db: Database                      # class-body field injection
            def __init__(self, cfg: Settings): ...

    **Function form** — the decorated function is the factory and its
    return value is the dependency::

        @injectable(scope=Scope.SINGLETON)
        def make_pool(settings: Settings) -> Pool:
            return Pool(settings.dsn)

    ``provides`` binds extra tokens to the same provider (typically
    runtime-checkable protocols), and ``multi=True`` lets several providers
    share one token so consumers can depend on ``list[T]``.

    The decorator must be called: ``@injectable()``.  The bare form
    ``@injectable`` is rejected because Python would pass the decorated
    object as a positional configuration argument, silently binding it to
    nothing.
    """
    if args:
        raise DecoratorUsageError(
            f"@{type(args[0]).__name__ if not isinstance(args[0], type) else args[0].__name__} "
            "must be called: write @injectable()",
            detail={
                "decorator": "injectable",
                "target": getattr(args[0], "__name__", repr(args[0])),
            },
        )

    def decorator(target: _T) -> _T:
        meta = InjectableMeta(
            scope=scope,
            provides=tuple(provides or ()),
            multi=multi,
        )
        try:
            setattr(target, INJECTABLE_META, meta)
        except (AttributeError, TypeError) as exc:  # pragma: no cover - exotic targets
            raise DecoratorUsageError(
                f"@injectable cannot attach metadata to {target!r}",
                detail={"decorator": "injectable", "target": repr(target)},
            ) from exc
        return target

    return decorator


def injectable_meta(target: Any) -> InjectableMeta | None:
    """Return the :class:`InjectableMeta` attached to ``target``, if any.

    Reading a marker that was inherited from a base class raises
    :class:`~lauren_di.MetadataInheritanceError` instead of silently
    registering the subclass with the base's scope and bindings.  This is
    the same lookup :meth:`DIContainer.register` performs, exposed so tools
    (containers, compilers, test helpers) can ask the same question.
    """
    return _get_injectable_meta(target)


def post_construct(fn: F) -> F:
    """Mark a method to run after the instance is constructed.

    Hooks run base class first, in definition order within a class.  For
    singletons they run during :meth:`DIContainer.startup`; for request and
    transient providers they run as part of the resolution that created the
    instance.
    """
    if not callable(fn):
        raise LifecycleConfigError(
            f"{fn!r} is not a method; decorate a method definition",
            detail={"target": repr(fn)},
        )
    setattr(fn, POST_CONSTRUCT, True)
    return fn


def pre_destruct(fn: F) -> F:
    """Mark a method to run during teardown.

    Hooks run subclass first, in definition order within a class.  A
    failing hook is recorded as a :class:`~lauren_di.DestructError` and
    teardown continues with the next instance.
    """
    if not callable(fn):
        raise LifecycleConfigError(
            f"{fn!r} is not a method; decorate a method definition",
            detail={"target": repr(fn)},
        )
    setattr(fn, PRE_DESTRUCT, True)
    return fn
