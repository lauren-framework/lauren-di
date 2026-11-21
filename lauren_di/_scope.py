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
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, overload

# Intra-package split: ``_container`` owns the token renderer, and ``_scope``
# reports teardown errors with the same labels.
from ._container import _describe  # pyright: ignore[reportPrivateUsage]
from ._errors import DestructError, LifecycleViolationError

if TYPE_CHECKING:
    from . import DIContainer, Provider

logger = logging.getLogger("lauren_di")

#: Return type of the ``type[T]`` overload of :meth:`ResolutionScope.resolve`
#: (PRD 01 §5.6): resolving a class token yields an instance of that class.
T = TypeVar("T")


class ResolutionScope:
    """One open resolution scope, owning the cache of request-scoped instances.

    The scope is the single owner of that cache: nothing else may tear it down,
    and closing it twice is a no-op rather than a double teardown. Values the
    host supplies (a request object, a job context) are visible to every
    resolution made through this scope.

    Resolving inside a closed scope raises :class:`LifecycleViolationError`
    instead of silently constructing an instance whose teardown will never run.
    """

    __slots__ = ("_cache", "_closed", "_container", "_errors", "_origin", "_values")

    def __init__(
        self,
        container: DIContainer,
        *,
        values: Mapping[Any, Any] | None = None,
        origin: str = "scope",
    ) -> None:
        self._container = container
        self._values: dict[Any, Any] = dict(values) if values else {}
        self._cache: dict[Any, Any] = {}
        self._errors: tuple[DestructError, ...] = ()
        self._closed = False
        self._origin = origin

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

    # -- lifecycle --------------------------------------------------------

    async def __aenter__(self) -> ResolutionScope:
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
        await self.close()
        return False

    # Overloads (PRD 01 §5.6), mirroring :meth:`DIContainer.resolve`: a class
    # token is statically typed, every other token keeps the ``Any`` shape.
    @overload
    async def resolve(self, token: type[T], *, owning_module: type | None = None) -> T: ...

    @overload
    async def resolve(self, token: Any, *, owning_module: type | None = None) -> Any: ...

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

    async def close(self) -> tuple[DestructError, ...]:
        """Tear down every instance this scope created. Idempotent; never raises."""
        if self._closed:
            return self._errors
        self._closed = True
        self._errors = tuple(
            await finalize_scope(self._container, self._cache, origin=self._origin)
        )
        self._cache.clear()
        return self._errors


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
