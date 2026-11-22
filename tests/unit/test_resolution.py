"""Resolution: constructor injection, field injection, providers and value tokens."""

from __future__ import annotations

import dataclasses
import inspect
import pathlib
import re
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


@injectable()
def make_repo_factory() -> Repo:
    return Repo()


@injectable()
class SpelledFunctionField:
    repo: Depends[make_repo_factory]


@injectable()
class AnnotatedFunctionField:
    repo: Annotated[Repo, Inject(make_repo_factory)]


@injectable()
class SpelledTypeField:
    repo: Depends[Repo]


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


def test_a_function_token_field_needs_annotated_inject() -> None:
    """The field form the container's docstring shows is the one that injects.

    PRD 01 §5.5 and PRD 02 §5.6 together: ``Depends[T]`` is a runtime spelling, and a
    field is collected only when its annotation names a type. So
    ``repo: Depends[make_repo_factory]`` is *not* a dependency — the subscript evaluates
    to the function, which is not a type, and the gate skips the field silently —
    while ``Annotated[Repo, Inject(make_repo_factory)]`` resolves the same token and
    keeps ``Repo`` for checkers. ``lauren_di/_container.py``'s "Dependency declaration
    styles" and "Function injectables" examples now show that form, so it is pinned here
    instead of trusted. The classes are module-level so the deferred annotations
    (``from __future__ import annotations``) can be evaluated, which is what the
    container does.
    """
    container = DIContainer()
    container.register(Repo)
    container.register(make_repo_factory)
    spelled_provider = container.register(SpelledFunctionField)
    annotated_provider = container.register(AnnotatedFunctionField)

    # The spelling *is* the function once the stored string is evaluated.
    evaluated = inspect.get_annotations(SpelledFunctionField, eval_str=True)
    assert evaluated["repo"] is make_repo_factory

    # A function is not a type, so gate 4/6 drops the field: no dependency, no error.
    assert spelled_provider.field_deps == ()
    assert not hasattr(run(container.resolve(SpelledFunctionField)), "repo")

    # The Annotated form carries the token and keeps the annotated type.
    assert annotated_provider.field_deps == (("repo", make_repo_factory),)
    assert isinstance(run(container.resolve(AnnotatedFunctionField)).repo, Repo)


def test_a_type_behind_the_spelling_is_still_a_field_dependency() -> None:
    """The gate reads the annotation's *token*, not whether the spelling was used.

    The other half of :func:`test_a_function_token_field_needs_annotated_inject`: the same
    ``Depends[...]`` spelling injects when the subscript is a type and is skipped when it is
    a function, because ``Depends[T] is T`` (PRD 01 §5.5, D-25) and gate 6 asks whether the
    token *looks injectable*. Without this case a future edit could skip every ``Depends[...]``
    field and the function-token assertion would still pass.
    """
    container = DIContainer()
    container.register(Repo)
    provider = container.register(SpelledTypeField)

    # The spelling evaluates to the class, which is the token the gate then judges.
    evaluated = inspect.get_annotations(SpelledTypeField, eval_str=True)
    assert evaluated["repo"] is Repo
    assert provider.field_deps == (("repo", Repo),)
    assert isinstance(run(container.resolve(SpelledTypeField)).repo, Repo)


# ---------------------------------------------------------------------------
# The rule, in the corpus: shape (b) is documented and never contradicted
# ---------------------------------------------------------------------------

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]

#: A ``Depends[...]`` whose argument is not a type. Every type in this corpus is CamelCase,
#: so a lower-case argument is a function (or another value) token — exactly the case the
#: field gate skips (PRD 02 §5.6 gates 4/6).
_FUNCTION_TOKEN = re.compile(r"Depends\[\s*([a-z_][A-Za-z0-9_]*)\s*\]")

#: A sentence that says the container *does* populate the field. Bare ``inject`` is excluded
#: on purpose: ``Inject`` is a public name, not a promise, and ``injects=`` is a keyword.
_INJECTION_PROMISE = re.compile(
    r"\b(?:is|are|gets?)\s+injected\b|#\s*injected\b",
    re.IGNORECASE,
)

#: Where the rule must keep being stated, as (path, start, end, required substrings).
#: ``start``/``end`` are the heading regexes that bound the section; ``None`` means the
#: whole file. The substrings encode the *ideas* — the function spelling, the skip, and the
#: typed spelling — rather than a sentence, so re-wording passes and re-promising does not.
_FIELD_RULE = (
    (
        "prds/01-product-definition-and-public-api.md",
        r"^### 5\.5 ",
        r"^### 5\.6 ",
        ("`session: Depends[make_session]`", "Annotated[Session, Inject(make_session)]"),
    ),
    (
        "prds/02-core-container-and-provider-registration.md",
        r"^### 5\.6 ",
        r"^### 5\.7 ",
        (
            "`Depends[make_clock]`",
            "skipped **silently**",
            "`Annotated[Clock, Inject(make_clock)]`",
        ),
    ),
    (
        "prds/02-core-container-and-provider-registration.md",
        r"^### 5\.7 ",
        r"^### 5\.8 ",
        ("`Depends[make_session]`", "skipped", "`Annotated[Session, Inject(make_session)]`"),
    ),
    (
        "prds/04-custom-providers-tokens-and-multi-bindings.md",
        r"^### 5\.9 ",
        r"^### 5\.10 ",
        ("skip it silently", "`Annotated[T, Inject(fn)]`"),
    ),
    (
        "prds/08-framework-compatibility-and-migration.md",
        None,
        None,
        ("`Annotated[Repo, Inject(fn)]`",),
    ),
    (
        "docs/guides/providers.md",
        None,
        None,
        ("is skipped by the gates below", "`Annotated[Session, Inject(make_session)]`"),
    ),
)


def _corpus_files() -> list[pathlib.Path]:
    """Every page a reader is taught from, minus the evidence that quotes old defects.

    ``prds/evidence/**`` is excluded deliberately: those files record what was wrong
    (goal #41's checker had to make the same distinction), so a quoted bad sentence there
    is a citation, not a promise.
    """
    files = [REPO_ROOT / name for name in ("README.md", "llms.txt", "llms-full.txt")]
    files.extend(sorted((REPO_ROOT / "docs").rglob("*.md")))
    files.extend(
        sorted(
            path
            for path in (REPO_ROOT / "prds").rglob("*.md")
            if "evidence" not in path.relative_to(REPO_ROOT / "prds").parts
        )
    )
    return [path for path in files if path.is_file()]


def _region(text: str, start: str | None, end: str | None) -> str:
    """The slice of ``text`` between two heading regexes, or all of it."""
    if start is None or end is None:
        return text
    opening = re.search(start, text, re.MULTILINE)
    assert opening is not None, f"section heading {start!r} is gone"
    closing = re.search(end, text[opening.end() :], re.MULTILINE)
    assert closing is not None, f"section heading {end!r} is gone"
    return text[opening.end() : opening.end() + closing.start()]


def test_every_document_still_states_the_field_gate_for_a_function_token() -> None:
    """Shape (b) is only a decision if the documents keep saying so.

    PRD 02 §5.6, §5.7, PRD 04 §5.9, PRD 08's P-5 row, PRD 01 §5.5 and the shipped guide
    each promised (or now explain) what happens to ``clock: Depends[make_clock]``. Deleting
    the explanation is as bad as contradicting it: a reader who is not told will write the
    spelling in annotation position and lose the dependency silently, which is the whole
    defect. So the rule is asserted where it lives, in each document's own words.
    """
    missing: list[str] = []
    for name, start, end, required in _FIELD_RULE:
        path = REPO_ROOT / name
        assert path.is_file(), f"the document this rule lives in is gone: {name}"
        body = _region(path.read_text(encoding="utf-8"), start, end)
        missing.extend(
            f"{name}: no longer states {claim!r}" for claim in required if claim not in body
        )

    assert missing == [], (
        "the field gate for a function token is no longer documented (PRD 02 §5.6): "
        + "; ".join(missing)
    )


def test_no_document_promises_injection_for_a_non_type_field_annotation() -> None:
    """The falsifier: re-promising injection must be a red gate, not a silent regression.

    The defect this goal reconciles was one line — ``clock: Depends[make_clock]  # injected:
    explicit dependency marker`` — and it survived because nothing read the prose. A
    ``Depends[...]`` whose argument is not a type is skipped by gates 4/6, so any page that
    pairs the two is wrong whichever direction the edit comes from. If a sentence is about a
    *different* field, reword it so the spelling and the claim are not on one line.
    """
    offenders: list[str] = []
    for path in _corpus_files():
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if _FUNCTION_TOKEN.search(line) and _INJECTION_PROMISE.search(line):
                offenders.append(
                    f"{path.relative_to(REPO_ROOT).as_posix()}:{number}: {line.strip()}"
                )

    assert offenders == [], (
        "a document promises that a non-type field annotation is injected; it is skipped "
        f"silently by PRD 02 §5.6's gates 4/6 (write `Annotated[T, Inject(token)]`): {offenders}"
    )


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
