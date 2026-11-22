"""Dependency injection container.

Providers are discovered by the ``@injectable`` decorator and compiled into an
immutable graph at startup. Three scopes are supported:

* ``SINGLETON`` — one instance per application
* ``REQUEST``   — one instance per request
* ``TRANSIENT`` — new instance on every resolution

Dependency declaration styles
-----------------------------

A class marked ``@injectable()`` may declare its dependencies in one (or
both!) of two places:

* **Constructor parameters** — ``def __init__(self, repo: UserRepo): ...``
  The classic style. Parameters are introspected at registration time
  and resolved via the container during instantiation.

* **Class-body annotations** — ::

      @injectable()
      class Service:
          repo: UserRepo
          cache: Annotated[Cache, Inject(cache_factory)]

  Field annotations that don't have a class-level default are treated as
  dependencies, and only an annotation that names a *type* is collected: the
  gate reads the annotation's token, so a token that is not a type (a
  function provider) is spelled ``Annotated[T, Inject(token)]``, which keeps
  ``T`` for checkers and carries the token for the container. ``Depends[T]``
  is that same token (``Depends[T] is T``) but it is a runtime spelling
  rather than a type form, so it belongs where it is *evaluated*, never in
  annotation position (PRD 01 §5.5). Each field's value is *resolved* before
  construction but *assigned* after the instance exists, with
  ``object.__setattr__`` (PRD 02 §5.6): a user-supplied ``__init__``
  therefore runs while the injected attributes are still unset, and reading
  one there raises ``AttributeError``. Assigning after construction is what
  keeps ``@dataclass``, ``__slots__`` and frozen classes working.

Parameters in ``__init__`` / ``__new__`` that name the same attribute as a
class-body annotation are injected by keyword as well — so existing code
that spells the dependencies in ``__init__`` keeps working, and authors can
mix the two forms to taste.

Function injectables
--------------------

``@injectable()`` also accepts a function. The function becomes a factory
provider whose return value is the dependency::

    @injectable()
    def async_sessionmaker(cfg: ConfigService) -> AsyncSessionmaker:
        return AsyncSessionmaker(cfg.db_url)

    @injectable()
    class UserRepo:
        sessmkr: Annotated[AsyncSessionmaker, Inject(async_sessionmaker)]

The function's own parameters are resolved via DI, exactly like a class
constructor's. The factory's token is the function itself, so
``resolve(async_sessionmaker)`` and ``Inject(async_sessionmaker)`` both reach
it; the container calls the function (once per scope) and returns the value.
A field annotated with the bare function is *not* collected — a function is
not a type, so the field gate skips it — which is why the field above carries
the token in ``Inject``.

Module-scoped resolution
------------------------

The container enforces module encapsulation as specified by PRD 06. Each
provider may be registered together with the *module class* that declared it,
and a caller resolves a dependency **through** its module: the container
considers only rows that module may use — its own rows, global rows
(``owning_module=None``), and rows granted to it by ``set_visible`` /
``grant_visible``.

Two modes, one switch (MV-13). While nothing is declared, resolution is the
flat registry and ``owning_module`` is recorded but never consulted (MV-21).
The first ``set_visible`` / ``grant_visible`` call turns encapsulation on for
the whole container; from then on an ``owning_module=None`` resolution means
the **global view** — global rows only, not every domain (MV-14, MV-23) — and
a module that never declared itself fails closed with
``reason="undeclared_module"`` (MV-17, MV-22).

Module owners are named ``owning_module`` for compatibility with the ``lauren``
framework; ``lauren_di`` has no module graph. A graph, if one is ever added,
lives outside this package and merely *feeds* ``set_visible`` (MV-38).
"""

from __future__ import annotations

import contextlib
import inspect
import logging
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any, TypeVar, cast, overload

from ._custom import (
    CustomProvider,
    OptionalDep,
    Token,
    # Intra-package split: ``_custom`` owns the marker type, ``_container``
    # reads it. Private by design, shared by construction.
    _InjectMarker,  # pyright: ignore[reportPrivateUsage]
)
from ._errors import (
    CircularDependencyError,
    DecoratorUsageError,
    DestructError,
    DIScopeViolationError,
    DuplicateBindingError,
    InvalidProviderError,
    LifecycleConfigError,
    MetadataInheritanceError,
    MissingProviderError,
    OverrideError,
    ProtocolAmbiguityError,
    UnresolvableParameterError,
)
from ._overrides import OverridePlan, ProviderOverride, active_override_plan
from ._typing import resolve_type_hints
from .types import Grant, Scope

_logger = logging.getLogger("lauren_di")

#: Return type of the ``type[T]`` overload of :meth:`DIContainer.resolve`
#: (PRD 01 §5.6): resolving a class token yields an instance of that class.
T = TypeVar("T")

INJECTABLE_META = "__lauren_di_injectable__"


class _GeneratorContextWrapper:
    """Wraps a one-shot generator dependency (FastAPI-style lifecycle).

    Code before ``yield`` runs as setup (post_construct); code after runs as
    teardown (pre_destruct). The wrapped ``value`` is what callers receive.
    ``aclose()`` advances the generator past the ``yield`` to run teardown.
    """

    __slots__ = ("value", "_gen", "_is_async", "_closed")

    def __init__(self, value: Any, gen: Any, *, is_async: bool) -> None:
        self.value = value
        self._gen = gen
        self._is_async = is_async
        self._closed = False

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._is_async:
            # Advance past the yield to run teardown code; expect StopAsyncIteration.
            with contextlib.suppress(StopAsyncIteration):
                await self._gen.__anext__()
        else:
            # Advance the sync generator past the yield to run teardown code.
            # next() raises StopIteration when the generator is exhausted (normal case).
            with contextlib.suppress(StopIteration):
                next(self._gen)


# ---------------------------------------------------------------------------
# Multi-binding collection tokens (``list[T]``)
# ---------------------------------------------------------------------------


def _multi_binding_element_type(token: Any) -> Any | None:
    """Return ``T`` when ``token`` is ``list[T]`` (or ``typing.List[T]``).

    This is the *only* collection shape the container treats as a
    multi-binding request. A handler parameter, constructor argument, or
    class-body annotation typed as ``list[SomeProtocol]`` tells the
    container: "give me every provider registered with ``multi=True``
    for ``SomeProtocol``, wrapped in a list."

    The narrow scope is intentional — widening to ``Sequence[T]`` /
    ``Iterable[T]`` / ``Collection[T]`` would quietly capture
    ``BaseModel`` fields, third-party generics and a host of unrelated
    type hints. ``list[T]`` is the canonical, unambiguous spelling and
    mirrors the shape that ``DIContainer.resolve(T)`` already returns
    for multi-bound tokens.

    Returns ``None`` for anything else (bare types, ``dict[K, V]``,
    ``Optional[X]``, …) so the caller can fall through to its normal
    single-provider resolution path.
    """
    import typing as _typing

    origin = _typing.get_origin(token)
    # ``list[X]`` has ``list`` as its origin (the builtin); ``typing.List[X]``
    # normalises to the same thing in 3.9+. Anything else — ``dict``,
    # ``tuple``, ``Annotated``, ``Union`` — is not a multi-binding request.
    if origin is not list:
        return None
    args = _typing.get_args(token)
    # ``list[T]`` has exactly one type argument; reject ``list`` (bare),
    # ``list[T, U]`` (illegal), and similar oddities defensively.
    if len(args) != 1:
        return None
    return args[0]


@dataclass
class InjectableMeta:
    """The immutable record ``@injectable`` writes onto the target.

    ``scope`` is the lifetime the container builds the target in, ``provides`` the
    extra lookup keys the same row answers to, and ``multi`` whether the row
    accumulates so ``list[T]`` resolves to all of them.
    """

    scope: Scope = Scope.SINGLETON
    provides: tuple[type, ...] = ()
    multi: bool = False


@dataclass
class Provider:
    """A compiled provider entry.

    ``cls`` is the DI token the provider binds to. Despite the legacy
    name, it is **not** required to be a class — string tokens and
    :class:`Token` instances live here too, registered through the
    custom-provider helpers (:func:`use_value`, :func:`use_class`,
    :func:`use_factory`, :func:`use_existing`). The annotation stays
    typed as ``type`` for backward compatibility with the wide surface
    of code that already imports ``Provider``; callers that need to
    reason about non-class tokens should use :attr:`provider_kind`.

    ``factory`` is the callable actually invoked to produce an instance.
    For classes it's the class itself (``Cls(**kwargs)``); for function
    providers it's the decorated function. Keeping the split explicit
    means the runtime never needs to ask "is this a class or a function?"
    — it always calls ``factory(**kwargs)``.

    ``deps`` are constructor / function arguments to resolve via DI.
    ``field_deps`` are class-body-annotated attributes resolved to plain
    attributes set on the instance before ``__init__`` runs.
    """

    cls: type
    scope: Scope
    provides: tuple[type, ...]
    multi: bool
    deps: tuple[tuple[str, Any], ...]
    #: The first ``@post_construct`` / ``@pre_destruct`` hook (kept for
    #: readers of a single hook); :attr:`post_construct_hooks` /
    #: :attr:`pre_destruct_hooks` hold every hook in run order.
    post_construct: Callable[..., Any] | None = None
    pre_destruct: Callable[..., Any] | None = None
    #: Module class that declared this provider. ``None`` means the provider
    #: was registered without a module context (test fixtures, late-registered
    #: helpers like auto-discovered middleware/guards) and is therefore visible
    #: to every module.
    owning_module: type | None = None
    #: The callable invoked to produce an instance. Usually ``cls`` itself
    #: for class providers; for function providers (``@injectable()`` on a
    #: ``def``) it's the decorated function. Kept nullable so existing
    #: construction sites that set ``cls`` continue to work — see
    #: :meth:`_call_factory` for the fallback.
    factory: Callable[..., Any] | None = None
    #: Class-body annotated dependencies: ``(attribute_name, type)`` pairs.
    #: Resolved before ``__init__`` runs and set as plain attributes.
    field_deps: tuple[tuple[str, type], ...] = ()
    #: True when this provider was declared via ``@injectable()`` on a
    #: function rather than a class. Affects lifecycle semantics (no
    #: ``@post_construct`` / ``@pre_destruct`` on a plain value).
    is_function_provider: bool = False
    #: True when the factory is a generator or async generator function.
    #: Code before ``yield`` runs as setup; code after runs as teardown.
    #: Teardown is invoked via ``aclose()`` at end of scope lifetime.
    is_generator_provider: bool = False
    #: Custom-provider kind: ``"value"`` / ``"class"`` / ``"factory"`` /
    #: ``"existing"`` for the four NestJS-style recipes, or ``None`` for
    #: the legacy ``@injectable``-decorated registration. Used by the
    #: dispatcher to skip lifecycle hooks on value providers, redirect
    #: alias resolutions, and forbid duplicate registration when one
    #: side is custom.
    provider_kind: str | None = None
    #: For ``provider_kind == "existing"`` only: the token this alias
    #: redirects to. The dispatcher sees this and forwards the resolve
    #: call rather than running its own factory.
    alias_target: Any = None
    #: Indices into ``deps`` whose values may resolve to ``None``
    #: instead of raising :class:`MissingProviderError`. Populated by
    #: :meth:`DIContainer.register_factory` when an entry of
    #: ``injects=[...]`` was wrapped in :class:`OptionalDep`.
    optional_dep_names: frozenset[str] = field(default_factory=frozenset)
    #: Positional-argument names (in order) for factory-style providers.
    #: When present the resolver passes the resolved deps positionally
    #: rather than by keyword — supporting raw lambdas where parameter
    #: names are arbitrary placeholders.
    positional_arg_names: tuple[str, ...] = ()
    #: Every ``@post_construct`` hook in run order: base classes first,
    #: definition order within a class. Empty means "use
    #: :attr:`post_construct`" (providers built by hand).
    post_construct_hooks: tuple[Callable[..., Any], ...] = ()
    #: Every ``@pre_destruct`` hook in run order: subclasses first,
    #: definition order within a class.
    pre_destruct_hooks: tuple[Callable[..., Any], ...] = ()

    @property
    def post_construct_chain(self) -> tuple[Callable[..., Any], ...]:
        """The ``@post_construct`` hooks to run, in order (empty when none)."""
        if self.post_construct_hooks:
            return self.post_construct_hooks
        return (self.post_construct,) if self.post_construct is not None else ()

    @property
    def pre_destruct_chain(self) -> tuple[Callable[..., Any], ...]:
        """The ``@pre_destruct`` hooks to run, in order (empty when none)."""
        if self.pre_destruct_hooks:
            return self.pre_destruct_hooks
        return (self.pre_destruct,) if self.pre_destruct is not None else ()


class DIContainer:
    """Compiled DI container; immutable after :meth:`compile`.

    ``overrides`` is applied while rows are registered. ``strict_request_scope``
    turns the lax request-scope fallback into an error. ``value_tokens`` nominates
    tokens that are supplied from a scope rather than by a provider.
    ``field_predicate`` returns ``False`` for class-body annotations your own layer
    owns, so they are left alone.

    Registration: ``register``, ``register_class``, ``register_instance``,
    ``register_value``, ``register_factory``, ``register_alias``,
    ``register_custom``. Resolution: ``compile``, ``resolve``, ``scope``.
    Lifecycle: ``startup``, ``shutdown``. Introspection: ``get_provider``,
    ``has_provider``, ``all_providers``, ``registered_providers``,
    ``applied_overrides``, ``singletons``, ``set_singleton``,
    ``mark_singleton_initialized``, ``instance_index``, ``adopt_lifecycle``,
    ``explain``. Visibility: ``set_visible``, ``grant_visible``, ``visible_in``.
    """

    def __init__(
        self,
        *,
        overrides: OverridePlan | None = None,
        strict_request_scope: bool = False,
        value_tokens: Iterable[Any] = (),
        field_predicate: Callable[[str, Any], bool] | None = None,
    ) -> None:
        self._providers: dict[type, Provider] = {}
        # Token -> list of providers (for protocols / multi-bindings)
        self._token_bindings: dict[type, list[Provider]] = {}
        # Keys are normally the provider token (type | str | Token) but
        # multi-binding providers use id(provider) (int) to keep each
        # sibling's singleton slot distinct.
        self._singletons: dict[Any, Any] = {}
        self._singletons_initialized: set[Any] = set()
        self._compiled = False
        #: module_cls -> frozenset of *declared* grants (PRD 06 §5.4). A bare
        #: grant names a token (every row bound to it); a ``(token, owner)``
        #: pair addresses exactly one row. A module's effective visible set is
        #: its own rows plus these grants (MV-06) — see :meth:`visible_in`.
        self._visible: dict[type, frozenset[Grant]] = {}
        #: The container-wide encapsulation switch (MV-13). ``False`` until the
        #: first :meth:`set_visible` / :meth:`grant_visible` call, which is why
        #: a container that never declares anything is the flat registry of
        #: PRDs 02–05 (MV-21).
        self._encapsulation_on = False
        #: Every provider in registration order, keyed by ``id(provider)``.
        #: Unlike ``_providers`` (keyed by token), multi-bound siblings that
        #: share a token all appear here.
        self._registered: dict[int, Provider] = {}
        #: Instance-cache key -> provider, for scope teardown. Built by
        #: :meth:`compile` and rebuilt lazily after a late registration.
        self._instance_index: dict[Any, Provider] | None = None
        #: Build-time overrides (:mod:`lauren_di._overrides`); ``None`` in production.
        #: Every registration path consults it, so an override replaces its
        #: target wherever — and whenever — the target would be registered.
        #: When no explicit plan is given the ambient one is used (PRD 07
        #: OV-20), so a builder that calls ``building_with`` need not pass it.
        self._overrides: OverridePlan | None = (
            overrides if overrides is not None else active_override_plan()
        )
        #: token -> provider rows built from an override, once applied.
        self._override_rows: dict[Any, list[Provider]] = {}
        #: ``DIContainer(strict_request_scope=True)``: resolving a
        #: ``Scope.REQUEST`` provider outside any open scope raises instead of
        #: falling back to an untracked transient.
        self._strict_request_scope = strict_request_scope
        #: Request-scoped providers already warned about (lax mode warns once each).
        self._unscoped_warned: set[Any] = set()
        #: id -> object for pre-built instances whose lifecycle hooks this
        #: container runs (see :meth:`register_instance`). Holding the object
        #: keeps the id stable; one entry per object, whatever its tokens.
        self._lifecycle_owners: dict[int, Any] = {}
        #: Tokens the host supplies itself (an HTTP request, a job context,
        #: ...). No provider is ever expected for these, so ``compile`` skips
        #: them when validating edges. Replaces the framework's hard-coded
        #: ``Request`` import (D-31).
        self._value_tokens: frozenset[Any] = frozenset(value_tokens)
        #: Pluggable ``(field_name, annotation) -> bool`` gate that lets an
        #: embedder declare extra class-body fields as DI fields (D-33).
        #: ``None`` keeps lauren_di's own structural rule.
        self._field_predicate: Callable[[str, Any], bool] | None = field_predicate

    # -- Registration ------------------------------------------------------

    def register(
        self, target: type | Callable[..., Any], *, owning_module: type | None = None
    ) -> Provider:
        """Register a class or function provider.

        Both flavours are accepted. The decorator
        (:func:`lauren_di.injectable`) already attaches
        :class:`InjectableMeta`; this method reads that marker, inspects
        the target's callable signature (and, for classes, class-body
        field annotations), and installs an immutable :class:`Provider`
        entry. Classes and functions are routed by the same registry
        so module visibility, scope validation, and multi-binding all
        work uniformly.
        """
        self._validate_owning_module(owning_module, target)
        if not (isinstance(target, type) or callable(target)):
            raise InvalidProviderError(
                f"Cannot register {_describe(target)}: expected a class or a callable",
                detail={"target": _describe(target), "kind": type(target).__name__},
            )
        meta = _get_injectable_meta(target)
        if self._overrides is not None:
            override = self._overrides.get(target)
            if override is not None:
                return self._register_override(
                    target,
                    override,
                    owning_module=owning_module,
                    target_scope=meta.scope if meta is not None else Scope.SINGLETON,
                    provides=tuple(meta.provides) if meta is not None else (),
                    multi=meta.multi if meta is not None else False,
                )
        if meta is None:
            raise MissingProviderError(
                f"{_describe(target)} is not marked @injectable",
                detail={"token": _describe(target)},
            )

        # Function providers: the token the container indexes by is the
        # function itself (functions are hashable and unique), so
        # ``Depends[my_factory]`` resolves correctly.
        if not inspect.isclass(target):
            token: Any = target
            if token in self._providers:
                raise self._duplicate_error(target)
            deps = _inspect_callable_deps(target, is_class_init=False)
            _is_gen = inspect.isgeneratorfunction(target) or inspect.isasyncgenfunction(target)
            if _is_gen and meta.scope == Scope.TRANSIENT:
                raise LifecycleConfigError(
                    f"Generator function provider {_describe(target)!r} cannot use "
                    "Scope.TRANSIENT — transient instances are not tracked for cleanup.",
                    detail={"provider": _describe(target)},
                )
            provider = Provider(
                cls=token,
                scope=meta.scope,
                provides=tuple(meta.provides),
                multi=meta.multi,
                deps=tuple(deps),
                post_construct=None,
                pre_destruct=None,
                owning_module=owning_module,
                factory=target,
                field_deps=(),
                is_function_provider=True,
                is_generator_provider=_is_gen,
            )
            self._providers[token] = provider
            self._bind_token(token, provider)
            for tok in meta.provides:
                self._bind_token(tok, provider)
            return provider

        cls = target
        if cls in self._providers:
            raise self._duplicate_error(cls)

        # Class-body field annotations: any name annotated on the class
        # body that (a) has no class-level default value AND (b) whose
        # type resolves to a real class (not a ``ClassVar`` / primitive
        # with an explicit default) is a candidate for injection. The
        # compiler cross-references these against the DI graph during
        # :meth:`compile` so missing providers surface at startup.
        field_deps = _collect_field_deps(cls, field_predicate=self._field_predicate)

        # Use ``inspect.signature(cls)`` as the single source of truth
        # for the class's callable surface. The stdlib walks any
        # ``__signature__`` attribute, the metaclass's ``__call__``,
        # and only then falls back to ``__init__`` / ``__new__`` — so
        # this one call handles every shape Python supports, including
        # Pydantic models, attrs classes, and metaclass-driven
        # factories. The legacy two-callable merge would have ignored
        # all three.
        deps = _inspect_class_deps(cls)

        post = _find_lifecycle_hooks(cls, "__lauren_di_post_construct__")
        pre = _find_lifecycle_hooks(cls, "__lauren_di_pre_destruct__", subclass_first=True)
        provider = Provider(
            cls=cls,
            scope=meta.scope,
            provides=tuple(meta.provides),
            multi=meta.multi,
            deps=tuple(deps),
            post_construct=post[0] if post else None,
            pre_destruct=pre[0] if pre else None,
            owning_module=owning_module,
            factory=cls,
            field_deps=tuple(field_deps),
            is_function_provider=False,
            post_construct_hooks=post,
            pre_destruct_hooks=pre,
        )
        self._providers[cls] = provider
        self._bind_token(cls, provider)
        for tok in meta.provides:
            self._bind_token(tok, provider)
        return provider

    def _bind_token(self, token: Any, provider: Provider) -> None:
        if self._overrides is not None:
            override = self._overrides.get(token)
            if override is not None and not any(
                row is provider for row in self._override_rows.get(token, ())
            ):
                # ``provider`` reaches an overridden token through ``provides``
                # (a Protocol / multi-binding token): the override replaces
                # the binding instead of joining it.
                self._register_override(
                    token,
                    override,
                    owning_module=None,
                    target_scope=provider.scope,
                    provides=(),
                    multi=provider.multi,
                )
                return
        self._token_bindings.setdefault(token, []).append(provider)
        self._registered.setdefault(id(provider), provider)
        self._instance_index = None
        # A late registration changes the graph, so the next resolve revalidates
        # instead of trusting a stale ``compile()`` (PRD 05 §5.1, MV-26).
        self._compiled = False

    # ------------------------------------------------------------------
    # Build-time overrides (:mod:`lauren_di._overrides`)
    # ------------------------------------------------------------------

    def applied_overrides(self) -> frozenset[Any]:
        """Tokens whose registration an override has replaced in this container."""
        return frozenset(self._override_rows)

    def _register_override(
        self,
        token: Any,
        override: ProviderOverride,
        *,
        owning_module: type | None,
        target_scope: Scope,
        provides: tuple[Any, ...],
        multi: bool,
    ) -> Provider:
        """Install the provider rows ``override`` describes in place of ``token``.

        The rows keep the target's token, owning module, ``provides`` and
        ``multi`` flag, so module visibility, Protocol bindings and
        ``list[T]`` membership are exactly what the replaced provider had.
        Applied once per token: later registrations of the same token (a
        multi-binding sibling, a repeated lazy registration) reuse the rows.
        No user class is mutated — the rows are built directly.
        """
        existing = self._override_rows.get(token)
        if existing:
            return existing[0]
        # A ``values`` override replaces a collection; on a single-row target it
        # would silently change ``resolve(T)`` from ``T`` to ``list[T]`` (OV-06).
        if override.kind == "values" and not multi:
            raise OverrideError(
                f"cannot override values {_describe(token)}: non_multi_values",
                detail={
                    "token": _describe(token),
                    "kind": override.kind,
                    "reason": "non_multi_values",
                    "hint": "register the token with multi=True, or override it with use_value",
                },
            )
        # ``scope=`` may narrow the target's lifetime, never widen it (OV-12):
        # a replacement that outlives what consumers were validated against
        # would reintroduce the very scope violation compile() refuses.
        if override.scope is not None and override.scope > target_scope:
            raise OverrideError(
                f"cannot override {override.kind} {_describe(token)}: scope_widen",
                detail={
                    "token": _describe(token),
                    "kind": override.kind,
                    "reason": "scope_widen",
                    "hint": f"{target_scope.label} cannot be widened to {override.scope.label}",
                },
            )
        if override.kind == "values":
            rows = [
                self._override_value_row(
                    token,
                    value,
                    owning_module=owning_module,
                    provides=provides,
                    multi=True,
                    scope=target_scope,
                )
                for value in override.values
            ]
        elif override.kind == "value":
            rows = [
                self._override_value_row(
                    token,
                    override.value,
                    owning_module=owning_module,
                    provides=provides,
                    multi=multi,
                    scope=target_scope,
                )
            ]
        else:
            rows = [
                self._override_built_row(
                    token,
                    override,
                    owning_module=owning_module,
                    scope=override.scope if override.scope is not None else target_scope,
                    provides=provides,
                    multi=multi,
                )
            ]
        self._override_rows[token] = rows
        for row in rows:
            self._providers[token] = row
            self._token_bindings.setdefault(token, []).append(row)
            # A ready-made value needs no factory run, so seed the cache the
            # resolver would otherwise populate. Only a SINGLETON row may be
            # seeded container-wide: a REQUEST row belongs to one scope's cache
            # and a TRANSIENT row is never tracked (OV-11, OV-14).
            if row.provider_kind == "value" and row.scope is Scope.SINGLETON:
                key: Any = id(row) if row.multi else token
                self._singletons[key] = row.factory() if row.factory is not None else None
                self._singletons_initialized.add(key)
            for tok in provides:
                self._bind_token(tok, row)
        return rows[0]

    def _override_value_row(
        self,
        token: Any,
        value: Any,
        *,
        owning_module: type | None,
        provides: tuple[Any, ...],
        multi: bool,
        scope: Scope = Scope.SINGLETON,
    ) -> Provider:
        def _value_factory() -> Any:
            return value

        return Provider(
            cls=token,
            scope=scope,
            provides=provides,
            multi=multi,
            deps=(),
            owning_module=owning_module,
            factory=_value_factory,
            is_function_provider=True,
            provider_kind="value",
        )

    def _override_built_row(
        self,
        token: Any,
        override: ProviderOverride,
        *,
        owning_module: type | None,
        scope: Scope,
        provides: tuple[Any, ...],
        multi: bool,
    ) -> Provider:
        if override.kind == "class":
            cls = override.use_class
            if not isinstance(cls, type):
                raise UnresolvableParameterError(
                    f"use_class override for {_describe(token)} needs a class; got {cls!r}"
                )
            post = _find_lifecycle_hooks(cls, "__lauren_di_post_construct__")
            pre = _find_lifecycle_hooks(cls, "__lauren_di_pre_destruct__", subclass_first=True)
            return Provider(
                cls=token,
                scope=scope,
                provides=provides,
                multi=multi,
                deps=tuple(_inspect_class_deps(cls)),
                owning_module=owning_module,
                factory=cls,
                field_deps=tuple(_collect_field_deps(cls, field_predicate=self._field_predicate)),
                is_function_provider=False,
                provider_kind="class",
                post_construct=post[0] if post else None,
                pre_destruct=pre[0] if pre else None,
                post_construct_hooks=post,
                pre_destruct_hooks=pre,
            )
        if override.kind == "factory":
            optional_names: set[str] = set()
            deps: list[tuple[str, Any]] = []
            for i, raw in enumerate(override.injects):
                name = f"__factory_arg_{i}"
                if isinstance(raw, OptionalDep):
                    optional_names.add(name)
                    raw = raw.token
                deps.append((name, raw))
            return Provider(
                cls=token,
                scope=scope,
                provides=provides,
                multi=multi,
                deps=tuple(deps),
                owning_module=owning_module,
                factory=override.factory,
                is_function_provider=True,
                provider_kind="factory",
                optional_dep_names=frozenset(optional_names),
                positional_arg_names=tuple(name for name, _ in deps),
            )
        # "existing": an alias row; the resolver forwards to the target.
        return Provider(
            cls=token,
            scope=scope,
            provides=provides,
            multi=multi,
            deps=(),
            owning_module=owning_module,
            factory=None,
            is_function_provider=True,
            provider_kind="existing",
            alias_target=override.existing,
        )

    # ------------------------------------------------------------------
    # Custom-provider registration
    # ------------------------------------------------------------------
    #
    # The four methods below mirror NestJS's useValue / useClass /
    # useFactory / useExisting recipes. Each lowers a high-level
    # description into a :class:`Provider` row that the existing
    # resolver path already knows how to handle. Keeping the lowering
    # here (rather than in a caller) means tests can drive the container
    # directly without booting an entire application.

    def register_value(
        self,
        token: Any,
        value: Any,
        *,
        owning_module: type | None = None,
        multi: bool = False,
    ) -> Provider:
        """Register a literal ``value`` under ``token`` (NestJS ``useValue``).

        The container caches the value as if it were a singleton and
        returns it untouched on every resolve. The factory the
        :class:`Provider` carries is a no-op closure so the resolver's
        "call factory" path still runs without special-casing.

        Multi-binding registrations don't pre-populate the singleton
        cache because that cache is keyed by token — two providers for
        the same token would race each other. The factory still runs
        on resolve, so the value still bypasses any user-construction.
        """
        self._validate_owning_module(owning_module, token)
        if token in self._providers and not multi:
            raise self._duplicate_error(token)

        # Closure over ``value`` — the resolver invokes the factory
        # exactly once on first resolve, after which the singleton
        # cache short-circuits subsequent calls.
        def _value_factory() -> Any:
            return value

        # Multi-binding: keep ``token`` as ``provider.cls`` (needed for the
        # module-visibility check), but pre-populate the singleton cache
        # with the provider's identity key (see ``_singleton_key``) instead
        # of the token. This prevents the first multi-bound value from
        # "claiming" the shared token slot in ``_singletons``, which would
        # cause every sibling provider to return that same value.
        cache_key: Any = token
        provider = Provider(
            cls=cache_key,
            scope=Scope.SINGLETON,
            provides=(),
            multi=multi,
            deps=(),
            owning_module=owning_module,
            factory=_value_factory,
            is_function_provider=True,
            provider_kind="value",
        )
        self._providers[cache_key] = provider
        self._bind_token(token, provider)
        # Pre-populate the singleton cache.  For multi-binding providers
        # we key by ``id(provider)`` instead of the token so that sibling
        # providers sharing the same ``provide=`` token don't collide in
        # the singleton cache (see ``_singleton_key`` helper).
        singleton_key = id(provider) if multi else cache_key
        self._singletons[singleton_key] = value
        self._singletons_initialized.add(singleton_key)
        return provider

    def register_instance(
        self,
        token: Any,
        instance: Any,
        *,
        owning_module: type | None = None,
    ) -> Provider:
        """Register a pre-built singleton whose lifecycle hooks run with the app.

        Unlike :meth:`register_value` (``use_value``), the hooks declared on
        ``type(instance)`` — ``@post_construct`` / ``@pre_destruct``, or else
        ``startup()`` / ``aclose()`` methods — run at ``container.startup()`` /
        ``container.shutdown()``. They run once per object: registering the same
        object under a second token (or adopting it again) adds no hooks.
        A build-time override of ``token`` (:mod:`lauren_di._overrides`) wins.
        """
        self._validate_owning_module(owning_module, token)
        if self._overrides is not None:
            override = self._overrides.get(token)
            if override is not None:
                return self._register_override(
                    token,
                    override,
                    owning_module=owning_module,
                    target_scope=Scope.SINGLETON,
                    provides=(),
                    multi=False,
                )
        provider = self.register_value(token, instance, owning_module=owning_module)
        if id(instance) not in self._lifecycle_owners:
            # ``instance`` is ``Any``, so ``type(instance)`` is ``type[Unknown]``
            # to a strict checker; a ``type`` local keeps the call fully typed.
            instance_type: type = type(instance)
            post, pre = instance_lifecycle_hooks(instance_type)
            if post or pre:
                self._lifecycle_owners[id(instance)] = instance
                provider.post_construct = post[0] if post else None
                provider.pre_destruct = pre[0] if pre else None
                provider.post_construct_hooks = post
                provider.pre_destruct_hooks = pre
        return provider

    def adopt_lifecycle(self, instance: Any) -> None:
        """Run ``instance``'s lifecycle hooks without registering it as a token.

        For objects the container holds directly rather than resolves — a
        resource passed to a collaborator, or an adapter the application owns.
        A no-op when the object declares no hooks or has already been adopted.
        """
        if id(instance) in self._lifecycle_owners:
            return
        instance_type: type = type(instance)
        post, pre = instance_lifecycle_hooks(instance_type)
        if not post and not pre:
            return
        name = getattr(instance_type, "__qualname__", instance_type.__name__)
        self.register_instance(Token(f"lauren_di.lifecycle:{name}"), instance)

    def register_class(
        self,
        token: Any,
        cls: type,
        *,
        owning_module: type | None = None,
        scope: Scope = Scope.SINGLETON,
        multi: bool = False,
    ) -> Provider:
        """Register ``cls`` under ``token`` (NestJS ``useClass``).

        Unlike a plain ``@injectable`` registration, the bound class
        does not have to be the same as the token — that's the whole
        point of ``useClass``. The class's ``__init__`` and class-body
        deps are introspected as usual so its own collaborators
        resolve through DI.

        If the class isn't already marked ``@injectable`` we synthesise
        the metadata so the user doesn't have to remember to decorate
        a target they're only re-binding under another token.
        """
        self._validate_owning_module(owning_module, token)
        # Runtime guard: the annotation says ``type``, but ``register_class`` is
        # reachable from untyped code and the error below names the offender far
        # better than the ``AttributeError`` that would otherwise escape.
        if not isinstance(cls, type):  # pyright: ignore[reportUnnecessaryIsInstance]
            raise UnresolvableParameterError(
                f"register_class expected a class; got {cls!r}",
            )
        if token in self._providers and not multi:
            raise self._duplicate_error(token)
        # Synthesise injectable meta on the class if missing so the
        # standard inspection path does not balk — the user explicitly
        # opted in by passing it to use_class.
        if INJECTABLE_META not in cls.__dict__:
            setattr(cls, INJECTABLE_META, InjectableMeta(scope=scope))
        field_deps = _collect_field_deps(cls, field_predicate=self._field_predicate)
        # Same rationale as :meth:`register` — inspect the class as a
        # callable to honour custom ``__signature__`` / metaclass
        # ``__call__`` overrides instead of demanding ``__init__``
        # parameters that may never run.
        deps = _inspect_class_deps(cls)
        post = _find_lifecycle_hooks(cls, "__lauren_di_post_construct__")
        pre = _find_lifecycle_hooks(cls, "__lauren_di_pre_destruct__", subclass_first=True)
        provider = Provider(
            cls=token,
            scope=scope,
            provides=(),
            multi=multi,
            deps=tuple(deps),
            owning_module=owning_module,
            factory=cls,
            field_deps=tuple(field_deps),
            is_function_provider=False,
            provider_kind="class",
            post_construct=post[0] if post else None,
            pre_destruct=pre[0] if pre else None,
            post_construct_hooks=post,
            pre_destruct_hooks=pre,
        )
        self._providers[token] = provider
        self._bind_token(token, provider)
        return provider

    def register_factory(
        self,
        token: Any,
        factory: Callable[..., Any],
        *,
        inject: tuple[Any, ...] = (),
        scope: Scope = Scope.SINGLETON,
        owning_module: type | None = None,
        multi: bool = False,
    ) -> Provider:
        """Register a factory callable under ``token`` (NestJS ``useFactory``).

        ``inject`` lists the tokens ``lauren_di`` resolves and passes
        positionally to ``factory``. Optional dependencies are wrapped
        in :class:`OptionalDep` and lower to ``None`` when no provider
        is visible — mirroring NestJS's ``{ token, optional: true }``.

        The factory itself does **not** need to be ``@injectable``
        decorated. We treat its parameters as pure positional slots
        keyed by ``inject``, so a bare lambda works just as well as a
        named function. This contrasts with ``register()`` for a
        function provider, where ``lauren_di`` introspects the function's
        own signature and resolves by *parameter type annotation*.
        """
        self._validate_owning_module(owning_module, token)
        if token in self._providers and not multi:
            raise self._duplicate_error(token)
        # A factory may be a generator function, in which case ``yield`` is the
        # setup/teardown boundary exactly as it is for a generator provider
        # registered through :meth:`register` (PRD 02 §5.8). The resolver and
        # ``shutdown()`` already drive every provider whose record carries
        # ``is_generator_provider``, so detecting it here is what keeps the two
        # registration spellings one behaviour: without this flag the suspended
        # generator itself was handed to consumers as the dependency, and its
        # ``finally`` never ran.
        _is_gen = inspect.isgeneratorfunction(factory) or inspect.isasyncgenfunction(factory)
        if _is_gen and scope == Scope.TRANSIENT:
            raise LifecycleConfigError(
                f"Generator function provider {_describe(factory)!r} cannot use "
                "Scope.TRANSIENT \u2014 transient instances are not tracked for cleanup.",
                detail={"provider": _describe(factory)},
            )
        # Build synthetic param names so the existing kwargs-based
        # resolver code path keeps working. Optional positions are
        # tracked separately so the resolver can fall back to ``None``.
        optional_names: set[str] = set()
        deps: list[tuple[str, Any]] = []
        positional_names: list[str] = []
        for i, raw in enumerate(inject):
            name = f"__factory_arg_{i}"
            if isinstance(raw, OptionalDep):
                optional_names.add(name)
                tok = raw.token
            else:
                tok = raw
            deps.append((name, tok))
            positional_names.append(name)
        provider = Provider(
            cls=token,
            scope=scope,
            provides=(),
            multi=multi,
            deps=tuple(deps),
            owning_module=owning_module,
            factory=factory,
            is_function_provider=True,
            is_generator_provider=_is_gen,
            provider_kind="factory",
            optional_dep_names=frozenset(optional_names),
            positional_arg_names=tuple(positional_names),
        )
        self._providers[token] = provider
        self._bind_token(token, provider)
        return provider

    def register_alias(
        self,
        token: Any,
        existing: Any,
        *,
        owning_module: type | None = None,
    ) -> Provider:
        """Alias ``token`` to ``existing`` (NestJS ``useExisting``).

        Alias rows carry no factory of their own; the resolver detects
        ``provider_kind == 'existing'`` and forwards the lookup to
        the target. Cycle detection runs at compile time.
        """
        self._validate_owning_module(owning_module, token)
        if token in self._providers:
            raise self._duplicate_error(token)
        provider = Provider(
            cls=token,
            scope=Scope.SINGLETON,  # placeholder; validation stamps the terminal scope (D-27)
            provides=(),
            multi=False,
            deps=(),
            owning_module=owning_module,
            factory=None,
            is_function_provider=True,
            provider_kind="existing",
            alias_target=existing,
        )
        self._providers[token] = provider
        self._bind_token(token, provider)
        return provider

    def register_custom(
        self, custom: CustomProvider, *, owning_module: type | None = None
    ) -> Provider:
        """Single entry point that dispatches to the right helper.

        Callers that receive a :func:`use_value` / :func:`use_class` /
        :func:`use_factory` / :func:`use_existing` record do not need to know
        which of the four it is.
        """
        self._validate_owning_module(owning_module, custom.provide)
        if self._overrides is not None:
            override = self._overrides.get(custom.provide)
            if override is not None:
                return self._register_override(
                    custom.provide,
                    override,
                    owning_module=owning_module,
                    target_scope=custom.scope
                    if custom.kind in ("class", "factory")
                    else Scope.SINGLETON,
                    provides=(),
                    multi=custom.multi,
                )
        if custom.kind == "value":
            return self.register_value(
                custom.provide,
                custom.value,
                owning_module=owning_module,
                multi=custom.multi,
            )
        if custom.kind == "class":
            assert custom.use_class is not None
            return self.register_class(
                custom.provide,
                custom.use_class,
                owning_module=owning_module,
                scope=custom.scope,
                multi=custom.multi,
            )
        if custom.kind == "factory":
            assert custom.factory is not None
            return self.register_factory(
                custom.provide,
                custom.factory,
                inject=custom.inject,
                scope=custom.scope,
                owning_module=owning_module,
                multi=custom.multi,
            )
        if custom.kind == "existing":
            return self.register_alias(
                custom.provide,
                custom.existing,
                owning_module=owning_module,
            )
        raise ValueError(f"unknown custom provider kind: {custom.kind!r}")

    # -- Visibility (PRD 06) -----------------------------------------------

    def set_visible(self, module_cls: type, grants: frozenset[Grant]) -> None:
        """Replace ``module_cls``'s declared grants.

        A module's *effective* set is always its own rows plus these grants
        (MV-06), so an empty ``grants`` means "own rows and global rows only",
        never "nothing" (MV-10). The first declaration on a container switches
        encapsulation on for every module-scoped call (MV-13) and invalidates
        :meth:`compile` (MV-26).
        """
        self._declare(module_cls, frozenset(grants))

    def grant_visible(self, module_cls: type, grants: frozenset[Grant]) -> None:
        """Union ``grants`` into ``module_cls``'s declared grants.

        A grant is a bare token — every row bound to it becomes visible,
        whichever class implements it (MV-07) — or a ``(token, owner)`` pair
        addressing exactly one row (MV-08). A grant that matches no row yet is
        recorded rather than rejected, so a module table may be written before
        the container is populated (MV-09).
        """
        self._declare(module_cls, self._visible.get(module_cls, frozenset()) | frozenset(grants))

    def _declare(self, module_cls: type, grants: frozenset[Grant]) -> None:
        """Store one module's grants and flip the container-wide switch."""
        self._validate_owning_module(module_cls, module_cls)
        self._visible[module_cls] = frozenset(grants)
        self._encapsulation_on = True
        self._compiled = False

    def visible_in(self, module_cls: type) -> frozenset[Any]:
        """The effective visible set for ``module_cls``: own tokens, then grants.

        Row-address grants appear as their bare token (MV-08), and grants that
        match no row stay in the set because they are recorded, not dropped
        (MV-09). A module with no declaration has only its own tokens.
        """
        tokens: set[Any] = {
            row.cls for row in self._registered.values() if row.owning_module is module_cls
        }
        for grant in self._visible.get(module_cls, frozenset()):
            address = _grant_address(grant)
            tokens.add(grant if address is None else address[0])
        return frozenset(tokens)

    def _is_visible(self, provider: Provider, owning_module: type | None) -> bool:
        """Whether ``provider`` belongs to ``owning_module``'s domain (PRD 06 §5.5).

        One switch, then five cases: the flat registry while nothing is
        declared (MV-21); global rows visible everywhere (MV-01); the global
        view holding global rows only (MV-14, MV-23); a module's own rows
        being non-revocable (MV-06); and everything else needing a grant to the
        consumer's *row*, not merely to its class name (MV-07, MV-08). An
        undeclared consumer fails closed (MV-17, MV-22).
        """
        if not self._encapsulation_on:
            return True
        if provider.owning_module is None:
            return True
        if owning_module is None:
            return False
        if provider.owning_module is owning_module:
            return True
        grants = self._visible.get(owning_module)
        if grants is None:
            return False
        return any(_grant_names_row(grant, provider) for grant in grants)

    # -- Domain checks and visibility diagnostics --------------------------

    def _validate_owning_module(self, value: Any, token: Any) -> None:
        """Reject a non-class owner on the way in (MV-03, MV-18; PRD 06 §7.1 row 32).

        Without this the user sees "invisible from module <object at 0x…>"
        much later, from a call that did not mention the keyword.
        """
        if value is not None and not isinstance(value, type):
            raise DecoratorUsageError(
                f"owning_module must be a class, got {value!r}",
                detail={"token": _describe(token), "owning_module": repr(value)},
            )

    def _duplicate_error(self, token: Any) -> DuplicateBindingError:
        """Row 12: a second non-``multi`` binding for one token (PRD 05 §5.6.2)."""
        existing = self._providers.get(token)
        return DuplicateBindingError(
            f"{_describe(token)} is already registered",
            detail={
                "token": _describe(token),
                "existing": _describe(existing.cls) if existing is not None else "",
                "new": _describe(token),
            },
        )

    def _undeclared_module_error(self, token: Any, owning_module: type) -> MissingProviderError:
        """Row 6 of §5.5: the consumer never declared a domain (MV-17, MV-22)."""
        name = _module_name(owning_module)
        return MissingProviderError(
            f"module {name} has no declared visibility",
            detail={
                "token": _describe(token),
                "module": name,
                "reason": "undeclared_module",
                "hint": f"call set_visible({name}, ...) to declare what it may see",
            },
        )

    def _domain(self, owning_module: type | None, token: Any) -> type | None:
        """Check a consumer's domain before filtering it (MV-17, MV-22).

        Encapsulation is container-wide: once anything is declared, a module
        with no declaration raises instead of silently seeing every row.
        """
        if not self._encapsulation_on or owning_module is None:
            return owning_module
        if owning_module not in self._visible:
            raise self._undeclared_module_error(token, owning_module)
        return owning_module

    def _not_visible_error(
        self,
        token: Any,
        owning_module: type | None,
        *,
        element: Any = None,
    ) -> MissingProviderError:
        """Row 30: the token exists, the consumer's domain may not use it (MV-16).

        The message never says "not registered" — a caller who reads it must
        not go looking for a missing ``register`` call (PRD 06 §7.2 row 11).
        """
        module = _module_name(owning_module)
        detail: dict[str, Any] = {
            "token": _describe(token),
            "module": module,
            "reason": "not_visible",
        }
        if element is not None:
            detail["element"] = _describe(element)
        detail["hint"] = _grant_hint(token, owning_module, self._declaring_modules(token))
        if element is not None:
            message = (
                f"No provider for {_describe(element)} (requested as {_describe(token)}) "
                f"visible from module {module}"
            )
        else:
            message = f"No provider for {_describe(token)} visible from module {module}"
        return MissingProviderError(message, detail=detail)

    def _declaring_modules(self, token: Any) -> list[str]:
        """Rendered owners of the rows bound to ``token``, sorted."""
        return sorted(
            {
                _module_name(row.owning_module)
                for row in self._token_bindings.get(token, ())
                if row.owning_module is not None
            }
        )

    def _ambiguity_error(self, token: Any, rows: list[Provider]) -> ProtocolAmbiguityError:
        """Row 13/15: a clash *inside one domain*, naming who owns each row.

        ``count`` is the number of visible rows, not the number registered
        (PRD 06 §7.2), and ``modules`` is what makes the clash actionable.
        """
        return ProtocolAmbiguityError(
            f"Multiple bindings for {_describe(token)} ({len(rows)})",
            detail={
                "token": _describe(token),
                "count": len(rows),
                # One entry per clashing row, so ``len(modules) == count``
                # (PRD 06 §7.2 rows 13/14).
                "modules": sorted(_module_name(row.owning_module) for row in rows),
            },
        )

    def _bindings_for(self, token: Any, owning_module: type | None) -> list[Provider]:
        """Rows bound to ``token``, including the structural-Protocol fallback.

        The fallback is domain-scoped (MV-30): a scan only sees rows the
        consumer's domain may use, and the protocol class itself is never a
        match (MV-31).
        """
        rows = list(self._token_bindings.get(token, ()))
        if not rows and getattr(token, "_is_runtime_protocol", False):
            rows = _structural_protocol_providers(
                token,
                owning_module,
                self._providers,
                is_visible=self._is_visible if self._encapsulation_on else None,
            )
        return rows

    def explain(self, token: Any, *, owning_module: type | None = None) -> str:
        """A deterministic report of how ``token`` would resolve from a domain.

        Never raises and never compiles (MV-34): an unknown token, an invisible
        row, an undeclared module and even a non-class owner produce a report
        rather than an exception, so this is safe inside a ``except DIError``
        handler or a log line. The field labels are part of the contract
        (MV-36) and every row that binds the token appears in registration
        order, the chosen row first (MV-33, MV-35).
        """
        rows = self._bindings_for(token, owning_module)
        if owning_module is None:
            header = "global view" if self._encapsulation_on else "flat registry"
        elif owning_module in self._visible:
            header = f"{_module_name(owning_module)} (declared)"
        else:
            header = f"{_module_name(owning_module)} (undeclared)"
        lines = [f"token: {_describe(token)}", f"domain: {header}"]
        if not rows:
            lines.append("row: none")
            lines.append("status: unregistered")
            return "\n".join(lines)
        shown = [row for row in rows if self._is_visible(row, owning_module)]
        chosen = shown[0] if shown else None
        ordered = ([chosen] if chosen is not None else []) + [
            row for row in rows if row is not chosen
        ]
        for index, row in enumerate(ordered, start=1):
            visible = any(row is candidate for candidate in shown)
            line = f"row {index}: owner={_module_name(row.owning_module)} visible={'yes' if visible else 'no'}"
            if not visible:
                if owning_module is None:
                    line += f' remedy="resolve {_describe(row.cls)} from {_module_name(row.owning_module)}"'
                else:
                    line += f' remedy="grant {_describe(row.cls)} to {_module_name(owning_module)}"'
            lines.append(line)
        if (
            owning_module is not None
            and self._encapsulation_on
            and owning_module not in self._visible
        ):
            lines.append("status: undeclared")
        elif not shown:
            lines.append("status: not_visible")
        elif len(shown) > 1 and not all(row.multi for row in shown):
            lines.append("status: ambiguous")
        else:
            lines.append("status: ok")
        return "\n".join(lines)

    async def startup(self) -> None:
        """Compile, then construct every singleton.

        lauren_di has no framework scheduler, so this is where singleton
        ``@post_construct`` hooks fire. Dependencies come first because each
        instantiation resolves its own edges.
        """
        self.compile()
        for provider in list(self._registered.values()):
            if provider.scope is Scope.SINGLETON and provider.provider_kind != "existing":
                await self._instantiate(provider, None, {})

    async def shutdown(self) -> tuple[DestructError, ...]:
        """Tear every singleton down, newest first. Never raises.

        Failures are collected, not raised, so one bad ``@pre_destruct`` does
        not strand the remaining instances — the same containment rule the
        request-scope finaliser uses.
        """
        from ._scope import finalize_scope

        wrappers = [
            value
            for value in self._singletons.values()
            if isinstance(value, _GeneratorContextWrapper)
        ]
        cache = {
            key: (value.value if isinstance(value, _GeneratorContextWrapper) else value)
            for key, value in self._singletons.items()
        }
        errors = list(await finalize_scope(self, cache, origin="shutdown"))
        for wrapper in reversed(wrappers):
            try:
                await wrapper.aclose()
            except Exception as exc:
                _logger.exception("Error closing a generator provider during shutdown")
                errors.append(
                    DestructError(
                        f"generator teardown failed during shutdown: {exc}",
                        detail={"origin": "shutdown", "cause": str(exc)},
                    )
                )
        self._singletons.clear()
        self._singletons_initialized.clear()
        return tuple(errors)

    def scope(self, *, values: Mapping[Any, Any] | None = None) -> ResolutionScope:
        """Open an explicit resolution scope owning its own request cache.

        The returned :class:`ResolutionScope` is itself an async context manager
        of itself -- ``__aenter__`` returns it and ``__aexit__`` closes it -- so
        both spellings in PRD 01 §5.3.3 work::

            async with container.scope() as scope:  ...
            scope = container.scope()

        The class, not the ``AsyncContextManager[ResolutionScope]`` protocol, is
        the declared return type because the protocol would hide ``.values``,
        ``.errors`` and ``await scope.close()`` from the caller while telling
        them less than the truth about what comes back.
        """
        return ResolutionScope(self, values=values)

    def _alias_terminal(self, provider: Provider) -> Provider | None:
        """The provider a consumer edge through ``provider`` actually acquires.

        An ``existing`` alias is a redirect: no factory, no instance of its own
        and no scope of its own (D-27, PRD 04 §5.5 rule 2). The scope the
        narrowing rule must compare is therefore the **terminal** target's, not
        the alias row's placeholder.

        Returns ``None`` when the chain is broken or cyclic. The caller then
        keeps the placeholder scope, so :meth:`compile` reports the real defect
        with its own error and its own precedence -- ``MissingProviderError`` for
        a dangling link, ``CircularDependencyError`` for a loop -- rather than
        inventing a scope violation on the way there.
        """
        seen: set[Any] = set()
        current = provider
        while current.provider_kind == "existing":
            token = current.cls
            if token in seen:
                return None
            seen.add(token)
            target = self._providers.get(current.alias_target)
            if target is None:
                return None
            current = target
        return current

    # -- Compilation -------------------------------------------------------

    def compile(self) -> None:
        """Validate graph: detect cycles, missing deps, scope violations."""
        if self._compiled:
            return
        # A plan's own targets are validated before the graph is walked, so a
        # typo in a test plan fails before any instance exists (OV-15/OV-16).
        # ``optional=True`` is the documented escape hatch and applies nothing.
        if self._overrides is not None:
            for token, override in self._overrides.providers.items():
                if token in self._override_rows or override.optional:
                    continue
                raise OverrideError(
                    f"cannot override {override.kind} {_describe(token)}: target_missing",
                    detail={
                        "token": _describe(token),
                        "kind": override.kind,
                        "reason": "target_missing",
                        "hint": "register the token, or mark the override optional",
                    },
                )
        WHITE, GRAY, BLACK = 0, 1, 2
        color: dict[Any, int] = dict.fromkeys(self._providers, WHITE)
        stack: list[Any] = []

        def _check_scope(chosen: Provider, *, for_scope: Scope, dependent: str) -> None:
            """Enforce the scope-narrowing rule on a single edge.

            A wider-scoped consumer must never depend on a narrower-scoped
            provider — that would either keep a short-lived instance alive
            past its sensible lifetime (``SINGLETON`` consumers) or silently
            cache a per-resolution value (``REQUEST`` consumers of a
            ``TRANSIENT`` provider).

            :class:`~lauren_di.Scope` is an ``IntEnum`` whose values are
            explicitly ordered from narrowest (``TRANSIENT = 0``) to widest
            (``SINGLETON = 2``). That lets us replace the previous lookup
            table with a single comparison — a dependent outlives its
            dependency exactly when ``consumer_scope > dependency_scope``.
            """
            # An alias carries no scope of its own, so the edge is judged
            # against the terminal target (D-27, PRD 04 §5.5 rule 2). The chain
            # is walked here instead of reading the row's ``scope`` because
            # ``visit`` may not have rewritten that row yet: registration order
            # decides which provider is visited first, and the answer must not
            # depend on it. A broken or cyclic chain yields ``None`` and the
            # placeholder scope stands, so the real defect is reported below by
            # its own error.
            terminal = self._alias_terminal(chosen)
            dependency = chosen if terminal is None else terminal
            if for_scope > dependency.scope:
                through = (
                    "" if dependency is chosen else f" (through alias {_describe(chosen.cls)})"
                )
                detail = {
                    "dependent": dependent,
                    "dependent_scope": for_scope.label,
                    "dependency_scope": dependency.scope.label,
                    "dependency": _describe(dependency.cls),
                }
                if dependency is not chosen:
                    detail["alias"] = _describe(chosen.cls)
                raise DIScopeViolationError(
                    f"{for_scope.label.capitalize()} {dependent} depends on "
                    f"{dependency.scope.label}-scoped {_describe(dependency.cls)}{through}",
                    detail=detail,
                )

        def resolve_one(
            token: Any, *, for_scope: Scope, owning_module: type | None, dependent: str
        ) -> list[Provider]:
            """Resolve ``token`` to the list of dep-graph providers it expands to.

            Normal (scalar) tokens resolve to a single-element list. A
            ``list[T]`` token resolves to every multi-bound provider for
            ``T``; the caller treats each element as an independent edge
            so cycle detection and scope checks apply per-binding.

            This function raises the same typed errors as before —
            ``MissingProviderError``, ``ProtocolAmbiguityError``,
            ``DIScopeViolationError`` — so callers that were relying on
            the original exception semantics keep working.
            """
            element_type = _multi_binding_element_type(token)
            # Every edge is judged in the declaring row's domain (MV-25), and an
            # undeclared domain fails here rather than at resolve time (MV-17).
            owning_module = self._domain(owning_module, token)
            if element_type is not None:
                # ``list[T]``: every registered provider for ``T`` becomes an
                # edge. Non-multi registrations are a programming error in
                # this context — the user either forgot ``multi=True`` or
                # should depend on ``T`` directly.
                inner_bindings = self._token_bindings.get(element_type)
                if not inner_bindings:
                    raise MissingProviderError(
                        f"No provider for {_describe(element_type)} "
                        f"(requested as {_describe(token)}). "
                        "Register at least one provider with multi=True.",
                        detail={"token": _describe(token), "element": _describe(element_type)},
                    )
                visible = [b for b in inner_bindings if self._is_visible(b, owning_module)]
                if not visible:
                    owner_name = owning_module.__name__ if owning_module is not None else "<global>"
                    raise MissingProviderError(
                        f"No provider for {_describe(element_type)} "
                        f"(requested as {_describe(token)}) "
                        f"visible from module {owner_name}.",
                        detail={
                            "token": _describe(token),
                            "module": owner_name,
                        },
                    )
                if not all(b.multi for b in visible):
                    # A mix of multi / non-multi (or a lone non-multi) is a
                    # registration error. Surface it with the same exception
                    # class users already handle for "multiple bindings".
                    raise ProtocolAmbiguityError(
                        f"{_describe(token)} requires every provider for "
                        f"{_describe(element_type)} to be registered with "
                        "multi=True",
                        detail={
                            "token": _describe(token),
                            "element": _describe(element_type),
                            "count": len(visible),
                        },
                    )
                # Scope narrowing applies to each provider independently:
                # a singleton consumer of ``list[T]`` must not receive a
                # request-scoped member.
                for b in visible:
                    _check_scope(b, for_scope=for_scope, dependent=dependent)
                return list(visible)

            bindings = self._bindings_for(token, owning_module)
            if not bindings:
                raise MissingProviderError(
                    f"No provider for {_describe(token)}",
                    detail={"token": _describe(token)},
                )
            # Filter by visibility first — a caller can only see bindings
            # within its module's reach, and an invisible row must never
            # contribute a phantom ambiguity or a phantom cycle edge (MV-19).
            visible_bindings = [b for b in bindings if self._is_visible(b, owning_module)]
            if not visible_bindings:
                raise self._not_visible_error(token, owning_module)
            if len(visible_bindings) > 1 and not all(b.multi for b in visible_bindings):
                raise self._ambiguity_error(token, visible_bindings)
            chosen = visible_bindings[0]
            _check_scope(chosen, for_scope=for_scope, dependent=dependent)
            return [chosen]

        # Tokens the *host* resolves (an HTTP request, a job context, ...)
        # rather than this container. No provider will ever exist for them,
        # so they are skipped when validating edges. Configured per
        # container instead of importing the framework's ``Request``.
        framework_tokens = set(self._value_tokens)

        def visit(provider: Provider) -> None:
            tok = provider.cls
            c = color.get(tok, WHITE)
            if c == GRAY:
                cycle = [_describe(t) for t in [*stack[stack.index(tok) :], tok]]
                raise CircularDependencyError(
                    "Circular dependency: " + " -> ".join(cycle),
                    detail={"cycle": cycle},
                )
            if c == BLACK:
                return
            color[tok] = GRAY
            stack.append(tok)
            # ``existing`` aliases have no deps of their own — their
            # only edge is the alias_target. Validate that the target
            # exists and recurse so a chain of aliases is checked end
            # to end (and cycles are caught by the GRAY path above).
            if provider.provider_kind == "existing":
                target = provider.alias_target
                target_provider = self._providers.get(target)
                if target_provider is None:
                    raise MissingProviderError(
                        f"Alias {_describe(tok)} -> {_describe(target)} "
                        "points at an unknown token. Did you forget to "
                        "register the underlying provider?",
                        detail={
                            "alias": _describe(tok),
                            "target": _describe(target),
                        },
                    )
                visit(target_provider)
                # D-27: the row reports the terminal target's scope, so
                # ``get_provider(alias).scope`` -- and every other reader of the
                # row -- cannot claim SINGLETON for a request-scoped target. The
                # recursion above is post-order, so an intermediate alias has
                # already been stamped and ``target_provider.scope`` is terminal.
                provider.scope = target_provider.scope
                stack.pop()
                color[tok] = BLACK
                return
            # Map param name -> has_default for this provider's factory.
            defaults = _callable_default_map(provider)
            # Field-injected attributes are always required (they have no
            # parameter-level default concept); add them to the dep graph.
            dep_iter = list(provider.deps) + list(provider.field_deps)
            for name, dep_type in dep_iter:
                if dep_type in framework_tokens:
                    continue
                try:
                    dep_providers = resolve_one(
                        dep_type,
                        for_scope=provider.scope,
                        owning_module=provider.owning_module,
                        dependent=_describe(provider.cls),
                    )
                except ProtocolAmbiguityError as exc:
                    # Row 14: an ambiguity reached *through an edge* names the
                    # site that asked for it, which a bare token cannot.
                    detail = dict(exc.detail)
                    detail.setdefault("dependent", _describe(provider.cls))
                    detail.setdefault("param", name)
                    raise ProtocolAmbiguityError(exc.message, detail=detail) from exc
                except MissingProviderError as exc:
                    # Three escape hatches: parameter defaults, an
                    # explicit OptionalDep wrap on a use_factory inject
                    # entry, and (legacy) a field-side default. Only
                    # the first two are intentional, but they share the
                    # same handler.
                    if defaults.get(name, False):
                        continue
                    if name in provider.optional_dep_names:
                        continue
                    # Row 8: a missing edge names its dependant and the
                    # parameter that asked, which the token alone cannot.
                    detail = dict(exc.detail)
                    dependent = _describe(provider.cls)
                    detail.setdefault("dependent", dependent)
                    detail.setdefault("param", name)
                    detail.setdefault(
                        "hint",
                        "register the token, give the parameter a default, "
                        "or wrap it in OptionalDep",
                    )
                    message = exc.message
                    if "required by" not in message:
                        message = f"{message} (required by {dependent}.{name})"
                    raise MissingProviderError(message, detail=detail) from exc
                # ``list[T]`` deps expand to every bound provider; each edge
                # participates in cycle detection on its own terms.
                for dep_provider in dep_providers:
                    visit(dep_provider)
            stack.pop()
            color[tok] = BLACK

        for p in list(self._providers.values()):
            visit(p)

        # Encapsulation is container-wide, so an undeclared *owner* is a
        # configuration error rather than a row nobody can reach: its own
        # module can never resolve it, and every other module needs a grant
        # that the module table does not describe (MV-22).
        if self._encapsulation_on:
            for provider in self._registered.values():
                owner = provider.owning_module
                if owner is not None and owner not in self._visible:
                    raise self._undeclared_module_error(provider.cls, owner)

        # Ambiguity is a per-domain question (MV-24): two rows that no single
        # domain can see are legitimate, while a clash *inside* one domain is
        # refused. The global view is a domain too, so two global rows still
        # clash for every consumer (MV-01, MV-23).
        if self._encapsulation_on:
            domains: list[type | None] = [None, *self._visible]
            for token in self._token_bindings:
                for domain in domains:
                    rows = [b for b in self._token_bindings[token] if self._is_visible(b, domain)]
                    if len(rows) > 1 and not all(b.multi for b in rows):
                        raise self._ambiguity_error(token, rows)
        else:
            for token, bindings in self._token_bindings.items():
                if len(bindings) > 1 and not all(b.multi for b in bindings):
                    raise self._ambiguity_error(token, bindings)

        # Precompute the teardown index once so scope finalisation never
        # rebuilds a provider map per request.
        self.instance_index()
        self._compiled = True

    # -- Resolution --------------------------------------------------------

    def has_provider(self, token: Any, *, owning_module: type | None = None) -> bool:
        """Return True when ``token`` could be resolved by this container.

        Recognises two shapes:

        * **Scalar tokens** — returns True iff at least one provider for
          ``token`` is visible from ``owning_module``.
        * **``list[T]`` tokens** — returns True iff at least one provider
          for ``T`` is visible from ``owning_module`` (the resolver
          enforces the ``multi=True`` requirement; visibility alone is
          enough to say "yes, this route can go through DI").

        Answers the same question ``resolve`` would answer, without raising:
        ``True`` iff at least one provider for ``token`` is visible from
        ``owning_module`` (MV-15). While encapsulation is off that is every
        registered row; once on, an undeclared module sees nothing and answers
        ``False`` rather than failing closed with an exception, and the global
        view (``owning_module=None``) holds global rows only (MV-14, MV-23).
        ``False`` therefore always agrees with a ``resolve`` that would refuse.
        """
        effective = _multi_binding_element_type(token) or token
        bindings = self._token_bindings.get(effective)
        if not bindings:
            return False
        if not self._encapsulation_on:
            return True
        # ``has_provider`` answers, it never raises: an undeclared module sees
        # nothing rather than failing closed with an exception (MV-15).
        if owning_module is not None and owning_module not in self._visible:
            return False
        return any(self._is_visible(b, owning_module) for b in bindings)

    def get_provider(self, token: Any, *, owning_module: type | None = None) -> Provider:
        """The row a resolution of ``token`` from ``owning_module`` would use.

        Applies the same domain filter as :meth:`resolve` (MV-15), so a row this
        method refuses is one ``resolve`` would refuse too.
        """
        self._validate_owning_module(owning_module, token)
        owning_module = self._domain(owning_module, token)
        bindings = self._bindings_for(token, owning_module)
        if not bindings:
            raise MissingProviderError(
                f"No provider for {_describe(token)}", detail={"token": _describe(token)}
            )
        bindings = [b for b in bindings if self._is_visible(b, owning_module)]
        if not bindings:
            raise self._not_visible_error(token, owning_module)
        if len(bindings) > 1 and not all(b.multi for b in bindings):
            raise self._ambiguity_error(token, bindings)
        return bindings[0]

    def all_providers(self) -> list[Provider]:
        return list(self._providers.values())

    def registered_providers(self) -> tuple[Provider, ...]:
        """Return every provider in registration order.

        Includes each multi-bound sibling, which :meth:`all_providers` (keyed
        by token) collapses to the last one registered.
        """
        return tuple(self._registered.values())

    def instance_index(self) -> Mapping[Any, Provider]:
        """Map each instance-cache key to the provider that produced it.

        A request-scoped instance is cached under ``provider.cls`` — or under
        ``id(provider)`` for multi-bound providers, which share their token.
        The scope finaliser uses this map to find an instance's
        ``@pre_destruct`` hook. It is built by :meth:`compile` and rebuilt on
        first use after a late registration.
        """
        index: dict[Any, Provider] | None = self._instance_index
        if index is None:
            index = {}
            for provider in self._registered.values():
                if provider.provider_kind == "existing":
                    continue  # aliases redirect; they never own a cached instance
                index.setdefault(id(provider) if provider.multi else provider.cls, provider)
            self._instance_index = index
        return index

    # Overloads (PRD 01 §5.6). A *class* token is statically typed, so
    # ``await container.resolve(Config)`` is a ``Config`` rather than ``Any``.
    # A generic alias is typed too, because ``list[T]`` is itself a
    # ``type[...]`` expression, so a multi-binding keeps its element type.
    #
    # The fallback names the two *non-class* token kinds the surface documents
    # -- a string and a :class:`~lauren_di.Token` -- instead of ``Any``. ``Any``
    # matches a class token too, and mypy resolves an overload with the
    # assignment's declared type in view: with an ``Any`` fallback,
    # ``number: int = await container.resolve(Config)`` silently took the
    # untyped variant and passed, while pyright rejected the same line. Naming
    # the non-class kinds leaves a class token one candidate, so both checkers
    # report the conflict (§5.6). The implementation below keeps ``token: Any``:
    # the overloads are declaration-only, so the runtime still accepts any
    # hashable key, and a host using an exotic key adds a cast.
    @overload
    async def resolve(
        self,
        token: type[T],
        *,
        request_cache: dict[Any, Any] | None = None,
        values: Mapping[Any, Any] | None = None,
        owning_module: type | None = None,
    ) -> T: ...

    @overload
    async def resolve(
        self,
        token: str | Token,
        *,
        request_cache: dict[Any, Any] | None = None,
        values: Mapping[Any, Any] | None = None,
        owning_module: type | None = None,
    ) -> Any: ...

    async def resolve(
        self,
        token: Any,
        *,
        request_cache: dict[Any, Any] | None = None,
        values: Mapping[Any, Any] | None = None,
        owning_module: type | None = None,
    ) -> Any:
        """Resolve an instance of ``token``.

        ``request_cache`` stores request-scoped instances for the current
        request, keyed by the container's own provider key. ``values`` maps
        opaque, type-like tokens to objects the host supplies itself: a
        ``type``, a ``Token`` or a string all work as keys, and any
        :class:`~collections.abc.Mapping` is accepted (``value_tokens=`` on the
        constructor declares which tokens may arrive that way).
        ``owning_module`` names the consumer's domain: only
        rows that domain may use are considered (PRD 06 §5.5). While nothing is
        declared that means every row — the flat registry — and once anything
        is declared it means the module's own rows, global rows, and its
        grants; an undeclared module raises rather than widening (MV-22).

        The two ``@overload`` variants are the *static* surface (PRD 01 §5.6): a
        class token resolves to an instance of that class, a string or a
        :class:`~lauren_di.Token` to the untyped shape. Any other hashable key
        still resolves here — the overloads are declaration-only and this
        signature is ``token: Any`` — but a host that registers one spells the
        call as ``cast(Any, key)`` so both checkers agree with the runtime.
        """
        values = values or {}
        if token in values:
            return values[token]
        self._validate_owning_module(owning_module, token)
        owning_module = self._domain(owning_module, token)
        # ``list[T]`` tokens expand to every *visible* multi-binding for ``T``.
        # The inner resolution reuses the same request cache and host values so
        # request-scoped collaborators still see a single instance per request.
        element_type = _multi_binding_element_type(token)
        if element_type is not None:
            return await self._resolve_multi_list(
                token,
                element_type,
                request_cache=request_cache,
                values=values,
                owning_module=owning_module,
            )
        bindings = self._bindings_for(token, owning_module)
        if not bindings:
            raise MissingProviderError(
                f"No provider for {_describe(token)}", detail={"token": _describe(token)}
            )
        # Filter before ambiguity, scope narrowing or cycle following, so an
        # invisible row never contributes a phantom error (MV-19).
        visible_bindings = [b for b in bindings if self._is_visible(b, owning_module)]
        if not visible_bindings:
            raise self._not_visible_error(token, owning_module)
        # Return all multi-bindings as a list when requested explicitly:
        if all(b.multi for b in visible_bindings) and len(visible_bindings) > 1:
            results: list[Any] = []
            for b in visible_bindings:
                results.append(await self._instantiate(b, request_cache, values))
            return results
        if len(visible_bindings) > 1:
            raise self._ambiguity_error(token, visible_bindings)
        return await self._instantiate(visible_bindings[0], request_cache, values)

    async def _resolve_multi_list(
        self,
        token: Any,
        element_type: Any,
        *,
        request_cache: dict[Any, Any] | None,
        values: Mapping[Any, Any],
        owning_module: type | None,
    ) -> list[Any]:
        """Resolve ``list[T]`` to a list of every visible multi-bound instance.

        Every provider for ``element_type`` that is both visible to
        ``owning_module`` and marked ``multi=True`` produces one element
        of the returned list, in registration order. The result type is
        a real ``list`` (not a tuple or a generator) so handler code can
        iterate it multiple times.

        Error modes, in priority order:

        * No providers for ``element_type`` at all →
          :class:`MissingProviderError`.
        * No *visible* providers for ``element_type`` from
          ``owning_module`` → :class:`MissingProviderError`.
        * At least one matching provider was **not** registered with
          ``multi=True`` → :class:`ProtocolAmbiguityError`, since
          returning a single-element list from a lone non-multi binding
          would silently diverge from how ``resolve(T)`` behaves on the
          same registration.
        """
        bindings = self._token_bindings.get(element_type)
        if not bindings:
            raise MissingProviderError(
                f"No provider for {_describe(element_type)} (requested as {_describe(token)})",
                detail={"token": _describe(token), "element": _describe(element_type)},
            )
        visible = [b for b in bindings if self._is_visible(b, owning_module)]
        if not visible:
            # MV-28: a filtered-empty collection raises rather than degrading to
            # ``[]``, which would be indistinguishable from "multi=True, none".
            raise self._not_visible_error(token, owning_module, element=element_type)
        if not all(b.multi for b in visible):
            raise ProtocolAmbiguityError(
                f"{_describe(token)} requires every provider for "
                f"{_describe(element_type)} to be registered with multi=True",
                detail={
                    "token": _describe(token),
                    "element": _describe(element_type),
                    "count": len(visible),
                    "modules": sorted({_module_name(b.owning_module) for b in visible}),
                },
            )
        results: list[Any] = []
        for provider in visible:
            results.append(await self._instantiate(provider, request_cache, values))
        return results

    async def _instantiate(
        self,
        provider: Provider,
        request_cache: dict[Any, Any] | None,
        values: Mapping[Any, Any],
    ) -> Any:
        # Alias rows carry no factory — redirect to the underlying token
        # before doing any caching work. ``existing`` aliases inherit the
        # target's scope automatically because we delegate the resolve
        # back through :meth:`resolve`, which re-enters ``_instantiate``
        # on the real provider.
        if provider.provider_kind == "existing":
            # An alias redirects; it does not grant. The target is resolved in
            # the *alias row's* domain, never the target's owner's, so an alias
            # can never be a one-line bypass around visibility (MV-29).
            try:
                return await self.resolve(
                    provider.alias_target,
                    request_cache=request_cache,
                    values=values,
                    owning_module=provider.owning_module,
                )
            except MissingProviderError as exc:
                detail = dict(exc.detail)
                detail.setdefault("alias", _describe(provider.cls))
                detail.setdefault("module", _module_name(provider.owning_module))
                raise MissingProviderError(exc.message, detail=detail) from exc

        # Multi-binding providers share the same ``provide=`` token as their
        # ``cls`` field — using ``cls`` as the singleton cache key would
        # cause every sibling provider to return the first one's value.
        # Use ``id(provider)`` instead so each binding gets its own slot.
        _singleton_key: Any = id(provider) if provider.multi else provider.cls

        if provider.scope == Scope.SINGLETON:
            if _singleton_key in self._singletons:
                cached = self._singletons[_singleton_key]
                return cached.value if isinstance(cached, _GeneratorContextWrapper) else cached
        elif provider.scope == Scope.REQUEST:
            if request_cache is None:
                # No request, WebSocket connection or OperationScope is active:
                # strict mode refuses; lax mode (the 1.x default) falls back to
                # an untracked transient and warns once per provider.
                self._unscoped_request_resolution(provider)
            elif _singleton_key in request_cache:
                cached = request_cache[_singleton_key]
                return cached.value if isinstance(cached, _GeneratorContextWrapper) else cached

        # Build ``kwargs`` for the factory: function-provider parameters
        # and class-provider __init__ / __new__ parameters are resolved
        # identically.
        defaults = _callable_default_map(provider)
        kwargs: dict[str, Any] = {}
        for name, dep_type in provider.deps:
            try:
                kwargs[name] = await self.resolve(
                    dep_type,
                    request_cache=request_cache,
                    values=values,
                    owning_module=provider.owning_module,
                )
            except MissingProviderError:
                if dep_type in values:
                    kwargs[name] = values[dep_type]
                elif name in provider.optional_dep_names:
                    # ``OptionalDep("...")`` from a use_factory inject
                    # list — missing provider lowers to ``None`` so the
                    # factory's positional argument receives a sentinel
                    # instead of an exception.
                    kwargs[name] = None
                elif defaults.get(name, False):
                    # Optional param — let the factory's default apply.
                    continue
                else:
                    raise

        # Class providers: resolve class-body annotated deps FIRST, then
        # construct through ``__new__`` / ``__init__`` with the same
        # kwargs. Setting the attributes *before* ``__init__`` runs is
        # deliberate — it mirrors how ``dataclasses`` populate fields
        # and lets user-written initializers read the injected values.
        if not provider.is_function_provider:
            field_values: dict[str, Any] = {}
            for attr, dep_type in provider.field_deps:
                field_values[attr] = await self.resolve(
                    dep_type,
                    request_cache=request_cache,
                    values=values,
                    owning_module=provider.owning_module,
                )
            # For ``use_class`` the token (``cls`` field) and the
            # *concrete class to instantiate* (``factory`` field) are
            # different objects — the whole point of useClass is that
            # the consumer-facing token may be a string while the
            # implementation is a real Python class. Pick the right
            # one to pass to the constructor.
            target = provider.factory if provider.provider_kind == "class" else provider.cls
            instance = await _construct_class(target, kwargs, field_values)  # type: ignore[arg-type]
        else:
            # Function provider: call the factory; the return value IS
            # the dependency. ``await`` if it returns a coroutine.
            factory = provider.factory
            if provider.positional_arg_names:
                # ``use_factory`` records: pass deps positionally so the
                # factory's parameter names stay irrelevant to the
                # contract (lambdas, lambdas with renamed params, and
                # named functions all work identically).
                positional = [kwargs[n] for n in provider.positional_arg_names]
                result = factory(*positional)  # type: ignore[misc]
            else:
                result = factory(**kwargs)  # type: ignore[misc]
            if provider.is_generator_provider:
                # Generator provider: advance to the yield point (setup),
                # wrap so teardown runs when the scope ends via aclose().
                if inspect.isasyncgen(result):
                    yielded = await result.__anext__()
                    instance = _GeneratorContextWrapper(yielded, result, is_async=True)
                else:
                    yielded = next(result)
                    instance = _GeneratorContextWrapper(yielded, result, is_async=False)
            else:
                if inspect.isawaitable(result):
                    result = await result
                instance = result

        if provider.scope == Scope.SINGLETON:
            self._singletons[_singleton_key] = instance
        elif provider.scope == Scope.REQUEST and request_cache is not None:
            request_cache[_singleton_key] = instance

        # Run @post_construct for every scope. lauren_di has no
        # LifecycleScheduler to defer singletons to, and a singleton is
        # constructed exactly once — so firing here fires exactly once,
        # whichever scope the provider declares.
        hooks = provider.post_construct_chain
        if hooks:
            resolved_instance = (
                instance.value if isinstance(instance, _GeneratorContextWrapper) else instance
            )
            for hook in hooks:
                await _invoke_hook(resolved_instance, hook)

        return instance.value if isinstance(instance, _GeneratorContextWrapper) else instance

    def _unscoped_request_resolution(self, provider: Provider) -> None:
        """Handle a ``Scope.REQUEST`` provider resolved without a request cache."""
        token = provider.cls
        name = _describe(token)
        if self._strict_request_scope:
            raise DIScopeViolationError(
                f"{name} is request-scoped but was resolved outside a request or app.scope(); "
                f"resolve it inside `async with app.scope(...) as scope:` (scope.resolve({name}))",
                detail={"token": name, "reason": "no_active_scope"},
            )
        key = id(provider) if provider.multi else token
        if key in self._unscoped_warned:
            return
        self._unscoped_warned.add(key)
        _logger.warning(
            "%s is request-scoped but was resolved outside a request or `app.scope()`; it will never be "
            "torn down. Use `app.scope()` or enable `strict_request_scope=True`.",
            name,
        )

    # -- Lifecycle access --------------------------------------------------

    def singletons(self) -> dict[type, Any]:
        return dict(self._singletons)

    def set_singleton(self, cls: type, instance: Any) -> None:
        self._singletons[cls] = instance
        self._singletons_initialized.add(cls)

    def mark_singleton_initialized(self, cls: type) -> None:
        """Record that a singleton's ``@post_construct`` has already run.

        Called by :class:`LifecycleScheduler` after it invokes the hook in
        topological order during startup so that subsequent ``resolve()``
        calls don't re-fire the hook.
        """
        self._singletons_initialized.add(cls)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_injectable_meta(target: Any) -> InjectableMeta | None:
    """Return the ``@injectable`` metadata, or ``None`` if absent.

    Classes follow the usual no-inheritance rule: reading the marker off
    a base class without the subclass re-decorating raises
    :class:`MetadataInheritanceError`. Functions simply look at the
    attribute; there's no inheritance surface for plain functions.
    """
    if not isinstance(target, type):
        return getattr(target, INJECTABLE_META, None)
    own_meta = target.__dict__.get(INJECTABLE_META)
    if own_meta is not None:
        return cast(InjectableMeta, own_meta)
    # Attribute not in own __dict__ but reachable via MRO -> inherited.
    for base in target.__mro__[1:]:
        if INJECTABLE_META in base.__dict__:
            raise MetadataInheritanceError(
                f"{target.__name__} inherited injectable metadata from "
                f"{base.__name__}; re-decorate explicitly.",
                detail={"target": target.__name__, "base": base.__name__},
            )
    return None


def _find_lifecycle_hooks(
    cls: type, marker: str, *, subclass_first: bool = False
) -> tuple[Callable[..., Any], ...]:
    """Every method of ``cls`` carrying ``marker``, in run order.

    Method decorators propagate through the MRO, and an attribute's *first*
    MRO definition wins, so an override without the decorator drops the
    base hook (the route rule). Hooks are ordered by the class that defines
    them — base classes first, or subclasses first when ``subclass_first``
    (teardown) — and by definition order within a class. One function
    reachable under two names runs once.
    """
    owners: dict[str, type] = {}
    for klass in cls.__mro__:
        for attr_name in klass.__dict__:
            owners.setdefault(attr_name, klass)
    classes = [k for k in cls.__mro__ if k is not object]
    if not subclass_first:
        classes.reverse()
    hooks: list[Callable[..., Any]] = []
    seen: set[int] = set()
    for klass in classes:
        for attr_name in klass.__dict__:
            if owners[attr_name] is not klass or (
                attr_name.startswith("__") and attr_name.endswith("__")
            ):
                continue  # dunders are never hooks (and may be deprecated class attributes)
            try:
                attr = getattr(cls, attr_name)
            except AttributeError:
                continue
            if not (callable(attr) and getattr(attr, marker, False)):
                continue
            key = id(getattr(attr, "__func__", attr))
            if key not in seen:
                seen.add(key)
                hooks.append(attr)
    return tuple(hooks)


def instance_lifecycle_hooks(
    cls: type,
) -> tuple[tuple[Callable[..., Any], ...], tuple[Callable[..., Any], ...]]:
    """Return ``(startup_hooks, shutdown_hooks)`` for a pre-built instance of ``cls``.

    Every ``@post_construct`` / ``@pre_destruct`` hook wins, in run order;
    otherwise ``startup()`` / ``aclose()`` methods (the store convention)
    are used.
    """
    post = _find_lifecycle_hooks(cls, "__lauren_di_post_construct__")
    pre = _find_lifecycle_hooks(cls, "__lauren_di_pre_destruct__", subclass_first=True)
    if not post:
        startup = _named_hook(cls, "startup")
        post = (startup,) if startup is not None else ()
    if not pre:
        aclose = _named_hook(cls, "aclose")
        pre = (aclose,) if aclose is not None else ()
    return post, pre


def _named_hook(cls: type, name: str) -> Callable[..., Any] | None:
    attr = getattr(cls, name, None)
    if attr is None or not callable(attr) or getattr(attr, "__name__", None) != name:
        return None
    return attr  # type: ignore[no-any-return]


async def _invoke_hook(instance: Any, hook: Callable[..., Any]) -> None:
    """Invoke a lifecycle hook bound to ``instance``, awaiting it if needed."""
    bound = getattr(instance, hook.__name__)
    result = bound()
    if inspect.isawaitable(result):
        await result


def _has_unresolved(hints: dict[str, Any]) -> bool:
    """Return True when any resolved annotation still contains a
    :class:`typing.ForwardRef` — i.e. the lenient resolver had to fall
    back for at least one name. The caller then retries with a wider
    namespace (typically the calling-frame stack) before giving up.

    Only the outermost layer is inspected, which covers the common cases
    (a bare name like ``"Config"`` or a one-level generic like
    ``"list[Config]"``). Deeper sweeps are unnecessary because any
    successful evaluation at the top level means the needed name was
    visible in the namespace.
    """
    import typing as _typing

    def _walk(ann: Any) -> bool:
        if isinstance(ann, _typing.ForwardRef):
            return True
        return any(_walk(arg) for arg in _typing.get_args(ann))

    return any(_walk(v) for v in hints.values())


def _safe_type_hints(fn: Callable[..., Any], *, include_extras: bool = True) -> dict[str, Any]:
    """Resolve a callable's annotations, tolerating unresolved refs.

    Delegates to :func:`lauren_di._typing.resolve_type_hints`, which first
    tries the stdlib ``typing.get_type_hints`` (fast path) and then
    falls back to a lenient walker that returns a
    :class:`typing.ForwardRef` for any name it cannot resolve. As a last
    resort we scan the calling frame stack for local names so that a
    ``Depends[factory]`` pointing at a function-scoped type still
    resolves when the test or plugin defined it inline.

    ``include_extras`` defaults to ``True`` so callers see the full
    ``Annotated[T, ...]`` wrapper and can react to ``Inject``,
    :class:`Depends`, and other markers. Callers that just want the
    bare type pass ``include_extras=False``; the helper still strips
    the metadata before returning.
    """
    try:
        hints = resolve_type_hints(fn, include_extras=include_extras)
        if not _has_unresolved(hints):
            return hints
    except Exception:
        hints = {}

    import sys

    # ``sys._getframe`` is private CPython API and the only way to read the
    # caller's locals; this package supports CPython only (PRD 01 §5.1).
    frame = sys._getframe(1)  # pyright: ignore[reportPrivateUsage]
    merged_locals: dict[str, Any] = {}
    while frame is not None:
        for k, v in frame.f_locals.items():
            merged_locals.setdefault(k, v)
        frame = frame.f_back  # type: ignore[assignment]
    try:
        return resolve_type_hints(
            fn,
            globalns=dict(getattr(fn, "__globals__", {}) or {}),
            localns=merged_locals,
            include_extras=include_extras,
        )
    except Exception:
        return hints or {}


def _safe_class_hints(cls: type, *, include_extras: bool = False) -> dict[str, Any]:
    """Resolve class-body annotations, tolerating unresolved strings.

    Tries progressively broader namespaces so PEP 563 / ``from __future__
    import annotations`` stringified references to function-scoped names
    (the common shape when ``Depends[factory]`` is used inside a test
    method or a nested class) still resolve. The order is:

    1. Plain ``get_type_hints(cls)`` — the fast path that succeeds
       whenever the annotation references module-level names.
    2. ``get_type_hints`` with the caller's frame stack merged into a
       local namespace dict. Class-scoped and method-scoped names get
       picked up this way; real-dict ``globalns`` is required because
       the stdlib's ``_eval_type`` rejects mapping-likes.
    3. Raw ``__annotations__`` — unresolved string entries simply don't
       participate in DI, which is the safe fallback.

    ``include_extras`` preserves ``Annotated[...]`` wrappers so callers
    that care about attached metadata (``Depends``, ``Path``, ...) can
    see them. It defaults to ``False`` for backwards compatibility with
    the callable-deps path, which wants the bare inner type.
    """
    try:
        hints = resolve_type_hints(cls, include_extras=include_extras)
        if not _has_unresolved(hints):
            return hints
    except Exception:
        hints = {}

    import sys as _sys

    # ``sys._getframe`` is private CPython API and the only way to read the
    # caller's locals; this package supports CPython only (PRD 01 §5.1).
    frame = _sys._getframe(1)  # pyright: ignore[reportPrivateUsage]
    merged_locals: dict[str, Any] = {}
    while frame is not None:
        for k, v in frame.f_locals.items():
            merged_locals.setdefault(k, v)
        frame = frame.f_back  # type: ignore[assignment]

    # Module globals for the class — look the module up on sys.modules
    # rather than trusting ``cls.__globals__`` (plain classes don't have
    # that attribute; only functions do).
    module = _sys.modules.get(getattr(cls, "__module__", ""))
    globalns: dict[str, Any] = dict(getattr(module, "__dict__", {})) if module is not None else {}
    try:
        return resolve_type_hints(
            cls,
            globalns=globalns,
            localns=merged_locals,
            include_extras=include_extras,
        )
    except Exception:
        return hints or dict(getattr(cls, "__annotations__", {}) or {})


def _describe(target: Any) -> str:
    """Pretty-name a class or function for error messages."""
    name = getattr(target, "__name__", None)
    if name is not None:
        return str(name)
    return str(target)


def _module_name(module: type | None) -> str:
    """Render a domain for a message: the module's name, or ``<global>``.

    Class identity is what keys a domain (MV-11); this function is only ever
    used for display, and never to compare two domains.
    """
    if module is None:
        return "<global>"
    return str(getattr(module, "__name__", None) or repr(module))


def _grant_address(grant: Grant) -> tuple[Any, type] | None:
    """Return ``(token, owner)`` when ``grant`` addresses one row (MV-08).

    A grant is either a token — hashable, usually a class, a string or a
    :class:`~lauren_di.Token` — or a ``(token, owner)`` pair whose second
    element is the declaring module class. Nothing else is a row address.
    """
    if isinstance(grant, tuple):
        pair = cast("tuple[Any, Any]", grant)
        if len(pair) == 2 and isinstance(pair[1], type):
            return pair[0], pair[1]
    return None


def _names_row(token: Any, provider: Provider) -> bool:
    """Whether ``token`` names ``provider``'s row.

    A row answers to the token it was registered under *and* to every token it
    declares through ``provides=[...]`` — the reason a bare-token grant reaches
    ``@injectable(provides=[Sender])`` rows (MV-07, PRD 06 §1.4).
    """
    if token is provider.cls or token == provider.cls:
        return True
    return any(token is provided or token == provided for provided in provider.provides)


def _grant_names_row(grant: Grant, provider: Provider) -> bool:
    """Whether ``grant`` covers ``provider``: a bare token or one row address."""
    address = _grant_address(grant)
    if address is None:
        return _names_row(grant, provider)
    token, owner = address
    return owner is provider.owning_module and _names_row(token, provider)


def _grant_hint(token: Any, owning_module: type | None, owners: list[str]) -> str:
    """The ``hint`` of error row 30: how to make ``token`` visible.

    It names the declaring module when there is one (MV-16), because the next
    question a caller asks is "who does have it?".
    """
    name = _module_name(owning_module)
    if owners:
        return (
            f"grant {_describe(token)} to {name} (set_visible or grant_visible), "
            f"or resolve it from {' or '.join(owners)}"
        )
    return f"grant {_describe(token)} to {name} (set_visible or grant_visible)"


def _inspect_class_deps(cls: type) -> list[tuple[str, Any]]:
    """Collect ``(param_name, token)`` pairs for a class's DI deps.

    Uses :func:`inspect.signature` on the **class itself** — the only
    reliable way to discover what arguments a class accepts. The
    stdlib walks several alternative sources, in order:

    1. An explicit ``__signature__`` attribute on the class (Pydantic
       v2, attrs, and many DSLs publish their constructor surface
       this way).
    2. The metaclass's ``__call__`` if it is not ``type.__call__``
       (factory metaclasses, plug-in registries).
    3. ``__init__`` and/or ``__new__`` (the usual case).
    4. ``object.__init__`` — yielding an empty signature.

    Inspecting ``cls.__init__`` and ``cls.__new__`` directly — as the
    legacy ``__init__``/``__new__`` merge did — is wrong whenever
    Python's own callable-resolution lands somewhere else: a class
    with a metaclass that bypasses ``__init__`` would be told it
    needs the never-invoked initializer's parameters; a Pydantic
    model with ``__init__(*args, **kwargs)`` would expose no deps at
    all instead of its declared field surface.

    The behaviour is the same as :func:`_inspect_callable_deps` for
    the inner walking of parameters: ``self`` / ``cls`` skipped,
    ``*args``/``**kwargs`` skipped, missing-annotation-without-default
    rejected, ``Annotated[T, Inject("X")]`` honoured, plain
    ``Annotated[T, ...]`` unwrapped to ``T``.

    Forward references (PEP 563 / ``from __future__ import annotations``
    stringified hints) are resolved via :func:`_resolve_class_signature_hints`
    so a class defined in a function-local scope, or a model whose
    annotations were captured as strings, still produces real type
    tokens.
    """
    try:
        sig = inspect.signature(cls)
    except (TypeError, ValueError):
        # Some C-implemented classes refuse signature introspection
        # entirely (PyType_Type, raw bound methods on slot descriptors).
        # Treat them as zero-arg constructors — the user can still
        # write a function provider if they need to feed arguments.
        return []
    sig = _resolve_class_signature_hints(cls, sig)
    # The signature returned by ``inspect.signature(cls)`` does NOT
    # include ``self`` (the stdlib already drops it) but DOES include
    # ``cls`` for ``__new__``-derived signatures. We still skip both
    # defensively in case a custom ``__signature__`` carries them.
    return _params_to_deps(
        sig,
        callable_name=cls.__name__,
        owner_label=cls.__name__,
        skip_first_self_or_cls=False,
    )


def _resolve_class_signature_hints(cls: type, sig: inspect.Signature) -> inspect.Signature:
    """Replace string annotations on ``sig`` with resolved types.

    With ``from __future__ import annotations`` (or any other
    PEP-563-style deferral), :func:`inspect.signature` returns a
    :class:`Signature` whose ``annotation`` fields are still strings.
    The container needs real types to look up providers, so we walk
    every parameter and re-resolve those strings against the most
    informative namespace we can build:

    * the underlying initializer's globals (so module-level types
      always resolve);
    * the calling stack's locals (so class definitions inside a test
      method or factory function still resolve, mirroring what the
      legacy ``_safe_type_hints`` path did);
    * a fallback to the original string annotation if all else fails
      — the caller will then surface a clean ``MissingProviderError``
      pointing at the unresolved name.
    """
    import typing as _typing

    # Fast path: if no parameter has a string-or-ForwardRef annotation,
    # there's nothing to resolve. ``typing.ForwardRef`` shows up when a
    # class machinery (NamedTuple, TypedDict, etc.) captures annotations
    # at class-creation time under ``from __future__ import annotations``.
    def _needs_resolving(ann: Any) -> bool:
        return isinstance(ann, (str, _typing.ForwardRef))

    if not any(_needs_resolving(p.annotation) for p in sig.parameters.values()):
        return sig

    # Build the resolution namespace. Prefer the class's own module
    # globals; layer the most relevant initializer's globals on top so
    # type-only imports done in that file are visible.
    globalns: dict[str, Any] = {}
    init_fn = cls.__dict__.get("__init__")
    new_fn = cls.__dict__.get("__new__")
    for fn in (init_fn, new_fn):
        if fn is not None:
            globalns.update(getattr(fn, "__globals__", {}) or {})
    if not globalns:
        # No user-defined initializer; pull the class module's globals.
        import sys as _sys

        mod = _sys.modules.get(cls.__module__)
        if mod is not None:
            globalns.update(vars(mod))

    # Walk the calling stack so locally-defined types (class declared
    # inside a function) still resolve. Mirrors the fallback in the
    # legacy ``_safe_type_hints`` path so behaviour stays consistent.
    import sys as _sys

    localns: dict[str, Any] = {}
    # ``sys._getframe`` is private CPython API and the only way to read the
    # caller's locals; this package supports CPython only (PRD 01 §5.1).
    frame = _sys._getframe(1)  # pyright: ignore[reportPrivateUsage]
    while frame is not None:
        for k, v in frame.f_locals.items():
            localns.setdefault(k, v)
        frame = frame.f_back  # type: ignore[assignment]

    # Stringified annotations under PEP 563 may carry an extra layer
    # of quoting when the source already wrote the annotation as a
    # literal string (``foo: "UpstreamService"`` becomes the Python
    # string ``"'UpstreamService'"`` after ``__future__`` defers it).
    # Plain :func:`eval` peels both layers cleanly and avoids
    # depending on the rapidly-evolving private API of
    # :class:`typing.ForwardRef` (Python 3.12 added ``type_params``;
    # Python 3.13 added ``recursive_guard`` as keyword-only). The
    # call is restricted to the dependency-resolution namespaces we
    # built above, so it cannot reach arbitrary expressions — the
    # same trust boundary applied by :func:`typing.get_type_hints`.

    def _resolve_string_annotation(ann: str) -> Any:
        try:
            resolved = eval(ann, globalns, localns)
        except Exception:
            return ann
        # If the resolved value is itself a string the source had
        # nested quoting; peel one more layer.
        if isinstance(resolved, str) and resolved != ann:
            return _resolve_string_annotation(resolved)
        return resolved

    new_params: list[inspect.Parameter] = []
    for param in sig.parameters.values():
        ann = param.annotation
        if isinstance(ann, str):
            param = param.replace(annotation=_resolve_string_annotation(ann))
        elif isinstance(ann, _typing.ForwardRef):
            # NamedTuple / TypedDict-style — the class machinery
            # already wrapped the string in a ForwardRef. Use the same
            # eval-based resolution by extracting the inner string.
            inner = getattr(ann, "__forward_arg__", None)
            if isinstance(inner, str):
                param = param.replace(annotation=_resolve_string_annotation(inner))
        new_params.append(param)
    return sig.replace(parameters=new_params)


def _params_to_deps(
    sig: inspect.Signature,
    *,
    callable_name: str,
    owner_label: str,
    skip_first_self_or_cls: bool,
) -> list[tuple[str, Any]]:
    """Walk a :class:`inspect.Signature` and produce DI dep pairs.

    Shared backend for :func:`_inspect_class_deps` (which already
    receives the class-level signature with ``self``/``cls`` removed)
    and :func:`_inspect_callable_deps` (which receives a raw
    ``__init__`` / ``__new__`` / function and should drop the leading
    receiver). Keeping the parameter walk in one place means the
    Inject-vs-Annotated handling and the missing-annotation rule are
    not duplicated across two paths.
    """
    deps: list[tuple[str, Any]] = []
    items = list(sig.parameters.items())
    if skip_first_self_or_cls and items and items[0][0] in ("self", "cls"):
        items = items[1:]
    for name, param in items:
        if name in ("self", "cls"):
            continue
        if param.kind in (
            inspect.Parameter.VAR_POSITIONAL,
            inspect.Parameter.VAR_KEYWORD,
        ):
            continue
        ann = param.annotation
        has_default = param.default is not inspect.Parameter.empty
        if ann is inspect.Parameter.empty:
            if not has_default:
                raise UnresolvableParameterError(
                    f"{owner_label} parameter {name!r} missing annotation",
                    detail={"target": owner_label, "param": name},
                )
            continue
        token = _extract_inject_token(ann)
        if token is None:
            token = _unwrap_annotated(ann)
        deps.append((name, token))
    return deps


def _inspect_callable_deps(
    fn: Callable[..., Any],
    *,
    is_class_init: bool,
    owning_class: type | None = None,
) -> list[tuple[str, Any]]:
    """Collect ``(param_name, token)`` pairs for a callable's DI deps.

    Shared by class ``__init__`` / ``__new__`` inspection and function-
    provider inspection. Rules:

    * ``self`` and ``cls`` are skipped.
    * ``*args`` / ``**kwargs`` are skipped.
    * Parameters without an annotation are rejected unless they carry a
      default; when they carry a default they are simply omitted from
      the DI plan.
    * Parameters annotated with ``Annotated[T, Inject("X")]`` resolve
      against ``"X"`` instead of ``T``. The annotation type is still
      kept on the parameter for static analysis but plays no role in
      runtime resolution. This is how non-class tokens (strings,
      :class:`Token` instances) reach into a class ``__init__``.
    * Plain ``Annotated[T, ...]`` whose metadata does NOT contain an
      :class:`_InjectMarker` is unwrapped to ``T`` so the existing
      class-token resolution path keeps working.
    """
    try:
        sig = inspect.signature(fn)
    except (TypeError, ValueError):
        return []
    hints = _safe_type_hints(fn)
    # Replace each parameter's annotation with the resolved type-hint
    # so forward refs work, then dispatch to the shared walker.
    new_params = [
        param.replace(annotation=hints.get(name, param.annotation))
        for name, param in sig.parameters.items()
    ]
    sig = sig.replace(parameters=new_params)
    target_name = (
        owning_class.__name__ if owning_class is not None else getattr(fn, "__name__", "?")
    )
    return _params_to_deps(
        sig,
        callable_name=getattr(fn, "__name__", "<callable>"),
        owner_label=target_name,
        skip_first_self_or_cls=False,
    )


def _extract_inject_token(ann: Any) -> Any | None:
    """Return the token an ``Annotated[T, Inject(...)]`` carries, else ``None``.

    Walks the metadata tuple looking for the first :class:`_InjectMarker`.
    Multiple markers on the same parameter is a programming error; we
    take the first one and trust the user to spot the duplicate —
    raising a startup-time error here would force users to special-case
    re-decorations they have a legitimate reason to write.
    """

    metadata = getattr(ann, "__metadata__", None)
    if not metadata:
        return None
    for entry in metadata:
        if isinstance(entry, _InjectMarker):
            return entry.token
    return None


def _collect_field_deps(
    cls: type,
    *,
    field_predicate: Callable[[str, Any], bool] | None = None,
) -> list[tuple[str, type]]:
    """Collect class-body-annotated DI fields.

    Only annotations that **clearly name an injectable collaborator**
    are harvested as DI fields. This deliberately narrow rule keeps
    ``lauren_di`` compatible with ``@dataclass`` and attrs-style classes out
    of the box: plain-data fields like ``name: str``,
    ``count: int = 0`` or ``tags: list[str] = field(default_factory=list)``
    are NOT mistaken for DI dependencies, so the container doesn't try
    to resolve a ``str`` / ``int`` / ``list`` provider it was never
    asked to create.

    A class-body annotation is treated as a DI field when **all** of
    the following hold:

    * The attribute name lives in the class's own ``__annotations__``
      dict (inherited annotations stay on their owner class).
    * The class body does NOT set a value for the attribute — neither a
      literal (``x: int = 5``) nor a dataclass descriptor
      (``x: int = field(default=...)``).
    * The dataclass machinery does NOT list the attribute in
      ``__dataclass_fields__`` (any field registered there has either
      a default, a default-factory, or is required by ``__init__`` and
      should be set by the caller rather than the container).
    * The resolved type is a concrete class that is either decorated
      with ``@injectable`` or a runtime-checkable ``Protocol``.

    Everything else is treated as plain data and left alone. Users who
    want true DI-injected class fields can still opt in: annotate the
    field with a type that carries an ``@injectable`` decorator (or is
    itself a ``Protocol`` bound by a provider) and omit any class-body
    default.
    """
    # Python 3.14+ (PEP 649/annotationlib) no longer stores ``__annotations__``
    # directly in ``cls.__dict__``; the key is absent and the annotations are
    # produced on demand via ``__annotate_func__``.  ``inspect.get_annotations``
    # handles this transparently on all supported Python versions (3.10+).
    own_annotations = inspect.get_annotations(cls, eval_str=False) or {}
    if not own_annotations:
        return []
    # Resolve hints with include_extras=True so ``Annotated[T, Depends]``
    # survives the ``get_type_hints`` pass. The DI collector needs the
    # metadata (``Depends``, ``Path``, ...) to decide whether a field
    # with a non-class annotation (e.g. ``Depends[function_factory]``)
    # should still be registered.
    hints = _safe_class_hints(cls, include_extras=True)
    # Dataclasses register every field in ``__dataclass_fields__`` —
    # we use that as a second defence against treating plain-data
    # fields (even those without an in-body default) as DI fields.
    dataclass_fields: dict[str, Any] = getattr(cls, "__dataclass_fields__", {}) or {}
    deps: list[tuple[str, type]] = []
    for name in own_annotations:
        if name in cls.__dict__:
            # Class-level default present → not a DI field.
            continue
        if name in dataclass_fields:
            # Dataclass-declared field → initialised by the generated
            # ``__init__`` (or a default_factory); never DI-injected.
            continue
        ann = hints.get(name, own_annotations[name])
        # Stringified annotations we couldn't resolve stay as strings;
        # skip them so the class still loads (the user will see the
        # attribute absent and get a regular AttributeError if they try
        # to read it, which is the right signal).
        if isinstance(ann, str):
            continue
        # ``Annotated[T, Inject("TOKEN")]`` overrides the default
        # type-hint-as-token convention. The injected token may be a
        # string or a Token instance, neither of which would pass
        # ``_looks_injectable`` — so we short-circuit BEFORE that gate.
        inject_token = _extract_inject_token(ann)
        if inject_token is not None:
            deps.append((name, inject_token))
            continue
        # The embedder's pluggable gate wins over the structural rule: it can
        # opt a field in that lauren_di would reject (a host marker such as
        # ``Path[int]``, a config object, ...). See D-33.
        if field_predicate is not None and field_predicate(name, ann):
            deps.append((name, _unwrap_annotated(ann)))
            continue
        if not _looks_injectable(ann):
            # The annotation names something the container obviously
            # cannot produce (``str``, ``int``, ``list[str]``, ...). We
            # leave the attribute alone — the user's ``__init__`` (or a
            # future assignment) is responsible for populating it.
            continue
        # Unwrap ``Annotated[T, Depends]`` / ``Annotated[T, Marker]`` so
        # the downstream resolver sees the bare token it was registered
        # under (the function factory, the injectable class, etc.)
        # rather than the typing wrapper.
        deps.append((name, _unwrap_annotated(ann)))
    return deps


def _unwrap_annotated(ann: Any) -> Any:
    """Strip a top-level ``Annotated[T, ...]`` wrapper, returning ``T``.

    Leaves non-annotated types untouched. Used by the field-deps
    collector so the DI container's token lookup sees the registered
    class / function rather than the typing wrapper.
    """
    import typing as _typing

    if _typing.get_origin(ann) is None:
        return ann
    # Annotated[...] always has a non-empty ``__metadata__`` tuple; any
    # other typing generic (``list[int]``, ``dict[str, X]``) reaches
    # here with an empty metadata and we should leave it alone.
    if getattr(ann, "__metadata__", None):
        args = _typing.get_args(ann)
        if args:
            return args[0]
    return ann


# Primitive / stdlib types the container will never resolve as DI
# dependencies. Kept as a frozenset of tokens so the membership check
# is a single pointer comparison.
_NON_INJECTABLE_PRIMITIVES: frozenset[type] = frozenset(
    {
        str,
        bytes,
        bytearray,
        int,
        float,
        complex,
        bool,
        type(None),
        list,
        tuple,
        set,
        frozenset,
        dict,
    }
)


def _structural_protocol_providers(
    protocol: Any,
    owning_module: type | None,
    providers: dict[Any, Provider],
    is_visible: Callable[[Provider, type | None], bool] | None = None,
) -> list[Provider]:
    """Return providers whose class is a structural subtype of *protocol*.

    Filtered by *is_visible* whenever a filter is supplied: a scan only sees
    rows the consumer's domain may use (MV-30), and in the global view that
    means global rows only (MV-32). The protocol class itself is never a match,
    so a Protocol that is also registered as a concrete class does not shadow
    its implementers (MV-31).

    When *is_visible* is ``None`` (encapsulation off) every provider is scanned:
    that is the flat registry.
    """
    matches: list[Provider] = []
    seen: set[int] = set()
    for p in providers.values():
        # ``Provider.cls`` is annotated ``type`` for compatibility but holds
        # string and ``Token`` keys at runtime (see the class docstring).
        if not isinstance(p.cls, type):  # pyright: ignore[reportUnnecessaryIsInstance]
            continue
        if id(p) in seen:
            # One row can be reachable under several tokens (``provides=[T]``
            # plus its own class), and the scan must name it once (MV-31).
            continue
        if is_visible is not None and not is_visible(p, owning_module):
            continue
        try:
            if issubclass(p.cls, protocol) and p.cls is not protocol:
                matches.append(p)
                seen.add(id(p))
        except TypeError:
            pass
    return matches


def _looks_injectable(ann: Any) -> bool:
    """Return True when ``ann`` plausibly names an injectable collaborator.

    The check is deliberately structural so lauren_di stays compatible with
    the full range of idioms users reach for:

    * Bare ``@injectable`` classes — always accepted.
    * Runtime-checkable ``Protocol`` tokens — may be bound at
      registration time via ``provides=[...]``.
    * ``Depends[factory_fn]`` / ``Depends[SomeClass]`` — an explicit
      field-level opt-in to function-provider or class-provider
      injection. These arrive here wrapped in
      ``Annotated[T, Depends]``; we unwrap and recurse so the inner
      type is inspected on its own terms.

    Stdlib primitives (``str``, ``int``, ``list[...]``, ``dict[...]``)
    are rejected so plain-data fields on ``@dataclass``-decorated
    providers aren't mistaken for DI dependencies.
    """
    import typing as _typing

    origin = _typing.get_origin(ann)
    # ``Annotated[T, Marker, ...]`` — inspect the metadata for an injection
    # marker (``Inject``, ``Depends``, ...) and treat any such
    # form as an injectable field. Falling through to the inner type
    # check gives callers who wrote ``Annotated[int, SomeUnrelatedMeta]``
    # the right answer too.
    if origin is not None and _typing.get_origin(ann) is not None:
        # ``typing.Annotated`` identity test varies a little across Python
        # versions; ``__metadata__`` is the stable marker.
        metadata = getattr(ann, "__metadata__", ())
        if metadata:
            # A host-specific marker is the embedder's business now: it is
            # handled by ``field_predicate`` in ``_collect_field_deps``.
            # Falling through to the inner type keeps
            # ``Annotated[int, SomeUnrelatedMeta]`` answering correctly.
            inner = _typing.get_args(ann)
            if inner:
                return _looks_injectable(inner[0])
            return False
        # ``list[T]`` is the one parameterised generic the container
        # knows how to materialise — it stands in for "every multi-bound
        # provider of T". Recurse into ``T`` so a field typed
        # ``list[SomeProtocol]`` is collected as a DI field when
        # ``SomeProtocol`` itself is injectable.
        element_type = _multi_binding_element_type(ann)
        if element_type is not None:
            return _looks_injectable(element_type)
        # Other parameterised generics (``dict[K, V]``, ``tuple[X, ...]``,
        # …) never name an injectable provider directly.
        return False
    if not isinstance(ann, type):
        return False
    if ann in _NON_INJECTABLE_PRIMITIVES:
        return False
    # Explicit opt-in: the class carries an ``@injectable`` marker.
    if hasattr(ann, "__lauren_injectable__"):
        return True
    # Protocol — may be bound by a provider at registration time.
    if getattr(ann, "_is_runtime_protocol", False):
        return True
    # Last resort: a user-defined class in the application itself.
    # The compiler will reject this as ``MissingProviderError`` if no
    # provider is actually registered, surfacing the mistake early
    # rather than silently skipping the field.
    return True


def _callable_default_map(provider: Provider) -> dict[str, bool]:
    """Map ``param_name -> has_default`` for the provider's factory.

    Used during compile and instantiation to detect optional params —
    those may silently skip DI resolution if no provider exists.

    For ``provider_kind == 'factory'`` (use_factory) we don't ask the
    user's lambda about its defaults: the dependency contract there is
    declared positionally via ``injects=[...]`` and optional entries
    are tagged by :class:`OptionalDep`. Returning ``{}`` here lets the
    OptionalDep path own that decision rather than the lambda's
    parameter defaults.
    """
    if provider.provider_kind in ("value", "factory", "existing"):
        return {}
    target: Any
    if provider.is_function_provider:
        target = provider.factory
    elif provider.provider_kind == "class":
        # use_class: the bound class lives on ``factory``; ``cls`` is
        # the token (which may be a string). Use the bound class for
        # signature introspection.
        target = provider.factory
        if target is None:
            return {}
    else:
        # Standard class provider — inspect the class as a callable
        # so the same ``__signature__`` / metaclass-``__call__``
        # priority applies as in :func:`_inspect_class_deps`.
        target = provider.cls
    try:
        sig = inspect.signature(target)
    except (TypeError, ValueError):
        return {}
    return {n: (p.default is not inspect.Parameter.empty) for n, p in sig.parameters.items()}


async def _construct_class(
    cls: type,
    init_kwargs: dict[str, Any],
    field_values: dict[str, Any],
) -> Any:
    """Construct an instance of ``cls`` via Python's normal call protocol.

    Strategy:

    1. Filter ``init_kwargs`` down to the parameters
       :func:`inspect.signature` advertises for ``cls`` — that's the
       authoritative shape, regardless of whether the eventual call
       reaches ``__init__`` / ``__new__`` / a metaclass ``__call__``
       / a ``__signature__``-published surface.
    2. Invoke ``cls(**filtered_kwargs)``. Whatever construction path
       Python chooses (metaclass ``__call__`` first, then
       ``__new__`` + ``__init__`` by default) runs verbatim.
    3. Inject any class-body-annotated DI fields as plain attributes
       on the resulting instance.

    This is a deliberate departure from the original implementation,
    which emulated :meth:`type.__call__` directly by walking
    ``cls.__new__`` and ``cls.__init__`` itself. That approach broke
    every class whose construction is actually mediated by a custom
    metaclass or a published ``__signature__`` (Pydantic models,
    attrs classes, NestJS-style factory metaclasses, ORM identity
    caches — anything that wires its own call protocol). Letting
    Python do the call is simpler and correct for every shape.

    .. note::
        Class-body-annotated DI fields are now set **after** the
        instance returns from ``cls(**kwargs)``. If a user-written
        ``__init__`` needs the value, they should declare it as an
        ``__init__`` parameter (the standard, framework-agnostic
        contract) rather than as a class-body annotation.

    Any kwarg that doesn't match the class's signature is silently
    dropped — useful when the same dep dict is shared with sibling
    callables (function providers, etc.) that consume different
    subsets.
    """
    try:
        sig = inspect.signature(cls)
        sig = _resolve_class_signature_hints(cls, sig)
    except (TypeError, ValueError):
        sig = None

    if sig is not None:
        kwargs = _filter_kwargs_for_signature(init_kwargs, sig, skip=())
    else:
        # No usable signature — best effort: pass everything and let
        # the class itself complain. This branch is reached only for
        # the rare C-extension class that refuses introspection.
        kwargs = init_kwargs

    result = cls(**kwargs)
    if inspect.isawaitable(result):
        # ``cls(**kwargs)`` returning a coroutine is unusual but
        # possible if a metaclass ``__call__`` is async. Await it
        # consistently with the rest of the resolver path.
        result = await result  # pragma: no cover

    # Field injection happens after construction. The class — and any
    # metaclass mediating its construction — has finished its work,
    # so we can safely layer DI-resolved fields on top. Frozen /
    # immutable instances reject ``__setattr__``; we skip those gracefully
    # because a user opting into immutability should be expressing
    # their dependencies through constructor parameters anyway.
    for attr, value in field_values.items():
        with contextlib.suppress(AttributeError, TypeError):
            object.__setattr__(result, attr, value)
    return result


def _filter_kwargs_for_signature(
    kwargs: dict[str, Any],
    sig: inspect.Signature,
    *,
    skip: tuple[str, ...] = (),
) -> dict[str, Any]:
    """Return the subset of ``kwargs`` accepted by ``sig``.

    Respects ``**kwargs`` (everything goes through) and filters out any
    names the signature doesn't declare explicitly when no var-keyword
    parameter is present.
    """
    has_var_kw = any(p.kind is inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values())
    if has_var_kw:
        return {k: v for k, v in kwargs.items() if k not in skip}
    accepted = {
        n
        for n, p in sig.parameters.items()
        if p.kind
        in (
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            inspect.Parameter.KEYWORD_ONLY,
        )
        and n not in skip
    }
    return {k: v for k, v in kwargs.items() if k in accepted}


# ``_scope`` imports ``_describe`` from this module, so importing it at the top
# would be a cycle. Deferring it to the end of the module body is what keeps
# ``DIContainer.scope``'s ``-> ResolutionScope`` annotation resolvable at runtime
# (``typing.get_type_hints``) and not only under type checking.
from ._scope import ResolutionScope  # noqa: E402  (deferred: see above)

__all__ = [
    "INJECTABLE_META",
    "DIContainer",
    "InjectableMeta",
    "Provider",
]
