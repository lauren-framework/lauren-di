"""§10 row 3: every PRD 01 §5.4 block compiles, and the ``run`` ones execute.

The section is the product's canonical example set, so a block that stops
compiling is a defect in the PRD and a block that stops *working* is a defect
in the library. Both are cheap to catch here:

* every fenced python block under §5.4 compiles with the same flags the docs
  contract uses (top-level ``await`` allowed, annotations deferred);
* the blocks marked ``run`` execute in file order in one shared namespace, so
  later blocks may use the imports and declarations of earlier ones;
* every ``####`` subsection contributes at least one checked block, so no
  example can be deleted without its behaviour losing coverage.

The marker grammar (``run``, ``fragment``/``skip``) is shared with
``scripts/check_docs_contract.py``; the synthetic blocks below prove the
grammar is what decides execution, so a section that happened to mark every
block ``run`` cannot make the selector vacuous.
"""

from __future__ import annotations

import ast
import asyncio
import re

import lauren_di
import prd_examples
import pytest

pytestmark = pytest.mark.unit

EXAMPLES = prd_examples.collect()
SUBSECTIONS = prd_examples.subsections()
CHECKED = [example for example in EXAMPLES if prd_examples.is_checked(example)]
RUNNABLE = [example for example in CHECKED if prd_examples.is_runnable(example)]

#: §5.4 currently has one ``####`` subsection per example group, and 22 blocks.
#: These numbers are pinned: deleting a subsection (and with it the only test of
#: its behaviour) must be a deliberate PRD change, not a silent one.
EXPECTED_SUBSECTIONS = 22
EXPECTED_BLOCKS = 22
EXPECTED_RUN_BLOCKS = 22

#: A miniature section exercising every marker the contract understands.
SYNTHETIC = "\n".join(
    [
        "#### compiled only",
        "",
        "```python",
        "compiled_only = True",
        "```",
        "",
        "#### executed",
        "",
        "```python run",
        "executed = True",
        "```",
        "",
        "#### partial",
        "",
        "```python fragment",
        "this is not python (",
        "```",
        "",
    ]
)


def test_section_5_4_has_the_expected_shape() -> None:
    assert SUBSECTIONS, "PRD 01 §5.4 has no `####` subsections"
    assert len(SUBSECTIONS) == EXPECTED_SUBSECTIONS, (
        f"PRD 01 §5.4 now has {len(SUBSECTIONS)} subsections, not {EXPECTED_SUBSECTIONS}: "
        "update this contract deliberately when the section grows or shrinks"
    )
    assert len(EXAMPLES) == EXPECTED_BLOCKS


@pytest.mark.parametrize("example", CHECKED, ids=[example.location for example in CHECKED])
def test_every_block_compiles(example: prd_examples.Example) -> None:
    prd_examples.compile_example(example)


def test_every_subsection_contributes_a_checked_block() -> None:
    by_heading: dict[str, list[prd_examples.Example]] = {}
    for example in CHECKED:
        by_heading.setdefault(example.heading, []).append(example)

    missing = [title for title in SUBSECTIONS if title not in by_heading]
    assert not missing, f"§5.4 subsections with no checked python block: {missing}"

    orphans = sorted(set(by_heading) - set(SUBSECTIONS))
    assert not orphans, f"blocks outside any §5.4 subsection: {orphans}"


def test_inventory_is_reported(capsys: pytest.CaptureFixture[str]) -> None:
    report = prd_examples.inventory()
    print(report)

    assert capsys.readouterr().out.strip() == report
    assert report == (
        "22 block(s) collected, 22 checked, 22 marked run, across 22 of 22 subsection(s)"
    )


def _error_leaves() -> set[str]:
    """The ``DIError`` subclasses in ``__all__`` — the one family allowed to share a block."""
    return {
        name
        for name in lauren_di.__all__
        if name != "DIError"
        and isinstance(getattr(lauren_di, name), type)
        and issubclass(getattr(lauren_di, name), lauren_di.DIError)
    }


def _covered_by_heading(name: str) -> bool:
    """Whether some ``####`` heading names ``name``.

    Headings wrap names in backticks, so only word characters are excluded at the
    edges — that still keeps ``Provider`` from matching inside ``ProviderOverride``
    and ``Scope`` from matching inside ``ResolutionScope``.
    """
    headings = "\n".join(SUBSECTIONS)
    return re.search(rf"(?<!\w){re.escape(name)}(?!\w)", headings) is not None


def _referenced_names(source: str) -> set[str]:
    """Identifiers a block actually uses, excluding what it merely imports.

    ``from lauren_di import DestructError`` mentions the name but demonstrates
    nothing about it, so import aliases are deliberately not collected: only
    ``ast.Name`` nodes count. That is what makes "covered by a grouped example"
    mean *used* in the group rather than *listed at the top of it*.
    """
    return {node.id for node in ast.walk(ast.parse(source)) if isinstance(node, ast.Name)}


def test_every_exported_name_has_an_example() -> None:
    """G8's "one canonical example per public name", derived rather than assumed.

    The claim in §2 G8 and §10.1 is that *every* symbol has an example, so the
    expectation is read from ``lauren_di.__all__`` instead of from a list kept by
    hand — a new export with no example fails here, which is the only way the
    claim stays true as the surface grows.

    Two shapes satisfy it, and which one a name gets is decided by the code:

    * a ``####`` heading that names it — the one-symbol-per-example rule as
      written;
    * a **grouped** example, which is what the thirteen error leaves use: they
      differ only by ``code``, so thirteen near-identical blocks would be noise.
      A name qualifies for that exemption only by being a ``DIError`` subclass,
      and the whole family then has to appear in one shared block, so a leaf
      added to ``__all__`` without being added to the group still fails.
    """
    error_leaves = _error_leaves()

    # Every non-error name must own a heading.
    headless = [
        name
        for name in lauren_di.__all__
        if name not in error_leaves and not _covered_by_heading(name)
    ]
    assert not headless, f"exported names with no §5.4 subsection of their own: {headless}"

    # Every error leaf must be *used* in some block, and the whole family must
    # share one -- that shared block *is* the documented grouping. Usage is read
    # from the AST's ``Name`` nodes rather than from the text, because a name that
    # is merely imported (``from lauren_di import DestructError``) is not an
    # example of it; dropping it from the list while leaving the import behind
    # must still fail.
    appearances: dict[str, set[str]] = {}
    for leaf in sorted(error_leaves):
        blocks = {
            example.heading for example in CHECKED if leaf in _referenced_names(example.source)
        }
        assert blocks, f"error leaf `{leaf}` is used in no §5.4 block"
        appearances[leaf] = blocks

    grouped = set.intersection(*appearances.values()) if appearances else set()
    assert grouped, (
        "the error leaves are not covered by one shared grouped example; "
        f"per-leaf blocks: { {k: sorted(v) for k, v in appearances.items()} }"
    )


#: §10.1's traceability row for the example rule. It is prose, so nothing executes
#: it unless this test does.
_ROW = re.compile(r"^\|\s*One usage example per named symbol\s*\|(.+?)\|\s*$", re.MULTILINE)


def test_no_section_5_4_block_puts_depends_in_annotation_position() -> None:
    """§5.5: ``Depends[T]`` is a runtime spelling, so no checker accepts it as a type.

    §5.4 is the section the typing gate type-checks (§10's Typing row), so a block that
    annotates ``session: Depends[make_session]`` makes the document's own example
    untypable — mypy reports ``Variable "Depends" is not valid as a type`` and pyright
    ``Variable not allowed in type expression``. The spelling belongs where it is
    *evaluated* (``assert Depends[X] is X``), which is how §5.4 now writes it; this test
    keeps it that way, in both directions of the same rule §5.5 states.
    """
    offenders = [
        f"{example.location}:{line} {text}"
        for example in EXAMPLES
        for line, text in _depends_in_annotation_position(example.source)
    ]

    assert offenders == [], (
        "§5.4 puts `Depends[...]` in annotation position, which mypy and pyright both reject "
        f"(§5.5): {offenders}"
    )


def _depends_in_annotation_position(source: str) -> list[tuple[int, str]]:
    """Every ``Depends[...]`` used where a checker reads a *type*, with its line number."""
    tree = compile(
        source,
        "<§5.4 block>",
        "exec",
        flags=ast.PyCF_ONLY_AST | ast.PyCF_ALLOW_TOP_LEVEL_AWAIT,
        dont_inherit=True,
    )
    found: list[tuple[int, str]] = []

    def collect(annotation: ast.expr | None) -> None:
        for node in ast.walk(annotation) if annotation is not None else ():
            if (
                isinstance(node, ast.Subscript)
                and isinstance(node.value, ast.Name)
                and node.value.id == "Depends"
            ):
                found.append((node.lineno, ast.unparse(node)))

    for node in ast.walk(tree):
        if isinstance(node, ast.AnnAssign):
            collect(node.annotation)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            collect(node.returns)
            arguments = [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]
            for argument in [*arguments, node.args.vararg, node.args.kwarg]:
                if argument is not None:
                    collect(argument.annotation)

    return found


def test_the_acceptance_row_states_the_rule_the_section_follows() -> None:
    """§10.1's row must not claim a shape §5.4 does not have.

    The row is the acceptance criterion a reader follows, and unlike the section
    itself nothing executes it: it said "one subsection per symbol" while §5.4 had
    22 subsections for 38 exports — the one claim in the document no test could
    falsify. So the claim is checked against the section's measured shape instead
    of trusted, and both directions are falsifiable:

    * a strict one-to-one claim is only allowed if the section really has one
      subsection per export;
    * otherwise the row must name the grouped exemption, and that exemption must
      be one the section still needs — if every export owned a heading, claiming
      a grouping would be the new false statement.

    Both branches read ``lauren_di.__all__`` and the parsed subsections, so neither
    can drift into agreement with a hardcoded copy of the shape.
    """
    text = prd_examples.PRD_PATH.read_text(encoding="utf-8")
    rows = _ROW.findall(text)
    assert len(rows) == 1, f"§10.1 should state the example rule once; found {len(rows)}"
    where = rows[0].strip()

    if "one subsection per symbol" in where.lower():
        assert len(SUBSECTIONS) == len(lauren_di.__all__), (
            f"§10.1 claims one subsection per symbol, but §5.4 has {len(SUBSECTIONS)} "
            f"subsections for {len(lauren_di.__all__)} exports"
        )
    else:
        assert "group" in where.lower(), (
            "§10.1 neither claims one subsection per symbol nor names the grouped "
            f"exemption §5.4 uses: {where}"
        )
        headless = [name for name in lauren_di.__all__ if not _covered_by_heading(name)]
        assert headless, (
            "§10.1 names a grouped exemption, but every export already owns a §5.4 "
            "heading — the claim and the section disagree in the other direction"
        )


def test_run_marked_blocks_execute_in_file_order() -> None:
    namespace = prd_examples.seed_namespace()
    executed: list[int] = []

    assert len(RUNNABLE) == EXPECTED_RUN_BLOCKS
    for example in RUNNABLE:
        prd_examples.execute(example, namespace)
        executed.append(example.line)

    assert executed == sorted(executed), "§5.4 blocks did not execute in file order"


def test_marker_grammar_selects_what_runs() -> None:
    examples = prd_examples.collect(SYNTHETIC, path="synthetic.md")

    assert [example.heading for example in examples] == [
        "compiled only",
        "executed",
        "partial",
    ]
    assert [example.source.strip() for example in examples if prd_examples.is_checked(example)] == [
        "compiled_only = True",
        "executed = True",
    ]
    assert [
        example.source.strip() for example in examples if prd_examples.is_runnable(example)
    ] == ["executed = True"]


def test_fragment_block_is_exempt_only_because_it_is_marked() -> None:
    fragment = prd_examples.collect(SYNTHETIC, path="synthetic.md")[-1]

    assert not prd_examples.is_checked(fragment)
    # The exemption is load-bearing: the block really is not valid python.
    with pytest.raises(SyntaxError):
        prd_examples.compile_example(fragment)


def test_top_level_await_is_driven_by_asyncio_run() -> None:
    text = "\n".join(
        [
            "#### await",
            "",
            "```python run",
            "await asyncio.sleep(0)",
            "slept = True",
            "```",
            "",
        ]
    )
    (example,) = prd_examples.collect(text, path="synthetic.md")
    namespace: dict[str, object] = {"asyncio": asyncio}

    prd_examples.execute(example, namespace)

    assert namespace["slept"] is True


def test_run_failure_names_the_block_location() -> None:
    text = "\n".join(["#### broken", "", "```python run", 'raise RuntimeError("boom")', "```", ""])
    (example,) = prd_examples.collect(text, path="synthetic.md")

    with pytest.raises(AssertionError) as excinfo:
        prd_examples.execute(example, {})

    assert "synthetic.md:" in str(excinfo.value)
    assert "boom" in str(excinfo.value)


def test_compile_failure_carries_the_block_location() -> None:
    text = "\n".join(["#### broken", "", "```python", "def broken(:", "```", ""])
    (example,) = prd_examples.collect(text, path="synthetic.md")

    with pytest.raises(SyntaxError) as excinfo:
        prd_examples.compile_example(example)

    assert str(excinfo.value.filename).startswith("synthetic.md:")
