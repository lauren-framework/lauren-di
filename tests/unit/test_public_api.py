"""§10 row 1 — the public surface is exactly the documented surface.

``__all__`` is a contract, so this test does not restate it. A test that carries
its own copy of the 38 names only proves that two lists in the same repository
agree, and it passes happily while the PRDs and the package drift apart in
lockstep. Instead the expected set is *derived* from the documents that define
it, using the rule PRD 01 §5.3's preamble states (amended so the set is
derivable as written — baseline B-1):

1. every ``Symbol`` row in the §5.3.1–§5.3.5 tables — 25 names. §5.3.3's
   second table lists ``DIContainer`` *members*, not exports, and §5.3.5
   tabulates ``DIError`` only, so the rule keys on the table's header cell
   rather than on position;
2. the 12 leaves of PRD 05 §5.6.1's ``DIError`` hierarchy;
3. ``OverrideError``, the one new leaf in PRD 07 §7.

The union of those three sources is what ``lauren_di.__all__`` must equal. Two
consequences worth knowing:

* a name added to ``__init__.py`` alone fails (it is in no PRD), and a name
  added to a PRD alone fails (it is not exported) — the contract is symmetric;
* the numbers the preamble asserts (``38``, ``25``, ``12``) are checked against
  what the parser actually finds, so the prose cannot drift from its sources.

§5.3.6 is the other half of "no more, no less": it names symbols that are
deliberately *not* exported, and two that are exported despite looking internal.
Both directions are asserted here.
"""

from __future__ import annotations

import ast
import pathlib
import re
import types
import typing
from typing import Any

import lauren_di
import pytest

pytestmark = pytest.mark.unit

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
PRDS = REPO_ROOT / "prds"
INIT = REPO_ROOT / "lauren_di" / "__init__.py"

PRD01 = PRDS / "01-product-definition-and-public-api.md"
PRD05 = PRDS / "05-compile-time-validation-and-errors.md"
PRD07 = PRDS / "07-testing-overrides-and-isolation.md"


# ---------------------------------------------------------------------------
# Reading the PRDs
# ---------------------------------------------------------------------------


def _read(path: pathlib.Path) -> str:
    assert path.is_file(), f"the PRD this contract is derived from is missing: {path}"
    return path.read_text(encoding="utf-8")


def _region(text: str, start: str, end: str) -> str:
    """The text between two headings; both patterns are matched on their own line."""
    begin = re.search(start, text, re.M)
    assert begin is not None, f"heading not found: {start!r}"
    tail = text[begin.end() :]
    stop = re.search(end, tail, re.M)
    assert stop is not None, f"heading not found after {start!r}: {end!r}"
    return tail[: stop.start()]


def _markdown_tables(text: str) -> list[list[list[str]]]:
    """Split a region into tables, each a list of rows, each row a list of cells.

    Cells are split on unescaped pipes only: the signature columns contain
    ``\\|`` inside type expressions (``Sequence[type] \\| None``), which a naive
    ``str.split("|")`` would shred.
    """
    tables: list[list[list[str]]] = []
    current: list[list[str]] = []
    for line in text.splitlines():
        if line.lstrip().startswith("|"):
            cells = [cell.strip() for cell in re.split(r"(?<!\\)\|", line.strip())[1:-1]]
            current.append(cells)
        elif current:
            tables.append(current)
            current = []
    if current:
        tables.append(current)
    return tables


def _symbol_rows(text: str) -> list[str]:
    """Every row of every table whose first header cell is ``Symbol``.

    Keying on the header is what makes §5.3.3's ``Method`` table (23 members,
    no exports) and §5.3.5's one-row table behave the way the preamble says.
    """
    names: list[str] = []
    for table in _markdown_tables(text):
        if not table or not table[0] or table[0][0] != "Symbol":
            continue
        for row in table[2:]:  # header, separator, then the rows
            match = re.fullmatch(r"`([A-Za-z_][A-Za-z0-9_]*)`", row[0])
            assert match is not None, f"unparsable `Symbol` row: {row}"
            names.append(match.group(1))
    return names


def _section_5_3_names() -> list[str]:
    """Source 1: the §5.3.1–§5.3.5 ``Symbol`` rows."""
    body = _region(_read(PRD01), r"^### 5\.3 Exported symbols\s*$", r"^### 5\.4 ")
    names: list[str] = []
    for heading in re.finditer(r"^#### 5\.3\.[1-5] .*$", body, re.M):
        rest = body[heading.end() :]
        following = re.search(r"^#### ", rest, re.M)
        names.extend(_symbol_rows(rest[: following.start()] if following else rest))
    return names


def _prd05_leaves() -> list[str]:
    """Source 2: the leaves drawn in PRD 05 §5.6.1's hierarchy."""
    hierarchy = _region(_read(PRD05), r"^#### 5\.6\.1 Hierarchy\s*$", r"^#### 5\.6\.2 ")
    return re.findall(r"^[├└]── ([A-Za-z_][A-Za-z0-9_]*)$", hierarchy, re.M)


def _prd07_leaves() -> list[str]:
    """Source 3: the exception named by PRD 07 §7's catalogue row."""
    section = _region(_read(PRD07), r"^## 7\. Error Catalog Impact\s*$", r"^## 8\. ")
    table = _markdown_tables(section)[0]
    assert table[0][1] == "Exception", f"PRD 07 §7's table changed shape: {table[0]}"
    names: list[str] = []
    for row in table[2:]:
        match = re.fullmatch(r"`([A-Za-z_][A-Za-z0-9_]*)`", row[1])
        assert match is not None, f"unparsable exception row: {row}"
        names.append(match.group(1))
    return names


def _preamble_counts() -> dict[str, int]:
    """The three counts §5.3's preamble claims, so the prose is checked too."""
    body = _region(_read(PRD01), r"^### 5\.3 Exported symbols\s*$", r"^#### 5\.3\.1 ")
    patterns = {
        "total": r"— (\d+) names, no more, no less",
        "section": r"tables below — (\d+) names",
        "leaves": r"the (\d+) leaves of PRD 05",
    }
    counts: dict[str, int] = {}
    for key, pattern in patterns.items():
        match = re.search(pattern, body)
        assert match is not None, f"§5.3's preamble no longer states its {key} count"
        counts[key] = int(match.group(1))
    return counts


def _section_5_3_6() -> str:
    return _region(_read(PRD01), r"^#### 5\.3\.6 Deliberately not exported\s*$", r"^### 5\.4 ")


def _withheld_names() -> list[str]:
    """The names §5.3.6 says are deliberately *not* exported."""
    first_sentence = _section_5_3_6().strip().split(". ")[0]
    # Drop parentheticals: they discuss ``kind`` strings and attribute names
    # that are not themselves withheld symbols.
    return re.findall(r"`([A-Za-z_][A-Za-z0-9_]*)`", re.sub(r"\([^)]*\)", "", first_sentence))


def _exported_anyway() -> list[str]:
    """The names §5.3.6 says *are* exported despite looking internal."""
    match = re.search(
        r"`([A-Za-z_][A-Za-z0-9_]*)`\s+and\s+`([A-Za-z_][A-Za-z0-9_]*)`\s+\*\*are\*\*\s+exported",
        _section_5_3_6(),
    )
    assert match is not None, "§5.3.6 no longer says which internal-looking names are exported"
    return list(match.groups())


SECTION_5_3: list[str] = _section_5_3_names()
PRD05_LEAVES: list[str] = _prd05_leaves()
PRD07_LEAVES: list[str] = _prd07_leaves()

#: The documented public surface, composed exactly as §5.3's preamble describes.
DOCUMENTED_SURFACE: frozenset[str] = frozenset(SECTION_5_3 + PRD05_LEAVES + PRD07_LEAVES)


# ---------------------------------------------------------------------------
# The set itself
# ---------------------------------------------------------------------------


def test_all_equals_the_documented_surface() -> None:
    """§10 row 1: ``__all__`` equals §5.3 exactly — no more, no less."""
    exported = set(lauren_di.__all__)
    missing = sorted(DOCUMENTED_SURFACE - exported)
    extra = sorted(exported - DOCUMENTED_SURFACE)
    assert not missing and not extra, (
        f"__all__ is missing {missing} (documented, not exported); "
        f"__all__ exports {extra} (exported, documented nowhere)"
    )


def test_all_has_the_documented_number_of_names() -> None:
    counts = _preamble_counts()
    assert len(lauren_di.__all__) == counts["total"]


def test_the_preambles_stated_counts_match_its_sources() -> None:
    """The prose's 38/25/12 must be what the parser actually finds."""
    counts = _preamble_counts()
    assert len(SECTION_5_3) == counts["section"], (
        f"§5.3 says {counts['section']} names, its Symbol tables hold {len(SECTION_5_3)}"
    )
    assert len(PRD05_LEAVES) == counts["leaves"], (
        f"§5.3 says {counts['leaves']} leaves, PRD 05 §5.6.1 draws {len(PRD05_LEAVES)}"
    )
    assert len(DOCUMENTED_SURFACE) == counts["total"], (
        f"§5.3 says {counts['total']} names, the three sources yield {len(DOCUMENTED_SURFACE)}"
    )


def test_the_three_sources_do_not_overlap() -> None:
    """A name documented twice would make the total meaningless."""
    overlaps = sorted(
        (set(SECTION_5_3) & set(PRD05_LEAVES))
        | (set(SECTION_5_3) & set(PRD07_LEAVES))
        | (set(PRD05_LEAVES) & set(PRD07_LEAVES))
    )
    assert overlaps == []


def test_all_is_sorted() -> None:
    """§5.5: normative *and sorted*.

    Ruff ``RUF022`` enforces the same order through isort's natural sort. The
    two orders agree here only because no SCREAMING_CASE name is exported
    (baseline B-17); if one ever is, this assertion and ``RUF022`` will want
    different lists and the choice has to be made deliberately.
    """
    assert list(lauren_di.__all__) == sorted(lauren_di.__all__)


def test_all_has_no_duplicates() -> None:
    assert len(lauren_di.__all__) == len(set(lauren_di.__all__))


def test_all_is_one_literal_with_no_post_hoc_mutation() -> None:
    """The list is a literal in ``__init__.py``, not built by ``append``.

    Baseline B-1: the 38th name arrived through ``__all__.append(...)`` after
    the literal, which no reader and no static tool could see.
    """
    tree = ast.parse(_read(INIT))
    bindings: list[ast.expr] = []
    mutations: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "__all__" for target in node.targets
        ):
            bindings.append(node.value)
        elif (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == "__all__"
        ):
            assert node.value is not None, "__all__ is annotated but never assigned"
            bindings.append(node.value)
        elif isinstance(node, ast.AugAssign) and (
            isinstance(node.target, ast.Name) and node.target.id == "__all__"
        ):
            mutations.append("__all__ += ...")
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "__all__"
        ):
            mutations.append(f"__all__.{node.func.attr}(...)")

    assert mutations == [], f"__all__ is mutated after its literal: {mutations}"
    assert len(bindings) == 1, f"__all__ is assigned {len(bindings)} times"
    literal = bindings[0]
    assert isinstance(literal, ast.List), (
        f"__all__ is a {type(literal).__name__}, not a list literal"
    )

    written = [element.value for element in literal.elts if isinstance(element, ast.Constant)]
    assert len(written) == len(literal.elts), (
        "a non-literal element hides a name from static readers"
    )
    assert written == list(lauren_di.__all__), "the source literal and the runtime list disagree"


# ---------------------------------------------------------------------------
# Every name is reachable, and nothing private leaks
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", sorted(DOCUMENTED_SURFACE))
def test_every_documented_name_imports_from_the_package(name: str) -> None:
    """The statement a user actually writes: ``from lauren_di import X``."""
    namespace: dict[str, Any] = {}
    exec(compile(f"from lauren_di import {name}", "<public-api>", "exec"), namespace)
    assert namespace[name] is getattr(lauren_di, name)


def test_star_import_yields_exactly_the_documented_surface() -> None:
    """``__all__`` governs ``import *``, so the two cannot disagree."""
    namespace: dict[str, Any] = {}
    exec(compile("from lauren_di import *", "<public-api>", "exec"), namespace)
    imported = {key for key in namespace if not key.startswith("__")}
    assert imported == set(lauren_di.__all__)


def test_no_exported_name_is_private() -> None:
    offenders = sorted(name for name in lauren_di.__all__ if name.startswith("_"))
    assert offenders == [], f"private names exported: {offenders}"


def test_section_5_3_6_names_stay_out_of_all() -> None:
    """The "no more" half: §5.3.6's withheld symbols must not be exported."""
    withheld = _withheld_names()
    assert withheld, "no withheld names parsed from §5.3.6"
    leaked = sorted(set(withheld) & set(lauren_di.__all__))
    assert leaked == [], f"§5.3.6 withholds {leaked} but __all__ exports them"


def test_section_5_3_6_names_that_are_exported_are_exported() -> None:
    """The stated exception: two internal-looking names *are* public."""
    declared = _exported_anyway()
    assert declared, "no exported-anyway names parsed from §5.3.6"
    not_exported = sorted(set(declared) - set(lauren_di.__all__))
    assert not_exported == [], f"§5.3.6 declares {not_exported} exported, __all__ disagrees"


# ---------------------------------------------------------------------------
# Declared alias invariants (§5.3.1 ``Depends``, §5.3.2 ``Owner``/``Grant``)
# ---------------------------------------------------------------------------


class _Sample:
    """A class the container never sees; only its identity matters here."""


def test_depends_is_a_spelling_alias_not_a_marker() -> None:
    """§5.3.1: ``Depends[T] is T`` — no marker object, no ``Annotated`` wrapper (D-25)."""
    assert lauren_di.Depends[str] is str
    assert lauren_di.Depends[_Sample] is _Sample
    assert lauren_di.Depends[int] is int

    # A generic alias is not cached by CPython (``list[int] is list[int]`` is
    # False), so identity has to be shown through a held reference.
    generic = list[int]
    assert lauren_di.Depends[generic] is generic
    assert lauren_di.Depends[list[int]] == list[int]

    # ``Depends`` itself is an instance, so subscribing cannot return a marker.
    assert not isinstance(lauren_di.Depends, type)
    assert typing.get_origin(lauren_di.Depends[str]) is None
    assert repr(lauren_di.Depends) == "Depends"


def test_depends_accepts_a_token_no_type_could_name() -> None:
    """§5.5: the spelling is evaluated, so its argument need not be a type at all.

    A function provider is a token (PRD 02 R-23), and it is exactly the case that makes
    a *class* unable to stand in for the alias: ``Depends[make_session]`` is a valid
    expression and an invalid type argument. Runtime identity is the whole contract, so
    it is asserted for the argument a checker could never accept.
    """

    def make_session() -> str:
        return "session"

    assert lauren_di.Depends[make_session] is make_session
    assert typing.get_origin(lauren_di.Depends[make_session]) is None


def test_the_prd_states_that_depends_is_a_runtime_spelling() -> None:
    """§5.5 is the rule a typed codebase follows, so the PRD has to keep stating it.

    The invariant above is only half the contract. §5.4's example annotates the resolved
    type rather than the spelling, and §5.5's bullet is what tells a reader why — including
    that a class cannot stand in for the alias. If the bullet is deleted or softened, the
    next edit writes ``x: Depends[T]`` again and the typing gate rejects the PRD's own
    example, so the wording is checked instead of trusted.
    """
    section = _region(_read(PRD01), r"^### 5\.5 ", r"^### 5\.6 ")
    bullet = next(
        (
            block
            for block in re.split(r"\n(?=- )", section)
            if block.startswith("- **`Depends[T]` is a runtime spelling")
        ),
        None,
    )
    assert bullet is not None, "§5.5 no longer states that `Depends[T]` is a runtime spelling"

    for claim in (
        "runtime spelling",
        "not a type form",
        "not valid as a type",  # mypy's message
        "reportInvalidTypeForm",  # pyright's rule
        "resolved type",  # what a typed codebase writes instead
    ):
        assert claim in bullet, f"§5.5's rule no longer states {claim!r}"


def test_owner_and_grant_are_type_aliases_of_the_documented_shape() -> None:
    """§5.3.2: both are ``TypeAlias`` declarations, not classes.

    The *representation* of a PEP 604 union is not stable across the supported
    interpreters, so the assertion is on the portable part:

    * ``Owner = type | None`` is a ``types.UnionType`` on 3.10–3.13 and a
      ``typing.Union`` on 3.14;
    * ``Grant = Any | tuple[Any, type]`` is already a ``typing.Union`` on 3.10,
      because ``Any | X`` cannot form a ``types.UnionType``; it is a
      ``types.UnionType`` on 3.11–3.13 and reverts on 3.14.

    ``typing.get_origin`` reports whichever family built the object, so the
    union *members* (``typing.get_args``) are what actually pin the documented
    shape — and those are identical on all five interpreters.
    """
    union_families = (types.UnionType, typing.Union)
    for name in ("Owner", "Grant"):
        alias = getattr(lauren_di, name)
        assert not isinstance(alias, type), f"{name} must be an alias, not a class"
        assert typing.get_origin(alias) in union_families, (
            f"{name} is not a union alias: {alias!r} (origin {typing.get_origin(alias)!r})"
        )

    assert typing.get_args(lauren_di.Owner) == (type, type(None))
    assert typing.get_args(lauren_di.Grant) == (typing.Any, tuple[typing.Any, type])
