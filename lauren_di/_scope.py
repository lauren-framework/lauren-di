"""Request-scope finalisation and the explicit resolution scope (private).

A request, a background job or a CLI command all end the same way: every
request-scoped instance created while the scope was open is torn down, newest
first. Each instance gets its ``@pre_destruct`` hooks (when the provider
declares them) and then ``aclose()`` (a generator provider's teardown, or an
instance's own ``aclose`` method).

:func:`finalize_scope` is the only implementation of that teardown. It finds
each instance's provider through :meth:`DIContainer.instance_index`, a map built
once when the container compiles, so a request does not rebuild it.

:class:`ResolutionScope` is the public entry point: an embedder that has no
framework request object opens one explicitly and passes it around.

    async with container.scope() as scope:
        handler = await scope.resolve(Handler)

There is no ``Request`` and no framework value registry here. A host that needs
its own objects resolvable declares them with ``DIContainer(value_tokens=...)``
and hands the values to :class:`ResolutionScope` via ``values=`` — see D-31.
"""

from __future__ import annotations

import inspect
import logging
import weakref
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, overload

# Intra-package split: ``_container`` owns the token renderer, and ``_scope``
# reports teardown errors with the same labels.
from ._container import _describe  # pyright: ignore[reportPrivateUsage]
from ._errors import DestructError, LifecycleConfigError, LifecycleViolationError

if TYPE_CHECKING:
    from . import DIContainer, Provider
    from ._custom import Token

logger = logging.getLogger("lauren_di")

#: Return type of the ``type[T]`` overload of :meth:`ResolutionScope.resolve`
#: (PRD 01 §5.6): resolving a class token yields an instance of that class.
T = TypeVar("T")


class _ScopeCache(dict[Any, Any]):
    """One scope level's own cache, reading through to its ancestors.

    A ``dict`` subclass rather than a mapping chain for three reasons, each of
    them a contract other code depends on:

    * ``DIContainer.resolve(request_cache=...)`` publishes ``dict[Any, Any]``
      (PRD 01 §5.3.3), and an instance of this class *is* one;
    * ``finalize_scope`` iterates the dict it is given and tears each entry
      down, so iteration must yield this level's own instances only — a chain
      would hand the parent's instances to the child's teardown (PRD 15 §5.4);
    * a plain ``dict`` accepts the same writes, so an embedder that passes its
      own cache keeps working unchanged.

    A hit in an ancestor is returned and **not** copied down: the instance was
    created at that level, and that level tears it down (PRD 15 §5.3). The walk
    up the chain is a loop, not a recursive call, so a chain deeper than the
    interpreter's stack still resolves (PRD 15 §6.6).
    """

    __slots__ = ("_parent",)

    def __init__(self, parent: _ScopeCache | None = None) -> None:
        super().__init__()
        self._parent = parent

    def __contains__(self, key: Any) -> bool:
        cache: _ScopeCache | None = self
        while cache is not None:
            if dict.__contains__(cache, key):
                return True
            cache = cache._parent
        return False

    def __getitem__(self, key: Any) -> Any:
        cache: _ScopeCache | None = self
        while cache is not None:
            if dict.__contains__(cache, key):
                return dict.__getitem__(cache, key)
            cache = cache._parent
        raise KeyError(key)

    def get(self, key: Any, default: Any = None) -> Any:
        try:
            return self[key]
        except KeyError:
            return default


class ResolutionScope:
    """One open resolution scope, owning the cache of request-scoped instances.

    The scope is the single owner of that cache: nothing else may tear it down,
    and closing it twice is a no-op rather than a double teardown. Values the
    host supplies (a request object, a job context) are visible to every
    resolution made through this scope.

    Scopes nest: :meth:`scope` opens a child scope from this one, to any depth,
    and the child's cache reads through to its ancestors — a ``REQUEST``
    instance created by the parent is reused rather than rebuilt, and only an
    instance the child itself creates is torn down when the child closes
    (PRD 15 §5.3, §5.4).

    Resolving inside a closed scope raises :class:`LifecycleViolationError`
    instead of silently constructing an instance whose teardown will never run.
    """

    __slots__ = (
        "__weakref__",
        "_cache",
        "_children",
        "_closed",
        "_container",
        "_depth",
        "_errors",
        "_max_depth",
        "_origin",
        "_parent",
        "_values",
    )

    def __init__(
        self,
        container: DIContainer,
        *,
        values: Mapping[Any, Any] | None = None,
        origin: str = "scope",
        parent: ResolutionScope | None = None,
        max_depth: int | None = None,
    ) -> None:
        self._container = container
        self._parent = parent
        self._depth: int = 1 if parent is None else parent._depth + 1
        # Values are a *copy*, taken once at creation: a child sees everything
        # its parent was seeded with, and a later mutation of either mapping is
        # not visible to the other (PRD 15 §5.5).
        inherited: dict[Any, Any] = dict(parent._values) if parent is not None else {}
        if values:
            inherited.update(values)
        self._values: dict[Any, Any] = inherited
        self._cache: _ScopeCache = _ScopeCache(None if parent is None else parent._cache)
        # Allocated on the first child, and a flat list of weak references
        # rather than a ``WeakSet``: a host opens one scope per unit of work, so
        # an unused container of children must cost nothing, and a chain of
        # levels must not pay ~1 KB per level for the privilege (measured:
        # PRD 15 §6.6).
        self._children: list[weakref.ref[ResolutionScope]] | None = None
        self._errors: tuple[DestructError, ...] = ()
        self._closed = False
        self._origin = origin
        self._max_depth: int | None = (
            max_depth
            if max_depth is not None
            else (parent._max_depth if parent is not None else None)
        )

    # -- introspection ----------------------------------------------------

    @property
    def values(self) -> dict[Any, Any]:
        """The host-supplied values visible inside this scope."""
        return self._values

    @property
    def errors(self) -> tuple[DestructError, ...]:
        """Teardown failures collected when the scope closed (empty if clean)."""
        return self._errors

    @property
    def closed(self) -> bool:
        """True once :meth:`close` has run."""
        return self._closed

    @property
    def parent(self) -> ResolutionScope | None:
        """The scope this one was opened from, or ``None`` for a root scope."""
        return self._parent

    @property
    def depth(self) -> int:
        """How many levels deep this scope sits; a root scope is ``1``."""
        return self._depth

    # -- lifecycle --------------------------------------------------------

    async def __aenter__(self) -> ResolutionScope:
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
        await self.close()
        return False

    def scope(
        self,
        *,
        values: Mapping[Any, Any] | None = None,
        max_depth: int | None = None,
    ) -> ResolutionScope:
        """Open a child scope that reads through to this one.

        ``values`` are merged over the values this scope already holds, so a
        child sees its ancestors' seeds without repeating them. ``max_depth``
        overrides the container's limit for this subtree; it may only narrow it
        (PRD 15 §5.6). Opening a level beyond the limit raises
        :class:`LifecycleViolationError` before anything is constructed.
        """
        if self._closed:
            raise LifecycleViolationError(
                "Cannot open a child scope from a closed scope",
                detail={"origin": self._origin, "state": "closed", "depth": self._depth},
            )
        limit = self._max_depth if max_depth is None else max_depth
        if max_depth is not None and max_depth < 1:
            raise LifecycleConfigError(
                f"max_depth must be at least 1, not {max_depth!r}",
                detail={"max_depth": max_depth, "reason": "invalid_max_depth"},
            )
        if max_depth is not None and self._max_depth is not None and max_depth > self._max_depth:
            raise LifecycleConfigError(
                f"max_depth {max_depth} widens this subtree's limit of {self._max_depth}",
                detail={
                    "max_depth": max_depth,
                    "inherited_max_depth": self._max_depth,
                    "reason": "widened_max_depth",
                },
            )
        depth = self._depth + 1
        if limit is not None and depth > limit:
            raise LifecycleViolationError(
                f"Cannot open scope level {depth}: max_depth is {limit}",
                detail={
                    "depth": depth,
                    "max_depth": limit,
                    "reason": "depth_exceeded",
                },
            )
        child = ResolutionScope(
            self._container,
            values=values,
            origin=self._origin,
            parent=self,
            max_depth=limit,
        )
        if self._children is None:
            self._children = []
        self._children.append(weakref.ref(child))
        return child

    # Overloads (PRD 01 §5.6), mirroring :meth:`DIContainer.resolve`: a class
    # token is statically typed, and the fallback names the two non-class token
    # kinds the surface documents rather than ``Any``, so a class token cannot
    # reach the untyped variant and a conflicting declared type is reported by
    # both checkers.
    @overload
    async def resolve(self, token: type[T], *, owning_module: type | None = None) -> T: ...

    @overload
    async def resolve(self, token: str | Token, *, owning_module: type | None = None) -> Any: ...

    async def resolve(self, token: Any, *, owning_module: type | None = None) -> Any:
        """Resolve ``token`` with this scope's cache and values."""
        if self._closed:
            raise LifecycleViolationError(
                f"Cannot resolve {_describe(token)} inside a closed scope",
                detail={"token": _describe(token), "origin": self._origin, "state": "closed"},
            )
        return await self._container.resolve(
            token,
            request_cache=self._cache,
            values=self._values,
            owning_module=owning_module,
        )

    def _live_children(self) -> list[ResolutionScope]:
        """Child scopes still open, pruning the references of those that are not."""
        if self._children is None:
            return []
        live: list[ResolutionScope] = []
        keep: list[weakref.ref[ResolutionScope]] = []
        for ref in self._children:
            child = ref()
            if child is None or child._closed:
                continue
            keep.append(ref)
            live.append(child)
        self._children = keep
        return live

    def child_scopes(self) -> tuple[ResolutionScope, ...]:
        """The child scopes opened from this one that nobody has closed yet."""
        return tuple(self._live_children())

    async def close(self) -> tuple[DestructError, ...]:
        """Tear down every instance this scope created. Idempotent; never raises.

        Open child scopes are closed first, deepest first, so an instance is
        never torn down while a scope that reads through it can still hand it
        out (PRD 15 §5.7). Both walks are iterative — this one and the cache
        lookup — because "unlimited" nesting must survive a chain deeper than
        the interpreter's stack (measured: PRD 15 §6.6). Each child keeps its
        own ``errors``; this scope's returned tuple is its own cache's failures.
        """
        if self._closed:
            return self._errors
        chain: list[ResolutionScope] = []
        stack: list[ResolutionScope] = [self]
        while stack:
            node = stack.pop()
            chain.append(node)
            stack.extend(node._live_children())
        # ``chain`` is parents-before-children, so the reverse is the teardown
        # order: a leaf's instances go before the ancestor it read through.
        for node in reversed(chain):
            if not node._closed:
                await node._close_self()
        return self._errors

    async def _close_self(self) -> None:
        """Finalise this level's own cache, without touching any other level."""
        self._closed = True
        self._errors = tuple(
            await finalize_scope(self._container, self._cache, origin=self._origin)
        )
        self._cache.clear()
        if self._parent is not None and self._parent._children is not None:
            self._parent._children = [ref for ref in self._parent._children if ref() is not self]


async def finalize_scope(
    container: DIContainer,
    cache: dict[Any, Any],
    *,
    origin: str = "request",
) -> list[DestructError]:
    """Tear down every instance in ``cache`` in reverse creation order.

    For each instance, newest first, run the provider's ``@pre_destruct`` hooks
    (every one it declares, in order), then call ``aclose()`` if the instance
    has one. A failing step is logged on the ``"lauren_di"`` logger, recorded as
    a :class:`DestructError` (its ``__cause__`` is the original exception) and
    teardown continues with the next step. Nothing is raised; the collected
    errors are returned.

    ``origin`` names the scope kind in log messages and in each error's
    ``detail`` (``"scope"``, ``"shutdown"``). The keys are snapshotted first, so
    the caller's dict is not mutated.
    """
    if not cache:
        return []
    index = container.instance_index()
    errors: list[DestructError] = []
    for key, instance in [(k, cache[k]) for k in reversed(list(cache))]:
        provider = index.get(key)
        name = _instance_label(key, provider, instance)
        hooks = provider.pre_destruct_chain if provider is not None else ()
        for hook in hooks:
            try:
                result = getattr(instance, hook.__name__)()
                if inspect.isawaitable(result):
                    await result
            except Exception as exc:
                logger.exception("Error in @pre_destruct on %s (%s teardown)", name, origin)
                errors.append(_teardown_error(name, origin, "pre_destruct", exc))
        aclose = getattr(instance, "aclose", None)
        if callable(aclose):
            try:
                result = aclose()
                if inspect.isawaitable(result):
                    await result
            except Exception as exc:
                logger.exception("Error in aclose() on %s (%s teardown)", name, origin)
                errors.append(_teardown_error(name, origin, "aclose", exc))
    return errors


def _instance_label(key: Any, provider: Provider | None, instance: Any) -> str:
    """A readable name for a cached instance (its token, else its type)."""
    target: Any = provider.cls if provider is not None else key
    if isinstance(target, int):  # a multi-binding slot is keyed by id(provider)
        target = type(instance)
    return _describe(target)


def _teardown_error(name: str, origin: str, stage: str, exc: Exception) -> DestructError:
    """Wrap a teardown failure, keeping the original as ``__cause__``."""
    step = "@pre_destruct" if stage == "pre_destruct" else "aclose()"
    error = DestructError(
        f"{step} on {name} failed during {origin} teardown: {exc}",
        detail={"class": name, "stage": stage, "origin": origin, "cause": str(exc)},
    )
    error.__cause__ = exc
    return error


__all__ = ["ResolutionScope", "finalize_scope"]
