"""Resolution: constructor injection, field injection, providers and value tokens."""

from __future__ import annotations

import dataclasses
from typing import Annotated

import pytest
from lauren_di import (
    Depends,
    DIContainer,
    Inject,
    MissingProviderError,
    Scope,
    Token,
    injectable,
)

from helpers import run

pytestmark = pytest.mark.unit


@injectable()
class Repo:
    def __init__(self) -> None:
        self.rows = ["a"]


@injectable()
class Service:
    def __init__(self, repo: Repo) -> None:
        self.repo = repo


def test_constructor_injection() -> None:
    container = DIContainer()
    container.register(Repo)
    container.register(Service)

    service = run(container.resolve(Service))

    assert isinstance(service, Service)
    assert isinstance(service.repo, Repo)


def test_singletons_are_shared() -> None:
    container = DIContainer()
    container.register(Repo)

    assert run(container.resolve(Repo)) is run(container.resolve(Repo))


def test_transients_are_not_shared() -> None:
    @injectable(scope=Scope.TRANSIENT)
    class Clock:
        pass

    container = DIContainer()
    container.register(Clock)

    assert run(container.resolve(Clock)) is not run(container.resolve(Clock))


def test_field_injection_is_assigned_after_construction() -> None:
    @injectable()
    class HasField:
        repo: Repo

        def __init__(self) -> None:
            # PRD 02 §5.6: the field is resolved before construction but
            # assigned after it, so __init__ must not see it yet.
            self.seen_during_init = hasattr(self, "repo")

    container = DIContainer()
    container.register(Repo)
    container.register(HasField)

    instance = run(container.resolve(HasField))

    assert instance.seen_during_init is False
    assert isinstance(instance.repo, Repo)


def test_field_gate_skips_primitives_and_defaults() -> None:
    @injectable()
    class Mixed:
        repo: Repo
        label: str = "static"
        count: int

    container = DIContainer()
    container.register(Repo)
    provider = container.register(Mixed)

    assert provider.field_deps == (("repo", Repo),)
    instance = run(container.resolve(Mixed))
    assert instance.label == "static"
    assert not hasattr(instance, "count")


def test_dataclass_fields_are_left_to_the_generated_init() -> None:
    @injectable()
    @dataclasses.dataclass
    class DataclassService:
        repo: Repo

    container = DIContainer()
    container.register(Repo)
    provider = container.register(DataclassService)

    # Gate 3: dataclass fields are never DI fields, so the generated
    # __init__ resolves them through the constructor instead.
    assert provider.field_deps == ()
    assert provider.deps == (("repo", Repo),)
    assert isinstance(run(container.resolve(DataclassService)).repo, Repo)


def test_function_provider_returns_its_value() -> None:
    @injectable()
    def make_repo() -> Repo:
        return Repo()

    container = DIContainer()
    container.register(make_repo)

    assert isinstance(run(container.resolve(make_repo)), Repo)
    assert run(container.resolve(make_repo)) is run(container.resolve(make_repo))


def test_generator_provider_yields_then_tears_down() -> None:
    events: list[str] = []

    @injectable()
    def resource():  # type: ignore[no-untyped-def]
        events.append("setup")
        yield "value"
        events.append("teardown")

    container = DIContainer()
    container.register(resource)

    run(container.startup())
    assert events == ["setup"]
    assert run(container.resolve(resource)) == "value"

    errors = run(container.shutdown())
    assert errors == ()
    assert events == ["setup", "teardown"]


def test_annotated_inject_resolves_the_named_token() -> None:
    config = Token("config")

    @injectable()
    class UsesToken:
        settings: Annotated[dict, Inject(config)]

    container = DIContainer()
    container.register_value(config, {"debug": True})
    container.register(UsesToken)

    assert run(container.resolve(UsesToken)).settings == {"debug": True}


def test_depends_is_a_spelling_alias() -> None:
    assert Depends[Repo] is Repo


def test_value_tokens_are_skipped_by_compile_and_filled_from_values() -> None:
    @injectable()
    class NeedsPayload:
        def __init__(self, payload: bytes) -> None:
            self.payload = payload

    container = DIContainer(value_tokens=[bytes])
    container.register(NeedsPayload)
    container.compile()  # no provider for ``bytes`` is expected

    resolved = run(container.resolve(NeedsPayload, values={bytes: b"x"}))
    assert resolved.payload == b"x"


def test_missing_provider_names_the_token() -> None:
    container = DIContainer()

    with pytest.raises(MissingProviderError) as caught:
        run(container.resolve("nothing-here"))

    assert caught.value.detail == {"token": "nothing-here"}
    assert caught.value.message == "No provider for nothing-here"


def test_missing_edge_names_the_dependent_and_parameter() -> None:
    class Never:
        pass

    @injectable()
    class NeedsNever:
        def __init__(self, never: Never) -> None:
            self.never = never

    container = DIContainer()
    container.register(NeedsNever)

    with pytest.raises(MissingProviderError) as caught:
        container.compile()

    detail = caught.value.detail
    assert detail["token"] == "Never"
    assert detail["dependent"] == "NeedsNever"
    assert detail["param"] == "never"
    assert "required by NeedsNever.never" in caught.value.message


def test_resolving_a_registered_but_invisible_row_is_not_reported_as_missing() -> None:
    class Billing:
        pass

    @injectable()
    class Private:
        pass

    container = DIContainer()
    container.register(Private, owning_module=Billing)
    container.set_visible(Billing, frozenset())

    class Outside:
        pass

    container.set_visible(Outside, frozenset())

    with pytest.raises(MissingProviderError) as caught:
        run(container.resolve(Private, owning_module=Outside))

    assert caught.value.detail["reason"] == "not_visible"
