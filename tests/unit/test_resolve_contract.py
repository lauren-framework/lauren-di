"""The §5.3.3 ``resolve`` contract: signature shape and opaque ``values`` keys.

Baseline B-2 recorded that ``DIContainer.resolve`` annotated ``request_cache``
and ``values`` as ``dict[type, Any]``. That contradicts §5.3.3 (``dict[Any, Any]``
and ``Mapping[Any, Any]``) and §6.3's rule that ``values`` keys are *opaque
type-like tokens* -- a ``Token``, a string or any other hashable object the host
chooses, not only ``type`` objects.

These tests pin the signature to §5.3.3 literally and prove the runtime already
behaves the way the widened annotations claim, so the annotations cannot quietly
narrow back to ``dict[type, Any]``.

The same treatment is applied to ``scope()``: §5.3.3 used to name the
``AsyncContextManager[ResolutionScope]`` protocol while the method said ``Any``,
which is neither. It now says ``ResolutionScope`` -- the class, which *is* its
own context manager -- and the row, the source spelling and both call shapes are
asserted here.

The §5.6 overload pair is asserted the same way, because its shape is what makes
a *checker* catch a misuse rather than a human: the fallback names the two
non-class token kinds (``str`` and ``Token``) instead of ``Any``, so a class
token has exactly one candidate and ````number: int = await
container.resolve(Config)```` is reported instead of silently taking the untyped
variant. That is pinned three ways here -- the source spelling, the §5.6 block
that documents it, and a real ``mypy --strict`` run over a fixture that misuses
the call and over one that reads the ``reveal_type`` notes.
"""

from __future__ import annotations

import ast
import functools
import importlib.util
import inspect
import pathlib
import subprocess
import sys
import textwrap
from collections.abc import Mapping
from types import MappingProxyType
from typing import Annotated, Any, get_type_hints

import pytest
from lauren_di import DIContainer, Inject, ResolutionScope, Token, injectable

from helpers import run

pytestmark = pytest.mark.unit

#: The token used across the ``values`` tests. ``Token`` compares by identity,
#: so the same instance has to reach both the mapping and ``resolve``.
CFG = Token("cfg")


def _signature(func: Any) -> dict[str, inspect.Parameter]:
    return dict(inspect.signature(func).parameters)


#: The repository root, used by the checker run below: ``mypy`` reads
#: ``pyproject.toml`` from its working directory, and ``lauren_di`` must be
#: importable as the session installed it.
REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]

#: PRD 01 is the normative surface; the §5.3.3 rows are read from it, never copied.
PRD_01 = REPO_ROOT / "prds" / "01-product-definition-and-public-api.md"

#: Placeholder for the markdown-escaped ``\|`` inside a table cell.
_PIPE = "\x00"


def _prd_5_3_3_row(member: str) -> str:
    """The signature cell of ``member``'s row in PRD 01 §5.3.3's table.

    Read rather than hardcoded: the point of the test is that the document and
    the source agree, so a copied expectation would let both drift together.
    """
    text = PRD_01.read_text(encoding="utf-8")
    start = text.index("#### 5.3.3")
    end = text.index("#### 5.3.4", start)
    for line in text[start:end].splitlines():
        if not line.startswith("|"):
            continue
        cells = [
            cell.strip().replace(_PIPE, "|")
            for cell in line.replace("\\|", _PIPE).strip().strip("|").split("|")
        ]
        if len(cells) == 3 and cells[0] == f"`{member}`":
            return cells[1].strip("`")
    raise AssertionError(f"PRD 01 §5.3.3 has no row for {member!r}")


def _source_return_annotation(name: str) -> str:
    """``name``'s return annotation exactly as ``_container.py`` spells it."""
    module_path = inspect.getsourcefile(DIContainer)
    assert module_path is not None, "DIContainer has no source file"
    tree = ast.parse(pathlib.Path(module_path).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            assert node.returns is not None, f"{name} has no return annotation"
            return ast.unparse(node.returns)
    raise AssertionError(f"{name} not found in {module_path}")


# ---------------------------------------------------------------------------
# §5.3.3 -- the signature itself
# ---------------------------------------------------------------------------


def test_resolve_signature_matches_prd_5_3_3() -> None:
    """§5.3.3: ``resolve(token, *, request_cache, values, owning_module) -> Any``."""
    params = _signature(DIContainer.resolve)

    assert list(params) == ["self", "token", "request_cache", "values", "owning_module"]

    # Every optional parameter is keyword-only, exactly as the PRD spells it.
    for name in ("request_cache", "values", "owning_module"):
        assert params[name].kind is inspect.Parameter.KEYWORD_ONLY, name
        assert params[name].default is None, name

    hints = get_type_hints(DIContainer.resolve)
    assert hints["token"] is Any
    assert hints["request_cache"] == dict[Any, Any] | None
    assert hints["values"] == Mapping[Any, Any] | None
    assert hints["owning_module"] == type | None
    assert hints["return"] is Any


def _resolve_overload_annotations(
    source: str, class_name: str = "DIContainer"
) -> list[dict[str, str]]:
    """The ``@overload`` variants of ``resolve`` in ``source``, as annotation text.

    ``typing.get_overloads`` is 3.11+, and 3.10's ``typing.overload`` keeps no
    registry at all, so the variants are read from the source with ``ast``. That
    works on every supported interpreter instead of skipping on the oldest one,
    and it asserts the *spelling* the PRD fixes rather than a ``repr`` of an
    evaluated hint. ``source`` is a parameter because the same extraction is run
    over ``_container.py`` and over PRD 01 §5.6's code block, so the document and
    the implementation are compared rather than each compared with a copy.
    """
    tree = ast.parse(source)

    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef) or node.name != class_name:
            continue
        variants: list[dict[str, str]] = []
        for item in node.body:
            if not isinstance(item, ast.AsyncFunctionDef | ast.FunctionDef):
                continue
            if item.name != "resolve":
                continue
            decorated = {
                decorator.id for decorator in item.decorator_list if isinstance(decorator, ast.Name)
            }
            if "overload" not in decorated:
                continue
            variants.append(
                {
                    argument.arg: ast.unparse(argument.annotation)
                    for argument in (*item.args.args, *item.args.kwonlyargs)
                    if argument.annotation is not None
                }
            )
        return variants

    raise AssertionError(f"class {class_name} not found in the source")


def _overload_annotations() -> list[dict[str, str]]:
    """The ``@overload`` variants of ``DIContainer.resolve`` as shipped."""
    module_path = inspect.getsourcefile(DIContainer)
    assert module_path is not None, "DIContainer has no source file"
    return _resolve_overload_annotations(pathlib.Path(module_path).read_text(encoding="utf-8"))


#: The docs contract owns the fence grammar, markers included: a PRD block opens
#: with ```` ```python ```` and may carry `run`, `fragment` or `skip` in its info
#: string. A second regex here would be a second reading of that grammar — the
#: shape goal #50 removed from the corpus — so this reads the one parser.
DOCS_CONTRACT = REPO_ROOT / "scripts" / "check_docs_contract.py"


@functools.lru_cache(maxsize=1)
def _docs_contract() -> Any:
    """``scripts/check_docs_contract.py``, loaded the way its own tests load it."""
    spec = importlib.util.spec_from_file_location("docs_contract_for_resolve_checks", DOCS_CONTRACT)
    assert spec is not None and spec.loader is not None, f"cannot import {DOCS_CONTRACT}"
    module = importlib.util.module_from_spec(spec)
    # Registered before execution: the contract defines frozen dataclasses, and
    # ``dataclasses`` resolves their string annotations through ``sys.modules``.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _prd_5_6_source() -> str:
    """The single fenced python block under PRD 01 §5.6, marker included."""
    text = PRD_01.read_text(encoding="utf-8")
    start = text.index("### 5.6 Typing contract")
    end = text.index("\n## 6.", start)
    contract = _docs_contract()
    blocks = [
        block
        for block in contract.extract_blocks(text[start:end], "PRD 01 §5.6")
        if block.language in contract.PYTHON_LANGUAGES
    ]
    assert len(blocks) == 1, f"§5.6 should hold exactly one python block, found {len(blocks)}"
    return blocks[0].source


def test_resolve_overloads_share_the_widened_parameter_annotations() -> None:
    """The §5.6 overloads must not drift back to ``dict[type, Any]``.

    mypy rejects an overload whose parameters are narrower than the
    implementation, so this also guards the invariant from the other side.
    """
    overloads = _overload_annotations()

    assert len(overloads) == 2, f"expected the §5.6 pair, found {overloads}"
    for hints in overloads:
        assert hints["request_cache"] == "dict[Any, Any] | None"
        assert hints["values"] == "Mapping[Any, Any] | None"
        assert hints["owning_module"] == "type | None"


def test_scope_resolve_takes_only_a_token() -> None:
    """§5.3.3 spells the scope call as ``await scope.resolve(token)``.

    The scope receives its values through ``container.scope(values=...)``, so it
    deliberately has no ``values`` parameter of its own.
    """
    params = _signature(ResolutionScope.resolve)

    assert "values" not in params
    assert list(params) == ["self", "token", "owning_module"]
    assert params["owning_module"].kind is inspect.Parameter.KEYWORD_ONLY


def test_scope_signature_matches_prd_5_3_3() -> None:
    """§5.3.3: ``scope(*, values: Mapping[Any, Any] | None = None) -> ResolutionScope``.

    The return type is the class, not the ``AsyncContextManager[ResolutionScope]``
    protocol the PRD used to name: ``ResolutionScope`` is its own context manager,
    so the protocol spelling stays valid while a caller may also hold the object.
    """
    params = _signature(DIContainer.scope)

    assert list(params) == ["self", "values"]
    assert params["values"].kind is inspect.Parameter.KEYWORD_ONLY
    assert params["values"].default is None

    hints = get_type_hints(DIContainer.scope)
    assert hints["values"] == Mapping[Any, Any] | None
    assert hints["return"] is ResolutionScope


def test_scope_annotation_matches_the_prd_row_in_the_same_words() -> None:
    """The row and the source spell the return type identically.

    §5.3.3's table is the normative statement, so the *spelling* is compared
    rather than only the evaluated hint: ``-> Any`` used to satisfy
    ``get_type_hints`` while telling a caller nothing.
    """
    row = _prd_5_3_3_row("scope")

    assert row == "scope(*, values: Mapping[Any, Any] | None = None) -> ResolutionScope"
    assert _source_return_annotation("scope") == "ResolutionScope"


def test_resolution_scope_implements_the_protocol_the_prd_named() -> None:
    """``ResolutionScope`` structurally satisfies ``AsyncContextManager[ResolutionScope]``.

    This is what makes the narrower annotation safe: ``async with`` needs
    ``__aenter__`` returning the scope and ``__aexit__`` returning ``bool | None``.
    """
    assert get_type_hints(ResolutionScope.__aenter__)["return"] is ResolutionScope
    assert get_type_hints(ResolutionScope.__aexit__)["return"] is bool


def test_scope_result_supports_both_documented_spellings() -> None:
    """Assign the object *and* enter it: §5.3.1's members stay reachable."""

    async def scenario() -> tuple[Any, Any, tuple[Any, ...]]:
        container = DIContainer()

        held = container.scope(values={CFG: "held"})
        assert held.values[CFG] == "held"
        assert held.closed is False
        errors = await held.close()

        async with container.scope(values={CFG: "entered"}) as entered:
            assert entered.values[CFG] == "entered"

        return held, entered, errors

    held, entered, errors = run(scenario())

    assert isinstance(held, ResolutionScope)
    assert isinstance(entered, ResolutionScope)
    assert held.closed is True
    assert entered.closed is True
    assert errors == ()


# ---------------------------------------------------------------------------
# §6.3 -- keys are opaque, type-like tokens
# ---------------------------------------------------------------------------


def test_values_accepts_token_and_non_type_keys() -> None:
    """A ``Token``, a string and a bare ``object`` all work as ``values`` keys."""
    container = DIContainer()
    token_value = object()
    opaque_key = object()
    opaque_value = object()
    values = {CFG: token_value, "dsn": "sqlite:///app.db", opaque_key: opaque_value}

    assert run(container.resolve(CFG, values=values)) is token_value
    assert run(container.resolve("dsn", values=values)) == "sqlite:///app.db"
    assert run(container.resolve(opaque_key, values=values)) is opaque_value


def test_values_accepts_a_read_only_mapping() -> None:
    """``Mapping`` in the annotation is real: a non-``dict`` mapping works.

    ``MappingProxyType`` is a ``Mapping`` but not a ``dict``, so a
    ``dict[type, Any]`` annotation would be a false promise here.
    """
    container = DIContainer()
    sentinel = object()
    values: Mapping[Any, Any] = MappingProxyType({CFG: sentinel})

    assert isinstance(values, Mapping)
    assert not isinstance(values, dict)
    assert run(container.resolve(CFG, values=values)) is sentinel


def test_token_keyed_values_reach_a_constructor_dependency() -> None:
    """The dependency path honours a ``Token`` key, not just a direct lookup."""

    @injectable()
    class NeedsCfg:
        def __init__(self, dsn: Annotated[str, Inject(CFG)]) -> None:
            self.dsn = dsn

    container = DIContainer(value_tokens=[CFG])
    container.register(NeedsCfg)
    container.compile()

    resolved = run(container.resolve(NeedsCfg, values={CFG: "sqlite:///app.db"}))

    assert resolved.dsn == "sqlite:///app.db"


def test_token_keyed_values_reach_a_field_dependency() -> None:
    """The class-body field path honours a ``Token`` key too."""

    @injectable()
    class NeedsCfg:
        dsn: Annotated[str, Inject(CFG)]

    container = DIContainer(value_tokens=[CFG])
    container.register(NeedsCfg)
    container.compile()

    resolved = run(container.resolve(NeedsCfg, values={CFG: "sqlite:///app.db"}))

    assert resolved.dsn == "sqlite:///app.db"


def test_values_are_visible_through_an_explicit_scope() -> None:
    """``container.scope(values=...)`` forwards opaque keys to ``scope.resolve``."""

    async def scenario() -> Any:
        container = DIContainer()
        async with container.scope(values={CFG: "from-scope"}) as scope:
            return await scope.resolve(CFG)

    assert run(scenario()) == "from-scope"


# ---------------------------------------------------------------------------
# §5.6 -- the overload pair, and the asymmetry it exists to close
# ---------------------------------------------------------------------------

#: A misuse fixture: the declared type contradicts the class token. This is the
#: line `mypy --strict` accepted while the fallback overload said ``token: Any``.
_MISUSE_FIXTURE = textwrap.dedent(
    '''
    """Written by tests/unit/test_resolve_contract.py; not part of the suite."""
    from __future__ import annotations

    from lauren_di import DIContainer


    class Config:
        dsn: str = "sqlite:///app.db"


    async def misuse(container: DIContainer) -> None:
        number: int = await container.resolve(Config)
    '''
)

#: The inference fixture: no conflicting context, so the notes are the assertions.
_INFERENCE_FIXTURE = textwrap.dedent(
    '''
    """Written by tests/unit/test_resolve_contract.py; not part of the suite."""
    from __future__ import annotations

    from lauren_di import DIContainer, Token


    class Config:
        dsn: str = "sqlite:///app.db"


    async def shapes(container: DIContainer) -> None:
        reveal_type(await container.resolve(Config))
        reveal_type(await container.resolve("config"))
        reveal_type(await container.resolve(Token("cfg")))
    '''
)


def _run_mypy(tmp_path: pathlib.Path, source: str) -> subprocess.CompletedProcess[str]:
    """Type-check ``source`` with the pinned ``mypy --strict``.

    ``mypy`` arrives with the ``dev`` extra, so the checker this test trusts is
    the checker ``nox -s typing`` runs, not whatever happens to be on ``PATH``.
    """
    fixture = tmp_path / "fixture.py"
    fixture.write_text(source, encoding="utf-8")
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "mypy",
            "--strict",
            "--cache-dir",
            str(tmp_path / ".mypy_cache"),
            str(fixture),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )


def test_the_fallback_overload_cannot_match_a_class_token() -> None:
    """§5.6: the untyped variant names the two *non-class* token kinds.

    ``Any`` would match a class token as well, which is what let mypy take the
    untyped variant on a conflicting assignment; naming ``str | Token`` leaves a
    class token one candidate, so the conflict is reported rather than absorbed.
    """
    overloads = _overload_annotations()

    assert len(overloads) == 2, f"expected the §5.6 pair, found {overloads}"
    assert overloads[0]["token"] == "type[T]"
    assert overloads[1]["token"] == "str | Token"


def test_the_scope_overloads_mirror_the_container_pair() -> None:
    """``ResolutionScope.resolve`` is documented as the same pair (§5.3.3, §5.6).

    A mirror that drifts would re-open the hole through the scope path, where
    §5.2's quickstart resolves request-scoped collaborators.
    """
    module_path = inspect.getsourcefile(ResolutionScope)
    assert module_path is not None, "ResolutionScope has no source file"
    source = pathlib.Path(module_path).read_text(encoding="utf-8")
    scope_overloads = _resolve_overload_annotations(source, "ResolutionScope")

    assert len(scope_overloads) == 2, f"expected the §5.6 pair, found {scope_overloads}"
    assert scope_overloads[0]["token"] == "type[T]"
    assert scope_overloads[1]["token"] == _overload_annotations()[1]["token"]


def test_prd_5_6_documents_the_pair_the_source_ships() -> None:
    """The §5.6 block is normative, so it is compared with the source, not trusted."""
    documented = _resolve_overload_annotations(_prd_5_6_source())

    assert documented == _overload_annotations()
    assert documented[1]["token"] == "str | Token"


def test_mypy_rejects_a_class_token_bound_to_a_conflicting_declared_type(tmp_path) -> None:
    """The misuse the goal names must fail, not merely infer badly.

    With the old ``token: Any`` fallback this fixture type-checked cleanly: mypy
    resolved the overload with ``int`` in view, found the untyped variant
    plausible, and returned ``Any``. pyright rejected the same line, so the gate
    was asymmetric. Both now reject it.
    """
    result = _run_mypy(tmp_path, _MISUSE_FIXTURE)

    assert result.returncode != 0, (
        "mypy accepted `number: int = await container.resolve(Config)`: the §5.6 "
        f"fallback overload is matching a class token again.\n{result.stdout}"
    )
    assert "resolve" in result.stdout, result.stdout
    assert "Config" in result.stdout, result.stdout
    assert "int" in result.stdout, result.stdout


def test_mypy_keeps_the_inference_the_overloads_promise(tmp_path) -> None:
    """The rejection above must not have cost the inference §5.6 promises.

    Three notes, in order: the class token stays ``Config``, and the two
    documented non-class kinds stay ``Any``.
    """
    result = _run_mypy(tmp_path, _INFERENCE_FIXTURE)

    assert result.returncode == 0, result.stdout
    notes = [line for line in result.stdout.splitlines() if "Revealed type is" in line]
    assert len(notes) == 3, notes
    assert notes[0].endswith('"fixture.Config"'), notes
    assert notes[1].endswith('"Any"'), notes
    assert notes[2].endswith('"Any"'), notes
