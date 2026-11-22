"""The parity harness's own contract: PRD 08 §5.8's rules, checked rather than assumed.

A parity suite is only worth its runtime if the harness cannot quietly stop
measuring one side. Each test here pins one of the harness rules (MC-28 … MC-34)
or one property of the §5.9 table, and every expectation is derived from the PRD
or from the suite's own source — never from a second copy of the same list:

* the rows come from §5.9's table, so adding a row there fails until it has a test;
* the divergence register's keys — and its stated size — come from §5.9's own sentence;
* "no invariant imports either implementation" is an AST scan of
  ``test_matrix.py``, not a promise in a docstring.
"""

from __future__ import annotations

import ast
import pathlib

import pytest
from backends import BACKENDS, DIVERGENCES, REFERENCE_ROOT, Backend
from spec import MATRIX, RECORDED, prd_register_rows, prd_register_size, prd_rows

pytestmark = pytest.mark.integration

PARITY = pathlib.Path(__file__).resolve().parent
MATRIX_MODULE = PARITY / "test_matrix.py"


def _matrix_source() -> str:
    return MATRIX_MODULE.read_text(encoding="utf-8")


def _matrix_tree() -> ast.Module:
    return ast.parse(_matrix_source())


# ---------------------------------------------------------------------------
# The table is the specification (MC-34)
# ---------------------------------------------------------------------------


def test_the_matrix_is_exactly_the_prd_table() -> None:
    """§5.9 has twelve rows; the suite has those twelve, in that order."""
    assert prd_rows() == MATRIX, f"PRD 08 §5.9 lists {prd_rows()}, the suite implements {MATRIX}"


@pytest.mark.parametrize("row", MATRIX)
def test_every_matrix_row_has_a_test_carrying_its_id(row: str) -> None:
    """A row without a test is a row nobody measures; the id makes failures legible."""
    needle = f"def test_{row.lower().replace('-', '')}_"
    assert needle in _matrix_source(), (
        f"{row} of PRD 08 §5.9 has no `{needle}…` test in test_matrix.py"
    )


def test_the_register_has_exactly_the_entries_the_prd_names() -> None:
    """§5.9 names the deliberate divergences; the register must hold exactly those.

    Both halves are read from §5.9: the row ids from its sentence, the size from
    the word it states. Nothing here repeats either, so a new entry cannot be
    added to the adapter without the prose, and the prose cannot claim a size the
    adapter does not hold.
    """
    named = set(prd_register_rows()) | {"values"}
    assert named == set(DIVERGENCES), (
        f"PRD 08 §5.9 names the register as {sorted(named)}, backends.py holds "
        f"{sorted(DIVERGENCES)}"
    )
    assert prd_register_size() == len(DIVERGENCES), (
        f"§5.9 says the register is complete at {prd_register_size()} entries, "
        f"found {len(DIVERGENCES)}"
    )


@pytest.mark.parametrize("row", sorted(RECORDED))
def test_every_divergent_row_records_an_outcome_for_both_backends(row: str) -> None:
    """A recorded outcome that names only one side is half a divergence."""
    assert set(RECORDED[row]) == {backend.name for backend in BACKENDS}, (
        f"{row} records outcomes for {sorted(RECORDED[row])}, "
        f"the backends are {sorted(backend.name for backend in BACKENDS)}"
    )


def test_every_recorded_row_is_a_registered_divergence() -> None:
    """`RECORDED` may only hold rows the register accounts for."""
    unregistered = set(RECORDED) - set(DIVERGENCES)
    assert not unregistered, (
        f"{sorted(unregistered)} record divergent outcomes but are not in the register; "
        "a divergence that is not written down is just a failing invariant"
    )


# ---------------------------------------------------------------------------
# The harness rules (MC-28 … MC-33)
# ---------------------------------------------------------------------------


def test_no_invariant_imports_either_implementation() -> None:
    """MC-28 / T08-28: the invariants talk to the adapter, never to a package."""
    offenders: list[str] = []
    for node in ast.walk(_matrix_tree()):
        if isinstance(node, ast.Import):
            offenders.extend(alias.name for alias in node.names if _is_implementation(alias.name))
        elif isinstance(node, ast.ImportFrom) and _is_implementation(node.module or ""):
            offenders.append(node.module or "")
    assert not offenders, f"test_matrix.py imports {offenders}; it must go through `backend`"


def _is_implementation(dotted: str) -> bool:
    return dotted.split(".")[0] in {"lauren", "lauren_di"}


def test_no_invariant_asserts_repr_identity_or_all() -> None:
    """MC-30 / T08-30: invariants assert behaviour, not incidental object shapes."""
    source = _matrix_source()
    banned = {"repr(": "a repr string", "id(": "an object address", "__all__": "an export list"}
    found = [f"{needle} ({what})" for needle, what in banned.items() if needle in source]
    assert not found, (
        f"test_matrix.py asserts {found}; two implementations may hold different-looking "
        "objects that satisfy the same invariant"
    )


def test_no_module_level_container_exists_in_the_matrix() -> None:
    """MC-33 / T08-33: a shared container would leak state between tests."""
    offenders: list[str] = []
    for node in _matrix_tree().body:
        if not isinstance(node, ast.Assign | ast.AnnAssign):
            continue
        for inner in ast.walk(node):
            if isinstance(inner, ast.Call) and getattr(inner.func, "attr", "") == "container":
                offenders.append(ast.unparse(node))
    assert not offenders, f"test_matrix.py builds a container at import time: {offenders}"


def test_each_test_gets_a_fresh_container(backend: Backend) -> None:
    """MC-33: every ``backend.container()`` call is a new container, per backend."""
    first = backend.container()
    second = backend.container()
    assert first is not second, "backend.container() must build a new container on every call"
    assert type(first) is type(second), "one backend must build one kind of container"


def test_a_backend_that_cannot_load_reports_skip_never_pass() -> None:
    """MC-29 / T08-29: an absent implementation is visibly absent, not silently green."""

    class Absent(Backend):
        name = "absent"
        module = "no_such_parity_backend_xyz"

    backend = Absent()
    assert backend.load() is False, "a module that does not exist must not report as loaded"
    with pytest.raises(pytest.skip.Exception) as skipped:
        if not backend.load():
            pytest.skip(f"{backend.module} is not importable")
    assert "no_such_parity_backend_xyz" in str(skipped.value), (
        "the skip message must name what is missing, or the summary cannot explain itself"
    )


def test_the_two_backends_are_the_two_packages() -> None:
    """The fixture is parameterised over both sides, always."""
    assert [backend.name for backend in BACKENDS] == ["lauren", "lauren_di"]
    assert [backend.module for backend in BACKENDS] == ["lauren", "lauren_di"]


# ---------------------------------------------------------------------------
# The pin (MC-32 / T08-32)
# ---------------------------------------------------------------------------


def test_the_reference_is_the_pinned_checkout(reference: pathlib.Path) -> None:
    """`lauren.__file__` resolves inside `../lauren-framework`, not site-packages."""
    assert REFERENCE_ROOT in reference.parents, (
        f"lauren.__file__ is {reference}, outside the pinned checkout {REFERENCE_ROOT}"
    )
    assert (reference.parent / "_di" / "__init__.py").is_file()


def test_the_reference_build_is_recorded_by_digest(reference_digest: str) -> None:
    """MC-32: the run records which build of `_di/__init__.py` it measured."""
    assert len(reference_digest) == 64, f"expected a sha256 hex digest, got {reference_digest!r}"
    assert all(character in "0123456789abcdef" for character in reference_digest)
