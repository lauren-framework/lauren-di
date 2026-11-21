"""Static-typing fixture for ``resolve(values=...)`` (PRD 01 §5.3.3 / §6.3).

``values`` is declared ``Mapping[Any, Any] | None``, so a *non-``dict``* mapping
must type-check, and the key type is ``Any`` because keys are opaque type-like
tokens -- a ``Token``, a string, or any other object. Before baseline B-2 was
fixed the annotation was ``dict[type, Any] | None``, which rejected all of the
shapes below.

Never executed and never collected by pytest. Run::

    mypy --strict --follow-imports=silent tests/typing/reveal_values_mapping.py

``--follow-imports=silent`` keeps the check on the *consumer* side: the package
itself is not strict-clean yet (that is a separate, tracked goal), but this
fixture must hold under ``--strict``.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import Any

from lauren_di import DIContainer, Token

CFG = Token("cfg")


class Config:
    dsn: str = "sqlite:///app.db"


def read_only_mapping() -> Mapping[Any, Any]:
    """A ``Mapping`` that is not a ``dict``: the annotation must accept it."""
    return MappingProxyType({CFG: "from-proxy"})


async def values_accepts_a_non_dict_mapping(container: DIContainer) -> None:
    resolved = await container.resolve(CFG, values=read_only_mapping())
    reveal_type(resolved)


async def values_accepts_opaque_keys(container: DIContainer) -> None:
    """Keys are ``Any``: a Token, a string and a bare object all type-check."""
    opaque_key: Any = object()
    await container.resolve(
        opaque_key,
        values={CFG: 1, "dsn": "sqlite:///app.db", opaque_key: None},
    )


async def values_accepts_a_declared_mapping_variable(container: DIContainer) -> None:
    values: Mapping[Any, Any] = {CFG: "declared"}
    await container.resolve(CFG, values=values)
    await container.resolve(Config, values=values)


async def request_cache_accepts_opaque_keys(container: DIContainer) -> None:
    """``request_cache`` is ``dict[Any, Any] | None``, not ``dict[type, Any]``."""
    cache: dict[Any, Any] = {CFG: object(), "key": object()}
    await container.resolve(Config, request_cache=cache)


async def scope_factory_accepts_a_non_dict_mapping(container: DIContainer) -> None:
    """§5.3.3: ``scope(*, values: Mapping[Any, Any] | None = None) -> ResolutionScope``.

    ``async with`` is the spelling both PRDs show, and it holds now that the
    factory is annotated with the class rather than the protocol.
    """
    async with container.scope(values=read_only_mapping()) as scope:
        resolved = await scope.resolve(CFG)
        reveal_type(resolved)
