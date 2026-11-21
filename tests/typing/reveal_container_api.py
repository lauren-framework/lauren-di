"""Static-typing fixture for the §5.4 *container* surface (PRD 01 §5.3.3, §5.6).

The declaration fixture covers what a user writes; this one covers what a user
*calls*: constructing a container (including with an ``OverridePlan``), the
registration methods, ``resolve`` in each of its shapes, the scope factory, the
override context manager, and the two structural aliases ``Owner`` and
``Grant``.

``resolve`` is the reason the overloads exist, so every shape §5.4 shows is
exercised here: a class token (typed), a ``Token``/string token (``Any``), a
``list[T]`` multi-binding (typed as a list), and a call through a
``ResolutionScope`` (same pair).

Never executed, never collected by pytest: ``nox -s typing`` runs the checkers
over this directory.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import Annotated, Any

from lauren_di import (
    DIContainer,
    Grant,
    Inject,
    OverridePlan,
    Owner,
    Provider,
    ResolutionScope,
    Token,
    active_override_plan,
    building_with,
    injectable,
    use_value,
)


class Config:
    dsn: str = "sqlite:///app.db"


class Repo:
    session: str = "session"


class Module:
    """A module class, used as an ``Owner`` and as a ``Grant`` address."""


DB = Token("DB")


# -- construction and registration -----------------------------------------


async def container_construction(plan: OverridePlan | None) -> None:
    """``DIContainer`` is keyword-only; ``overrides`` is a record, not a dict."""
    container = DIContainer(overrides=plan, strict_request_scope=True)
    reveal_type(container)  # expect: DIContainer
    provider: Provider = container.register(Config)
    reveal_type(provider)  # expect: Provider
    container.register(Repo, owning_module=Module)
    custom = use_value(provide="SETTINGS", value={"debug": True})
    reveal_type(container.register_custom(custom))  # expect: Provider
    reveal_type(container.compile())  # expect: None
    reveal_type(container.all_providers())  # expect: list[Provider]
    reveal_type(container.registered_providers())  # expect: tuple[Provider, ...]
    reveal_type(container.instance_index())  # expect: Mapping[Any, Provider]
    reveal_type(container.explain(Config))  # expect: str
    reveal_type(await container.shutdown())  # expect: tuple[DestructError, ...]


# -- resolve, in every §5.4 shape ------------------------------------------


async def resolve_keeps_the_class_token_typed(container: DIContainer) -> None:
    config = await container.resolve(Config)
    reveal_type(config)  # expect: Config
    repo = await container.resolve(Repo)
    reveal_type(repo)  # expect: Repo


async def resolve_falls_back_to_any_for_opaque_tokens(container: DIContainer) -> Any:
    from_token = await container.resolve(DB)
    reveal_type(from_token)  # expect: Any
    from_string = await container.resolve("SETTINGS")
    reveal_type(from_string)  # expect: Any
    return from_string


async def resolve_multi_binding_is_a_list(container: DIContainer) -> None:
    """``list[T]`` is a ``type[...]`` expression, so the element type survives."""
    repos = await container.resolve(list[Repo])
    reveal_type(repos)  # expect: list[Repo]


async def resolve_accepts_keyword_only_parameters(container: DIContainer) -> None:
    """``request_cache`` / ``values`` / ``owning_module`` stay keyword-only."""
    resolved = await container.resolve(
        Config,
        request_cache={},
        values={DB: "from-values"},
        owning_module=None,
    )
    reveal_type(resolved)  # expect: Config


# -- the scope factory and ResolutionScope ---------------------------------


async def scope_is_an_async_context_manager(container: DIContainer) -> None:
    """§5.3.3: ``scope(*, values: Mapping[Any, Any] | None = None) -> ResolutionScope``.

    ``ResolutionScope`` is itself the async context manager (``__aenter__``
    returns ``self``), so ``async with`` is the spelling both PRD 03 §5.3 and
    PRD 01 §5.3.3 show.
    """
    values: Mapping[Any, Any] = MappingProxyType({DB: "from-proxy"})
    async with container.scope(values=values) as scope:
        reveal_type(scope)  # expect: ResolutionScope
        config = await scope.resolve(Config)
        reveal_type(config)  # expect: Config
        opaque = await scope.resolve(DB)
        reveal_type(opaque)  # expect: Any
        reveal_type(scope.values)  # expect: Mapping[Any, Any]
        reveal_type(scope.closed)  # expect: bool


async def scope_close_returns_errors(scope: ResolutionScope) -> None:
    """Teardown failures are collected, not raised."""
    reveal_type(await scope.close())  # expect: tuple[DestructError, ...]
    reveal_type(scope.errors)  # expect: tuple[DestructError, ...]


# -- overrides and the structural aliases ----------------------------------


def overrides_are_a_context_manager(plan: OverridePlan) -> None:
    """``building_with`` installs a plan for the duration of the block."""
    with building_with(plan):
        current = active_override_plan()
        reveal_type(current)  # expect: OverridePlan | None


def owner_and_grant_are_structural() -> None:
    """``Owner`` is ``type | None``; ``Grant`` admits a bare token or a pair."""
    owner: Owner = Module
    reveal_type(owner)  # expect: type | None
    none_owner: Owner = None
    reveal_type(none_owner)  # expect: type | None
    bare: Grant = DB
    reveal_type(bare)  # expect: Any
    pair: Grant = (DB, Module)
    reveal_type(pair)  # expect: Any


def visibility_is_typed(container: DIContainer) -> None:
    """The visible-set API takes ``frozenset[Grant]`` and returns tokens."""
    container.set_visible(Module, frozenset({DB, (Repo, Module)}))
    container.grant_visible(Module, frozenset({(Repo, Module)}))
    reveal_type(container.visible_in(Module))  # expect: frozenset[Any]
    reveal_type(container.applied_overrides())  # expect: frozenset[Any]
    reveal_type(container.has_provider(Config))  # expect: bool


@injectable()
class Injected:
    """A declaration used only to prove ``Inject`` is annotation-safe here."""

    def __init__(self, conn: Annotated[Any, Inject(DB)]) -> None:
        self.conn = conn


def inject_marker_is_annotation_only(injected: Injected) -> None:
    reveal_type(injected.conn)  # expect: Any


def scope_factory_is_callable_without_values(container: DIContainer) -> None:
    """``values`` is optional, so the factory takes no required arguments.

    The result is ``Any`` today because ``DIContainer.scope`` is annotated
    ``-> Any``; that drift is tracked as its own goal (PRD 01 §5.3.3).
    """
    reveal_type(container.scope())  # expect: Any (until §5.3.3 is reconciled)
