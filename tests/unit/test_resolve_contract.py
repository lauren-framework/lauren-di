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
"""

from __future__ import annotations

import ast
import inspect
import pathlib
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


#: PRD 01 is the normative surface; the §5.3.3 rows are read from it, never copied.
PRD_01 = (
    pathlib.Path(__file__).resolve().parents[2] / "prds" / "01-product-definition-and-public-api.md"
)

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


def _overload_annotations() -> list[dict[str, str]]:
    """The ``@overload`` variants of ``DIContainer.resolve``, as annotation text.

    ``typing.get_overloads`` is 3.11+, and 3.10's ``typing.overload`` keeps no
    registry at all, so the variants are read from the source with ``ast``. That
    works on every supported interpreter instead of skipping on the oldest one,
    and it asserts the *spelling* the PRD fixes rather than a ``repr`` of an
    evaluated hint.
    """
    module_path = inspect.getsourcefile(DIContainer)
    assert module_path is not None, "DIContainer has no source file"
    tree = ast.parse(pathlib.Path(module_path).read_text(encoding="utf-8"))

    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef) or node.name != DIContainer.__name__:
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

    raise AssertionError(f"class {DIContainer.__name__} not found in {module_path}")


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
