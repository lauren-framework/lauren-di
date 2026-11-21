"""Static-typing fixture for the ``resolve`` overloads (PRD 01 §5.6).

This module is never executed and never collected by pytest: it exists so a
type checker can assert *what the overloads infer*. The ``reveal_type`` calls
are the assertions -- run::

    mypy tests/typing/reveal_resolve.py

Expected output: ``Config`` for a class token, ``Any`` for the string and
arbitrary-object fallbacks, and the same pair through
:class:`~lauren_di.ResolutionScope`.

``reveal_type`` is a type-checker builtin with no runtime existence, which is
why this file is not importable test code. ``tests/typing/`` is wired into
``nox -s typing`` rather than pytest.
"""

from __future__ import annotations

from typing import Any

from lauren_di import DIContainer, ResolutionScope


class Config:
    """A plain class token: the ``type[T]`` overload applies."""

    dsn: str = "sqlite:///app.db"


class Repo:
    """A second class token, so the two inferences cannot be confused."""

    session: str = "session"


async def container_infers_class_token(container: DIContainer) -> None:
    """A class token resolves to that class, not ``Any``."""
    resolved = await container.resolve(Config)
    reveal_type(resolved)


async def container_infers_across_tokens(container: DIContainer) -> None:
    """The ``type[T]`` overload tracks ``T``, it does not hard-code one class."""
    repo = await container.resolve(Repo)
    reveal_type(repo)


async def container_falls_back_to_any(container: DIContainer) -> Any:
    """Non-class tokens keep the ``Any`` shape of the fallback overload."""
    from_string = await container.resolve("config")
    reveal_type(from_string)
    return from_string


async def container_keeps_keyword_only_parameters(container: DIContainer) -> None:
    """``request_cache`` / ``values`` / ``owning_module`` stay keyword-only."""
    resolved = await container.resolve(
        Config,
        request_cache={},
        values={},
        owning_module=None,
    )
    reveal_type(resolved)


async def container_infers_multi_binding(container: DIContainer) -> None:
    """A ``list[T]`` alias is a ``type[...]`` expression, so it stays typed.

    The element type is preserved rather than collapsing to ``Any``: a
    multi-binding resolves to a list of the bound type (PRD 04 §5.8).
    """
    repos = await container.resolve(list[Repo])
    reveal_type(repos)


async def scope_infers_class_token(scope: ResolutionScope) -> None:
    """The scope overload mirrors the container's ``type[T]`` variant."""
    resolved = await scope.resolve(Config)
    reveal_type(resolved)


async def scope_falls_back_to_any(scope: ResolutionScope) -> Any:
    """A non-type token through a scope stays ``Any``."""
    other = await scope.resolve(object())
    reveal_type(other)
    return other
