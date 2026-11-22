"""PRD 03 §5.3's scope example *runs*, and its surface table matches the class.

The defect this module exists to prevent was invisible to every gate: PRD 03
§5.3 documented `ResolutionScope.active` and `ResolutionScope.teardown_errors`,
and its runnable example asserted `scope.teardown_errors == ()`, while the
shipped class exposes `closed` and `errors`. The block compiled — it is valid
Python — and `scripts/check_docs_contract.py` compiles blocks, so nothing ever
called it; only a reader who typed it out met `AttributeError`.

Two checks close that, and they cover different halves:

* **the block executes.** ``python run`` on the fence means
  :func:`prd_examples.execute` calls it, so a member that does not exist raises
  where the example is written rather than where a user is.
* **the table is compared to the class.** The member names in §5.3's surface
  table must equal the public attributes `dir(ResolutionScope)` reports, so a
  rename in either direction is a failure even if no block happens to use that
  member.

The `scope.<name>` prose check covers the third way in: a sentence that names a
member without a block behind it.
"""

from __future__ import annotations

import re

import prd_examples
import pytest
from lauren_di import ResolutionScope

pytestmark = pytest.mark.unit

SECTION = prd_examples.PRD_03_SECTION_5_3
EXAMPLES = prd_examples.collect_section(SECTION)
CHECKED = [example for example in EXAMPLES if prd_examples.is_checked(example)]
RUNNABLE = [example for example in CHECKED if prd_examples.is_runnable(example)]

#: §5.3 currently carries one 32-line example, marked ``run``. Pinned so
#: deleting it (and with it the only execution of the scope surface) is a
#: deliberate PRD change rather than a silent one.
EXPECTED_BLOCKS = 1
EXPECTED_RUN_BLOCKS = 1

#: The heading that introduces the surface table.
SURFACE_HEADING = "`ResolutionScope` surface:"

#: ``scope.resolve``, ``scope.errors`` … in prose: every member §5.3 claims.
#: The lookbehind drops ``consumer.scope.label`` (the ``Scope`` enum's label,
#: not a scope object) and the lookahead drops a page name such as
#: ``docs/reference/scope.md``.
SCOPE_MEMBER_RE = re.compile(r"(?<![\w.])scope\.(?!md\b)([a-z_][a-z0-9_]*)")

#: Public members the class actually ships.
SHIPPED = frozenset(name for name in dir(ResolutionScope) if not name.startswith("_"))
#: The two dunder names the table documents as the context-manager pair.
CONTEXT_MANAGER = frozenset({"__aenter__", "__aexit__"})


def surface_table() -> dict[str, str]:
    """The §5.3 surface table as ``member -> contract cell``.

    A cell may name a pair (``__aenter__`` / ``__aexit__``); each name is kept
    with the same contract so either can be checked on its own.
    """
    lines = SECTION.path.read_text(encoding="utf-8").splitlines()
    start = next(index for index, line in enumerate(lines) if line.strip() == SURFACE_HEADING)
    rows: dict[str, str] = {}
    for line in lines[start + 1 :]:
        if line.startswith("### "):
            break
        if not line.startswith("| `"):
            if rows:
                break
            continue
        cells = line.split("|")
        for name in cells[1].split("/"):
            rows[name.strip().strip("`")] = cells[3].strip()
    return rows


def test_the_section_has_the_block_this_module_asserts_on() -> None:
    """The pinned inventory, so a deleted example cannot make the tests vacuous."""
    assert len(EXAMPLES) == EXPECTED_BLOCKS, prd_examples.inventory_of(SECTION)
    assert len(RUNNABLE) == EXPECTED_RUN_BLOCKS, (
        "§5.3 must carry a `run` block: a compile-only example is how the "
        "`teardown_errors` defect survived\n" + prd_examples.inventory_of(SECTION)
    )


@pytest.mark.parametrize("example", CHECKED, ids=lambda example: example.location)
def test_every_block_compiles(example: prd_examples.Example) -> None:
    """A block that does not compile is a defect in the PRD."""
    prd_examples.compile_example(example)


@pytest.mark.parametrize("example", RUNNABLE, ids=lambda example: example.location)
def test_every_run_block_executes(example: prd_examples.Example) -> None:
    """The §5.3 example runs against the shipped package, in a fresh namespace."""
    prd_examples.execute(example, {"__name__": "prd_03_section_5_3"})


def test_the_surface_table_names_exactly_the_shipped_members() -> None:
    """The table and `dir(ResolutionScope)` must not be two different surfaces."""
    table = surface_table()
    documented_public = {name for name in table if not name.startswith("__")}
    documented_dunder = {name for name in table if name.startswith("__")}

    assert documented_public == set(SHIPPED), (
        "§5.3's surface table and the shipped class disagree: "
        f"only in the table {sorted(documented_public - set(SHIPPED))}, "
        f"only in the class {sorted(set(SHIPPED) - documented_public)}"
    )
    assert documented_dunder == set(CONTEXT_MANAGER)
    for name in documented_dunder:
        assert hasattr(ResolutionScope, name), f"§5.3 documents {name}, which the class lacks"


def test_the_closed_row_states_the_shipped_sense() -> None:
    """`closed` is not `active`: the row must say which way round it is."""
    contract = surface_table()["closed"]
    assert "False" in contract and "True" in contract, contract
    assert "until the scope closes" in contract, contract


def test_no_prd_03_prose_names_a_scope_member_the_class_lacks() -> None:
    """A sentence is the third way to promise a member no block exercises."""
    text = SECTION.path.read_text(encoding="utf-8")
    claimed = set(SCOPE_MEMBER_RE.findall(text))
    assert claimed, "no `scope.<member>` reference found, so nothing was checked"
    assert not claimed - set(SHIPPED), (
        f"PRD 03 names scope members the class does not ship: {sorted(claimed - set(SHIPPED))}"
    )


def test_the_execution_step_is_load_bearing() -> None:
    """A block touching a missing member must fail *here*, not in a user's shell."""
    broken = prd_examples.Example(
        path="synthetic.md",
        line=1,
        heading="synthetic",
        markers=frozenset({"run"}),
        source="scope = object()\nscope.teardown_errors\n",
    )
    with pytest.raises(AssertionError, match="`run` block failed"):
        prd_examples.execute(broken, {})
