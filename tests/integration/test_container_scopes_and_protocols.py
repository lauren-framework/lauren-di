"""Request scopes, teardown aggregation and structural Protocol resolution.

Three behaviours share this file because they all live on the *edge* of the
container: what happens when the app stops (``shutdown``), when a request ends
(``ResolutionScope.close``), and when the token is a ``typing.Protocol`` that
was never registered explicitly.

Each is an error-handling path, and error-handling paths are exactly where a
refactor silently drops a case: an exception swallowed in one teardown loop
looks identical to a clean shutdown.  The tests therefore assert on the
collected :class:`DestructError` values and on the concrete exception types,
not merely that "something was raised" (PRD 05 §lifecycle, PRD 06 §errors).
"""

from __future__ import annotations

import asyncio
import typing

import pytest
from lauren_di import (
    DestructError,
    DIContainer,
    DIScopeViolationError,
    ProtocolAmbiguityError,
    ResolutionScope,
    Scope,
    injectable,
    post_construct,
    pre_destruct,
)

pytestmark = pytest.mark.integration


class Recorder:
    """Collects lifecycle callbacks so a test can assert on their ordering."""

    def __init__(self) -> None:
        """Start with an empty call log."""
        self.events: list[str] = []

    def record(self, event: str) -> None:
        """Append ``event`` to the log.

        Args:
            event: The lifecycle step that just ran.
        """
        self.events.append(event)


class TestRequestScopeLifecycle:
    """A ``ResolutionScope`` owns the request-scoped instances it created.

    ``post_construct`` must run when the instance is created and
    ``pre_destruct`` when the scope closes — never in between, and never twice.
    """

    def test_post_construct_runs_on_creation_and_pre_destruct_on_close(self) -> None:
        """The two hooks bracket the scope: create → use → tear down."""
        recorder = Recorder()

        @injectable()
        class Session:
            @post_construct
            def open(self) -> None:
                recorder.record("open")

            @pre_destruct
            def close(self) -> None:
                recorder.record("close")

        container = DIContainer()
        container.register_class(Session, Session, scope=Scope.REQUEST)
        container.compile()

        async def scenario() -> None:
            async with ResolutionScope(container) as scope:
                await scope.resolve(Session)
                assert recorder.events == ["open"]
            assert recorder.events == ["open", "close"]

        asyncio.run(scenario())


class TestTeardownErrorAggregation:
    """Teardown never lets one failure abort the remaining clean-up.

    A request that leaks three connections because the first ``pre_destruct``
    raised is worse than a reported error, so ``close``/``shutdown`` must keep
    going and hand back every failure as a :class:`DestructError`.
    """

    def test_failing_pre_destruct_is_reported_not_raised(self) -> None:
        """``close`` returns errors instead of propagating them."""

        @injectable()
        class Session:
            @pre_destruct
            def close(self) -> None:
                raise RuntimeError("half-closed")

        container = DIContainer()
        container.register_class(Session, Session, scope=Scope.REQUEST)
        container.compile()

        async def scenario() -> tuple[DestructError, ...]:
            async with ResolutionScope(container) as scope:
                await scope.resolve(Session)
                return await scope.close()

        errors = asyncio.run(scenario())

        assert len(errors) == 1
        assert isinstance(errors[0], DestructError)

    def test_close_is_idempotent(self) -> None:
        """A second ``close`` replays the first result and re-runs no hooks."""
        recorder = Recorder()

        @injectable()
        class Session:
            @pre_destruct
            def close(self) -> None:
                recorder.record("close")
                raise RuntimeError("boom")

        container = DIContainer()
        container.register_class(Session, Session, scope=Scope.REQUEST)
        container.compile()

        async def scenario() -> tuple[int, int]:
            async with ResolutionScope(container) as scope:
                await scope.resolve(Session)
                first = await scope.close()
                second = await scope.close()
                return len(first), len(second)

        assert asyncio.run(scenario()) == (1, 1)
        assert recorder.events == ["close"]

    def test_aclose_errors_are_collected(self) -> None:
        """The duck-typed ``aclose`` hook is torn down like a lifecycle hook
        and its failure is aggregated the same way.
        """

        @injectable()
        class AsyncClient:
            async def aclose(self) -> None:
                raise RuntimeError("cannot close")

        container = DIContainer()
        container.register_class(AsyncClient, AsyncClient, scope=Scope.REQUEST)
        container.compile()

        async def scenario() -> tuple[DestructError, ...]:
            async with ResolutionScope(container) as scope:
                await scope.resolve(AsyncClient)
            return await scope.close()

        errors = asyncio.run(scenario())

        assert len(errors) == 1
        assert isinstance(errors[0], DestructError)

    def test_shutdown_collects_singleton_teardown_failures(self) -> None:
        """``shutdown`` answers the same question for singletons."""

        @injectable()
        class Pool:
            @pre_destruct
            def close(self) -> None:
                raise RuntimeError("pool stuck")

        container = DIContainer()
        container.register_class(Pool, Pool)
        container.compile()

        async def scenario() -> tuple[DestructError, ...]:
            await container.resolve(Pool)
            return await container.shutdown()

        errors = asyncio.run(scenario())

        assert len(errors) == 1
        assert isinstance(errors[0], DestructError)

    def test_clean_shutdown_returns_no_errors(self) -> None:
        """The happy path must stay silent — an empty tuple, not ``None``."""

        @injectable()
        class Pool:
            pass

        container = DIContainer()
        container.register_class(Pool, Pool)
        container.compile()

        async def scenario() -> tuple[DestructError, ...]:
            await container.resolve(Pool)
            return await container.shutdown()

        assert asyncio.run(scenario()) == ()


class TestAdoptLifecycle:
    """``adopt_lifecycle`` registers an externally built object for teardown.

    Frameworks create some instances themselves (middleware, transport
    adapters) but still want the container to close them with everything else.
    """

    def test_adopted_instance_is_torn_down_with_the_container(self) -> None:
        """The adopted object's ``pre_destruct`` must run on shutdown."""
        recorder = Recorder()

        class External:
            @pre_destruct
            def close(self) -> None:
                recorder.record("closed")

        container = DIContainer()
        container.compile()
        container.adopt_lifecycle(External())

        asyncio.run(container.shutdown())

        assert recorder.events == ["closed"]


class TestStrictRequestScope:
    """``strict_request_scope=True`` turns a silent leak into an error.

    Resolving a request-scoped provider outside a scope produces an instance
    nobody will ever tear down.  The default logs a warning; strict mode makes
    the same situation fail loudly (PRD 05 §request scope).
    """

    def test_resolving_request_scope_outside_a_scope_raises(self) -> None:
        """Strict mode refuses to create an untracked request instance."""

        @injectable()
        class Session:
            pass

        container = DIContainer(strict_request_scope=True)
        container.register_class(Session, Session, scope=Scope.REQUEST)
        container.compile()

        with pytest.raises(DIScopeViolationError):
            asyncio.run(container.resolve(Session))

    def test_inside_a_scope_strict_mode_allows_the_same_provider(self) -> None:
        """Strict mode constrains *where* resolution happens, not whether the
        provider is usable at all.
        """

        @injectable()
        class Session:
            pass

        container = DIContainer(strict_request_scope=True)
        container.register_class(Session, Session, scope=Scope.REQUEST)
        container.compile()

        async def scenario() -> object:
            async with ResolutionScope(container) as scope:
                return await scope.resolve(Session)

        assert isinstance(asyncio.run(scenario()), Session)


@typing.runtime_checkable
class Greeter(typing.Protocol):
    """Runtime-checkable protocol used to exercise structural resolution."""

    def greet(self) -> str:
        """Return a greeting."""


class EnglishGreeter:
    """Structurally satisfies :class:`Greeter` without inheriting it."""

    def greet(self) -> str:
        """Return an English greeting."""
        return "hello"


class FrenchGreeter:
    """A second structural match, used to provoke ambiguity."""

    def greet(self) -> str:
        """Return a French greeting."""
        return "bonjour"


class TestStructuralProtocolResolution:
    """An unregistered ``runtime_checkable`` Protocol resolves structurally.

    Modules declare providers without importing the protocol's token, so the
    container scans visible rows for a unique structural match.  A *unique*
    match resolves; two matches must fail rather than pick arbitrarily
    (PRD 04 §protocols, MV-30 domain scoping).
    """

    def test_unique_structural_match_is_resolved(self) -> None:
        """One matching row is enough to satisfy the protocol token."""
        container = DIContainer()
        container.register_class(EnglishGreeter, EnglishGreeter)
        container.compile()

        assert isinstance(asyncio.run(container.resolve(Greeter)), EnglishGreeter)

    def test_two_structural_matches_are_an_ambiguity(self) -> None:
        """Choosing silently between two implementations would be a wiring
        bug that only shows up as wrong behaviour in production.
        """
        container = DIContainer()
        container.register_class(EnglishGreeter, EnglishGreeter)
        container.register_class(FrenchGreeter, FrenchGreeter)
        container.compile()

        with pytest.raises(ProtocolAmbiguityError):
            asyncio.run(container.resolve(Greeter))

    def test_explicit_binding_beats_the_structural_scan(self) -> None:
        """An explicit registration for the protocol token must win; the scan
        is only a fallback for *unbound* protocol tokens.
        """
        container = DIContainer()
        container.register_class(Greeter, FrenchGreeter)
        container.register_class(EnglishGreeter, EnglishGreeter)
        container.compile()

        assert isinstance(asyncio.run(container.resolve(Greeter)), FrenchGreeter)
