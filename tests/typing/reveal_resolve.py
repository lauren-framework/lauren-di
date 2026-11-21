"""Static-typing fixture for the ``resolve`` overloads (PRD 01 §5.6).

This module is never executed and never collected by pytest: it exists so a
type checker can assert *what the overloads infer*. The ``reveal_type`` calls
are the assertions -- run::

    mypy tests/typing/reveal_resolve.py

Expected output: ``Config`` for a class token, ``Any`` for the string and
``Token`` fallbacks, and the same pair through :class:`~lauren_di.ResolutionScope`.

§5.6's fallback overload names the two *non-class* token kinds (``str`` and
``Token``) instead of ``Any``, which is what lets a checker reject
``number: int = await container.resolve(Config)`` instead of quietly taking the
untyped variant; ``tests/unit/test_resolve_contract.py`` pins that rejection.
The consequence for the consumer side is the last function here: an exotic
hashable key resolves at runtime, and the *static* spelling of that call is
``cast(Any, key)``.

``reveal_type`` is a type-checker builtin with no runtime existence, which is
why this file is not importable test code. ``tests/typing/`` is wired into
``nox -s typing`` rather than pytest.
"""

from __future__ import annotations

from typing import Any, cast

from lauren_di import DIContainer, ResolutionScope, Token


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
    """The two non-class token kinds keep the ``Any`` shape of the fallback."""
    from_string = await container.resolve("config")
    reveal_type(from_string)
    from_token = await container.resolve(Token("cfg"))
    reveal_type(from_token)
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
    """A non-class token through a scope stays ``Any``."""
    other = await scope.resolve(Token("opaque"))
    reveal_type(other)
    return other


async def exotic_key_needs_a_cast(container: DIContainer) -> None:
    """§5.6: any hashable key resolves, but the *static* spelling is a cast.

    The fallback admits ``str`` and ``Token`` only, because admitting ``Any``
    would admit class tokens too and re-open the hole the narrowing closed. A
    host that registers an arbitrary instance as a key writes the call this
    way; the runtime is unchanged and accepts the key directly.
    """
    key = object()
    resolved = await container.resolve(cast(Any, key))
    reveal_type(resolved)
