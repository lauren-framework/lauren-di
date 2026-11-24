"""The error catalogue: hierarchy, stable codes and representative raises (PRD 05)."""

from __future__ import annotations

import json

import pytest
from lauren_di import (
    CircularDependencyError,
    DecoratorUsageError,
    DestructError,
    DIContainer,
    DIError,
    DIScopeViolationError,
    DuplicateBindingError,
    InvalidProviderError,
    LifecycleConfigError,
    LifecycleViolationError,
    MetadataInheritanceError,
    MissingProviderError,
    OverrideError,
    ProtocolAmbiguityError,
    ProviderOverride,
    Scope,
    UnresolvableParameterError,
    injectable,
    post_construct,
)

from helpers import run

pytestmark = pytest.mark.integration


LEAVES = [
    (InvalidProviderError, "invalid_provider"),
    (DecoratorUsageError, "decorator_usage"),
    (MissingProviderError, "missing_provider"),
    (DuplicateBindingError, "duplicate_binding"),
    (ProtocolAmbiguityError, "protocol_ambiguity"),
    (CircularDependencyError, "circular_dependency"),
    (DIScopeViolationError, "di_scope_violation"),
    (UnresolvableParameterError, "unresolvable_parameter"),
    (MetadataInheritanceError, "metadata_inheritance"),
    (LifecycleConfigError, "lifecycle_config"),
    (LifecycleViolationError, "lifecycle_violation"),
    (DestructError, "destruct_error"),
    (OverrideError, "override_error"),
]


@pytest.mark.parametrize(("leaf", "code"), LEAVES)
def test_every_leaf_subclasses_dierror_with_a_stable_code(leaf: type[DIError], code: str) -> None:
    assert issubclass(leaf, DIError)
    assert leaf.code == code
    assert DIError.code == "lauren_di_error"


def test_codes_are_unique() -> None:
    codes = [code for _, code in LEAVES]
    assert len(codes) == len(set(codes))
    assert "lauren_di_error" not in codes


def test_detail_is_copied_and_json_ready() -> None:
    error = MissingProviderError("nope", detail={"token": "Repo"})
    assert error.detail == {"token": "Repo"}
    json.dumps(error.to_payload(), sort_keys=True)
    assert json.loads(error.as_json())["error"]["code"] == "missing_provider"


def test_repr_names_the_leaf_and_carries_the_detail() -> None:
    """``repr(error)`` is the debugging surface: the leaf, the sentence and the facts.

    ``str`` is the human sentence alone (that is what a log line or an HTTP body
    carries), while ``repr`` is what a traceback, a ``pytest`` assertion diff or a
    structured logger prints when it is handed the exception *object*. Both halves
    are pinned here: a leaf name that lost its ``detail`` reads as a bare sentence
    in a bug report, and nothing else in the catalogue would notice.

    Pinned rather than merely exercised, because the line used to be covered only
    incidentally: ``test_c8_teardown_errors_are_reported`` builds its assertion
    message eagerly, and that message formats ``unit.errors!r`` — so a
    ``DestructError`` object is repr'd only on the adapters that report error
    *objects*, which are the framework-adapted ones (the CLI driver collects
    stderr *strings*). The gate therefore read 88.56 % with the example
    frameworks absent and 88.61 % with them installed: a figure that depends on
    which extras happen to be present, which is the defect
    ``[tool.coverage.report]`` exists to avoid.
    """
    error = MissingProviderError("no provider", detail={"token": "Repo"})
    assert repr(error) == "MissingProviderError('no provider', detail={'token': 'Repo'})"
    assert str(error) == "no provider"
    assert repr(DIError("plain")) == "DIError('plain', detail={})"


def test_catch_all_still_catches_a_leaf() -> None:
    with pytest.raises(DIError):
        DIContainer().register(42)


# -- representative raise sites --------------------------------------------


def test_invalid_provider_names_the_target_and_kind() -> None:
    container = DIContainer()
    with pytest.raises(InvalidProviderError) as caught:
        container.register(42)  # type: ignore[arg-type]
    assert caught.value.detail["target"] == "42"
    assert caught.value.detail["kind"] == "int"


def test_bare_injectable_is_rejected() -> None:
    class Thing:
        pass

    with pytest.raises(DecoratorUsageError) as caught:
        injectable(Thing)  # type: ignore[call-arg]
    assert caught.value.detail["decorator"] == "injectable"


def test_unmarked_class_reports_missing_provider() -> None:
    class Unmarked:
        pass

    with pytest.raises(MissingProviderError) as caught:
        DIContainer().register(Unmarked)
    assert caught.value.detail["token"] == "Unmarked"


def test_second_non_multi_binding_is_a_duplicate() -> None:
    @injectable()
    class Doubled:
        pass

    container = DIContainer()
    container.register(Doubled)
    with pytest.raises(DuplicateBindingError) as caught:
        container.register(Doubled)
    assert caught.value.detail["token"] == "Doubled"


def test_two_providers_for_one_protocol_are_ambiguous() -> None:
    class Proto:
        pass

    @injectable(provides=[Proto])
    class One:
        pass

    @injectable(provides=[Proto])
    class Two:
        pass

    container = DIContainer()
    container.register(One)
    container.register(Two)
    with pytest.raises(ProtocolAmbiguityError) as caught:
        container.compile()
    assert caught.value.detail["token"] == "Proto"
    assert caught.value.detail["count"] == 2


def test_cycle_is_reported_with_its_path() -> None:
    @injectable()
    class A:
        def __init__(self, b: B) -> None:
            self.b = b

    @injectable()
    class B:
        def __init__(self, a: A) -> None:
            self.a = a

    container = DIContainer()
    container.register(A)
    container.register(B)
    with pytest.raises(CircularDependencyError) as caught:
        container.compile()
    assert caught.value.detail["cycle"] == ["A", "B", "A"]


def test_unannotated_parameter_is_unresolvable() -> None:
    @injectable()
    class NoAnnotation:
        def __init__(self, value) -> None:  # type: ignore[no-untyped-def]
            self.value = value

    with pytest.raises(UnresolvableParameterError) as caught:
        DIContainer().register(NoAnnotation)
    assert caught.value.detail["param"] == "value"
    assert caught.value.detail["target"] == "NoAnnotation"


def test_inherited_metadata_is_rejected() -> None:
    @injectable()
    class Base:
        pass

    class Sub(Base):
        pass

    with pytest.raises(MetadataInheritanceError) as caught:
        DIContainer().register(Sub)
    assert caught.value.detail["target"] == "Sub"
    assert caught.value.detail["base"] == "Base"


def test_transient_generator_provider_is_a_lifecycle_error() -> None:
    @injectable(scope=Scope.TRANSIENT)
    def generate():
        yield 1

    with pytest.raises(LifecycleConfigError) as caught:
        DIContainer().register(generate)
    assert caught.value.detail["provider"] == "generate"


def test_closed_scope_is_a_lifecycle_violation() -> None:
    @injectable(scope=Scope.REQUEST)
    class PerRequest:
        pass

    container = DIContainer()
    container.register(PerRequest)

    async def scenario() -> None:
        scope = container.scope()
        await scope.close()
        with pytest.raises(LifecycleViolationError) as caught:
            await scope.resolve(PerRequest)
        assert caught.value.detail["state"] == "closed"

    run(scenario())


def test_bad_override_payload_is_rejected_at_construction() -> None:
    class Token:
        pass

    with pytest.raises(OverrideError) as caught:
        ProviderOverride(token=Token, kind="class", use_class="not-a-class")  # type: ignore[arg-type]
    assert caught.value.code == "override_error"
    assert caught.value.detail["reason"] == "bad_payload"


def test_destruct_error_is_returned_not_raised() -> None:
    @injectable()
    class Bad:
        @post_construct
        def ready(self) -> None:
            pass

    container = DIContainer()
    container.register(Bad)

    async def scenario() -> tuple[object, ...]:
        await container.startup()
        return await container.shutdown()

    assert run(scenario()) == ()
