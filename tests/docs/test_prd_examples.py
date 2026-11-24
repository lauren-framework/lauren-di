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
import pathlib
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

#: The whole corpus: every PRD page, every python block. Compilation and marker
#: discipline belong to ``prds/check_consistency.py``'s ``blocks`` check
#: (``nox -s prds``); *execution* is this module's half, so a ``run`` marker
#: cannot be a promise nobody keeps — which is what it was for the 65 blocks
#: outside the two sections this harness used to name.
CORPUS_PAGES = prd_examples.PRD_PAGES
CORPUS = {page.name: prd_examples.collect_page(page) for page in CORPUS_PAGES}
EXPECTED_CORPUS_PAGES = 14
EXPECTED_CORPUS_BLOCKS = 118
EXPECTED_CORPUS_RUN_BLOCKS = 31
EXPECTED_CORPUS_UNMARKED = 0

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


def test_the_corpus_census_is_the_one_this_module_executes(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The corpus's shape, pinned: 118 blocks over 14 pages, 31 of them executed.

    The census is the claim this module makes about itself — every ``run`` block
    in every PRD executes here, and nothing else is expected to. ``prds/
    check_consistency.py`` reports the same split as an ``ok`` line, so a page
    that gains or loses blocks is visible in both gates.
    """
    report = prd_examples.corpus_inventory()
    print(report)

    assert capsys.readouterr().out.strip() == report
    assert len(CORPUS_PAGES) == EXPECTED_CORPUS_PAGES
    assert sum(len(examples) for examples in CORPUS.values()) == EXPECTED_CORPUS_BLOCKS
    assert (
        sum(
            1
            for examples in CORPUS.values()
            for example in examples
            if prd_examples.is_runnable(example)
        )
        == EXPECTED_CORPUS_RUN_BLOCKS
    )
    assert report == (
        "118 python block(s) in 14 page(s): 31 marked run, 87 fragment/skip, 0 unmarked"
    )


def test_no_prd_block_is_unmarked() -> None:
    """A block with no marker is one no gate has decided about.

    The marker is the decision: ``run`` (executed here) or ``fragment``/``skip``
    (deliberately partial, compiled only). Leaving it off used to be the third,
    silent option, which is how 65 blocks — including three complete programs
    with ``asyncio.run(main())`` — went unexecuted for the life of the corpus.
    """
    unmarked = [
        example.location
        for examples in CORPUS.values()
        for example in examples
        if not example.markers
    ]

    assert len(unmarked) == EXPECTED_CORPUS_UNMARKED, (
        "PRD blocks with no `run`/`fragment` marker, so nothing decides whether they "
        f"should execute: {unmarked}"
    )


@pytest.mark.parametrize("page", CORPUS_PAGES, ids=lambda page: page.name)
def test_every_run_marked_block_in_the_page_executes(page: pathlib.Path) -> None:
    """Every ``run`` block in the corpus runs, per page, in file order.

    Corpus-wide on purpose: before this test the ``run`` marker meant something
    only inside PRD 01 §5.4 and PRD 03 §5.3, so a ``run`` block in PRD 04-08
    would have been a promise no gate kept. A page's blocks share one namespace
    in file order (PRD 01 §5.4's rule), seeded with PRD 01's stand-ins and
    otherwise empty.
    """
    namespace = prd_examples.page_namespace(page)
    executed: list[int] = []

    for example in prd_examples.collect_page(page):
        if not prd_examples.is_runnable(example):
            continue
        prd_examples.execute(example, namespace)
        executed.append(example.line)

    assert executed == sorted(executed), f"{page.name} `run` blocks did not execute in file order"


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


#: §2's G8 row — "Give every public name one canonical example".
_G8_ROW = re.compile(r"^\|\s*G8\s*\|[^|]*\|(.+?)\|\s*$", re.MULTILINE)
#: §10's `Examples` row, whose Location cell names the module that implements it.
_EXAMPLES_ROW = re.compile(r"^\|\s*Examples\s*\|([^|]*)\|", re.MULTILINE)


def test_the_goal_row_points_at_the_test_that_implements_it() -> None:
    """G8 named a document that cannot exist; it must name §10 row 3's test.

    The row read "a tested example in PRD 10's docs contract". The corpus is
    01–08 and the owner forbids a 09, so PRD 10 is not a document a reader can
    open — and because the citation carried no `§`, no check ever resolved it
    either (goal #41). Both halves are pinned here: the row names the location
    §10 gives, and every `PRD NN` it cites is a file on disk.
    """
    text = prd_examples.PRD_PATH.read_text(encoding="utf-8")
    rows = _G8_ROW.findall(text)
    assert len(rows) == 1, f"§2 should state G8 once; found {len(rows)}"
    cell = rows[0].strip()

    where = _EXAMPLES_ROW.findall(text)
    assert len(where) == 1, f"§10 should have one Examples row; found {len(where)}"
    implementing = where[0].strip().strip("`")

    assert "§10 row 3" in cell, f"G8 does not name the location §10 gives: {cell}"
    assert implementing in cell, (
        f"G8 does not name the module §10 row 3 names ({implementing}): {cell}"
    )

    cited = {f"{int(number):02d}" for number in re.findall(r"PRD\s+(\d+)", cell)}
    on_disk = {path.name[:2] for path in prd_examples.PRD_PATH.parent.glob("[0-9][0-9]-*.md")}
    assert cited <= on_disk, (
        f"G8 cites PRD(s) {sorted(cited - on_disk)}, which are not files in prds/: {cell}"
    )


def test_the_goal_row_uses_the_acceptance_row_s_words() -> None:
    """One rule, two places: G8 and §10.1 must not describe the shape differently.

    §10.1 owns the statement of the rule, G8 owns the promise it backs. They are
    prose, so nothing but this test compares them — and a row that paraphrases
    the other is how the two drifted into "one subsection per symbol" while §5.4
    had 22 subsections for 38 exports (goal #25).
    """
    text = prd_examples.PRD_PATH.read_text(encoding="utf-8")
    goal = _G8_ROW.findall(text)
    acceptance = _ROW.findall(text)
    assert len(goal) == 1 and len(acceptance) == 1

    # §10.1 states the location, an em dash, then the rule itself.
    rule = acceptance[0].split("—")[-1].strip()
    assert rule, f"§10.1 states no rule to compare against: {acceptance[0]!r}"
    assert rule in goal[0], (
        "G8 and §10.1 describe the example rule in different words:\n"
        f"  §10.1: {rule}\n"
        f"  G8:    {goal[0].strip()}"
    )
