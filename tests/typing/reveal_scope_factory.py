"""Static-typing fixture for the ``scope()`` factory's return (PRD 01 §5.3.3).

§5.3.3 declares ``scope(*, values: Mapping[Any, Any] | None = None) ->
ResolutionScope``. The class is named rather than the
``AsyncContextManager[ResolutionScope]`` protocol it satisfies, so *both* call
shapes have to type-check:

* ``async with container.scope() as scope:`` -- the spelling PRD 03 §5.3 and
  every guide use;
* ``scope = container.scope()`` -- holding the object, which keeps ``values``,
  ``errors``, ``closed`` and ``await scope.close()`` usable.

The second shape is the whole reason for the choice: annotating the protocol
would make that assignment a type error while telling the caller less than the
truth about what comes back (``ResolutionScope`` is its own context manager --
``__aenter__`` returns ``self``).

Never executed and never collected by pytest. Run::

    mypy --strict tests/typing/reveal_scope_factory.py
    pyright tests/typing/reveal_scope_factory.py

``nox -s typing`` runs both over the whole ``tests/typing/`` directory.
"""

from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from types import MappingProxyType

from lauren_di import DIContainer, ResolutionScope, Token

CFG = Token("cfg")


class Config:
    dsn: str = "sqlite:///app.db"


def scope_is_the_declared_return_type(container: DIContainer) -> None:
    """§5.3.3: the factory returns ``ResolutionScope``, not the protocol."""
    scope = container.scope()
    reveal_type(scope)  # expect: ResolutionScope


def scope_result_satisfies_the_protocol_the_prd_used_to_name(
    container: DIContainer,
) -> None:
    """The class is a superset: assignable where the protocol is expected.

    The two checkers print this differently -- mypy ``AbstractAsyncContextManager
    [ResolutionScope, bool | None]``, pyright ``ResolutionScope`` -- but both
    accept the assignment, which is the property being asserted.
    """
    manager: AbstractAsyncContextManager[ResolutionScope] = container.scope()
    reveal_type(manager)  # expect: AbstractAsyncContextManager[ResolutionScope]


def scope_result_keeps_its_own_surface(container: DIContainer) -> None:
    """Holding the object is what the class annotation buys: these stay typed."""
    scope = container.scope(values=MappingProxyType({CFG: "from-proxy"}))
    reveal_type(scope.values)  # expect: dict[Any, Any]
    reveal_type(scope.closed)  # expect: bool


async def scope_works_in_async_with(container: DIContainer) -> None:
    """The protocol spelling is unchanged by the annotation."""
    async with container.scope() as scope:
        reveal_type(scope)  # expect: ResolutionScope
        config = await scope.resolve(Config)
        reveal_type(config)  # expect: Config


async def scope_closes_explicitly_when_held(container: DIContainer) -> None:
    """``await scope.close()`` only exists if the class is the declared type."""
    scope = container.scope()
    errors = await scope.close()
    reveal_type(errors)  # expect: tuple[DestructError, ...]
