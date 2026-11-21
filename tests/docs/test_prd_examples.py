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

import asyncio

import prd_examples
import pytest

pytestmark = pytest.mark.unit

EXAMPLES = prd_examples.collect()
SUBSECTIONS = prd_examples.subsections()
CHECKED = [example for example in EXAMPLES if prd_examples.is_checked(example)]
RUNNABLE = [example for example in CHECKED if prd_examples.is_runnable(example)]

#: §5.4 currently has one ``####`` subsection per example group, and 18 blocks.
#: These numbers are pinned: deleting a subsection (and with it the only test of
#: its behaviour) must be a deliberate PRD change, not a silent one.
EXPECTED_SUBSECTIONS = 18
EXPECTED_BLOCKS = 18
EXPECTED_RUN_BLOCKS = 18

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
        "18 block(s) collected, 18 checked, 18 marked run, across 18 of 18 subsection(s)"
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
