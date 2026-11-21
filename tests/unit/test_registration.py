"""Registration: the decorator, the ``Provider`` record, and its rejection rules."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

import pytest
from lauren_di import (
    DecoratorUsageError,
    DIContainer,
    DuplicateBindingError,
    MetadataInheritanceError,
    MissingProviderError,
    Provider,
    Scope,
    injectable,
    injectable_meta,
    post_construct,
    pre_destruct,
)

from helpers import run

pytestmark = pytest.mark.unit


# -- the decorator -----------------------------------------------------------


def test_injectable_records_default_metadata() -> None:
    @injectable()
    class Repo:
        pass

    meta = injectable_meta(Repo)
    assert meta is not None
    assert meta.scope is Scope.SINGLETON
    assert meta.provides == ()
    assert meta.multi is False


def test_injectable_records_explicit_metadata() -> None:
    @injectable(scope=Scope.REQUEST, provides=[bytes], multi=True)
    class Repo:
        pass

    meta = injectable_meta(Repo)
    assert meta is not None
    assert meta.scope is Scope.REQUEST
    assert meta.provides == (bytes,)
    assert meta.multi is True


def test_bare_injectable_is_rejected() -> None:
    with pytest.raises(DecoratorUsageError) as caught:
        injectable(str)  # type: ignore[call-arg]

    assert caught.value.detail["decorator"] == "injectable"
    assert caught.value.detail["target"] == "str"
    assert "must be called" in caught.value.message


def test_primitives_are_never_injectable() -> None:
    assert injectable_meta(str) is None
    assert injectable_meta(object()) is None


def test_inherited_metadata_is_rejected() -> None:
    @injectable()
    class Base:
        pass

    class Child(Base):
        pass

    with pytest.raises(MetadataInheritanceError) as caught:
        DIContainer().register(Child)

    assert caught.value.detail == {"target": "Child", "base": "Base"}
    assert "inherited injectable metadata" in caught.value.message


def test_lifecycle_decorators_mark_their_methods() -> None:
    class Service:
        @post_construct
        def up(self) -> None:
            pass

        @pre_destruct
        def down(self) -> None:
            pass

    assert Service.up.__lauren_di_post_construct__ is True  # type: ignore[attr-defined]
    assert Service.down.__lauren_di_pre_destruct__ is True  # type: ignore[attr-defined]


# -- the Provider record -----------------------------------------------------


def test_register_returns_a_finalised_value_record() -> None:
    @injectable()
    class Repo:
        pass

    provider = DIContainer().register(Repo)

    assert isinstance(provider, Provider)
    assert provider.cls is Repo
    assert provider.scope is Scope.SINGLETON
    assert provider.owning_module is None
    assert provider.multi is False
    assert provider.provides == ()
    # The container finalises the record: the factory is the class itself,
    # so the resolver never has to ask "class or function?" (PRD 02 §5.4).
    assert provider.factory is Repo
    assert provider.provider_kind is None


def test_register_records_the_owning_module() -> None:
    class Billing:
        pass

    @injectable()
    class Repo:
        pass

    provider = DIContainer().register(Repo, owning_module=Billing)
    assert provider.owning_module is Billing


def test_registering_the_same_token_twice_is_rejected() -> None:
    @injectable()
    class Repo:
        pass

    container = DIContainer()
    container.register(Repo)

    with pytest.raises(DuplicateBindingError) as caught:
        container.register(Repo)

    assert caught.value.detail == {"token": "Repo", "existing": "Repo", "new": "Repo"}
    assert caught.value.message == "Repo is already registered"


def test_register_requires_the_decorator() -> None:
    class Plain:
        pass

    with pytest.raises(MissingProviderError) as caught:
        DIContainer().register(Plain)

    assert caught.value.detail == {"token": "Plain"}
    assert caught.value.message == "Plain is not marked @injectable"


def test_non_class_owner_is_rejected_at_registration() -> None:
    @injectable()
    class Repo:
        pass

    with pytest.raises(DecoratorUsageError) as caught:
        DIContainer().register(Repo, owning_module="Billing")  # type: ignore[arg-type]

    assert caught.value.detail == {"token": "Repo", "owning_module": "'Billing'"}


def test_provider_records_constructor_dependencies() -> None:
    @injectable()
    class Repo:
        pass

    @injectable()
    class Service:
        def __init__(self, repo: Repo) -> None:
            self.repo = repo

    provider = DIContainer().register(Service)
    assert provider.deps == (("repo", Repo),)


def test_provider_records_field_dependencies() -> None:
    @injectable()
    class Repo:
        pass

    @injectable()
    class Service:
        repo: Repo

    provider = DIContainer().register(Service)
    assert provider.field_deps == (("repo", Repo),)
    assert provider.deps == ()


def test_structural_protocols_are_bindable_tokens() -> None:
    @runtime_checkable
    class Store(Protocol):
        def total(self) -> int: ...

    @injectable(provides=[Store])
    class DictStore:
        def total(self) -> int:
            return 1

    container = DIContainer()
    container.register(DictStore)

    assert container.has_provider(Store) is True
    assert type(run(container.resolve(Store))) is DictStore
