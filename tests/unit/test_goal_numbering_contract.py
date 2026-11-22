"""The numbering rule ``GOALS.md`` is held to, proven by the plans it must reject.

``prds/check_consistency.py``'s ``owners`` check reads the *live* ``GOALS.md``, so
a rule that stops firing looks exactly like a plan that satisfies it: the gate
stays green, the number keeps meaning two things, and nothing says so. That is
precisely how §4's ``#36`` came to denote two goals while every table lined up.

These tests drive ``_check_goal_numbering`` against small synthetic plans instead
of the real file, so each rule is pinned by the mutation it rejects rather than by
the current corpus happening to trip it. The real ``GOALS.md`` is never touched:
the fixture points the module at a ``tmp_path`` copy, so a failure here can only
come from the rule under test.
"""

from __future__ import annotations

import collections.abc
import importlib.util
import pathlib
import types

import pytest

pytestmark = pytest.mark.unit

ROOT = pathlib.Path(__file__).resolve().parents[2]
PRD_CHECK = ROOT / "prds" / "check_consistency.py"

#: A plan that satisfies every rule: two closed goals, one carried goal, one
#: programme item, and prose that cites only ids the file defines.
PLAN = """\
# GOALS.md

## 2. Closed \u2014 goals #1\u2013#2

| Goals | Delivered | Evidence |
|---|---|---|
| #1\u2013#2 | the first two goals | `prds/evidence/01.md` |

## 3. Carried \u2014 goals #3

| # | Status | Item |
|---|---|---|
| 3 | **verified** | the third goal |

## 4. The programme \u2014 programme items P1\u2013P2

### P1 \u2014 the first programme item

### P2 \u2014 the second programme item

## 5. Order and dependencies

Goals #1\u2013#3 are closed; P1 and P2 are not queued yet.
"""


def _load() -> types.ModuleType:
    """Import ``prds/check_consistency.py`` by path (it is not an importable package)."""
    spec = importlib.util.spec_from_file_location("prds_check_consistency", PRD_CHECK)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def numbering(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> collections.abc.Callable[[str], tuple[list[str], tuple[int, int]]]:
    """Run ``_check_goal_numbering`` over ``plan``; return its failures and its counts."""
    module = _load()
    plan_file = tmp_path / "GOALS.md"

    def run(plan: str) -> tuple[list[str], tuple[int, int]]:
        plan_file.write_text(plan)
        monkeypatch.setattr(module, "GOALS", plan_file)
        module.failures.clear()
        counts = module._check_goal_numbering()
        return list(module.failures), counts

    return run


def test_the_clean_plan_passes_and_reports_both_namespaces(numbering) -> None:
    """The baseline: no failure, and both kinds of reference are actually counted."""
    failures, (queued, programme) = numbering(PLAN)
    assert failures == []
    assert queued > 0, "no '#NN' reference was counted, so nothing was checked"
    assert programme > 0, "no 'PN' reference was counted, so nothing was checked"


def test_a_position_registered_twice_in_the_tables_fails(numbering) -> None:
    """The collision the tables can hide: a dict projection keeps only the last row."""
    plan = PLAN.replace(
        "| 3 | **verified** | the third goal |",
        "| 3 | **verified** | the third goal |\n| 3 | **verified** | a different third goal |",
    )
    failures, _ = numbering(plan)
    assert failures, "a position registered twice was accepted"
    assert "registered twice" in failures[0]
    assert "a different third goal" in failures[0]


def test_a_bolded_position_cell_fails(numbering) -> None:
    """A first cell that is not a bare number registers nothing, so it must be a failure.

    ``| **44** | **verified** | … |`` is a well-formed markdown row and reads like a
    goal, but the old reader asked "is this a number?" and answered "no" by skipping
    the row — so the goal existed nowhere, and nothing noticed.
    """
    plan = PLAN.replace(
        "| 3 | **verified** | the third goal |",
        "| 3 | **verified** | the third goal |\n"
        "| **4** | **verified** | a goal the parser cannot see |",
    )
    failures, _ = numbering(plan)
    assert failures, "a bolded number cell was silently skipped"
    assert any("first cell must be a bare number" in failure for failure in failures), failures
    assert any("**4**" in failure for failure in failures), failures


def test_a_row_too_short_to_reach_its_description_fails(numbering) -> None:
    """§3 keeps the item in cell 3, so a two-cell row has no goal to register."""
    plan = PLAN.replace(
        "| 3 | **verified** | the third goal |",
        "| 3 | **verified** | the third goal |\n| 4 | a goal with no status cell |",
    )
    failures, _ = numbering(plan)
    assert failures, "a row with no descriptive cell was silently dropped"
    assert any("cell(s), but its description is cell 3" in failure for failure in failures), (
        failures
    )


def test_the_required_width_is_the_table_s_own_not_three(numbering) -> None:
    """§2's description is cell 2, so a two-cell §2 row is complete and must pass.

    The two tables are not the same width: demanding three cells everywhere would
    reject a valid §2 row, which is why the rule names the descriptive cell per table.
    """
    plan = PLAN.replace(
        "| #1–#2 | the first two goals | `prds/evidence/01.md` |",
        "| #1–#2 | the first two goals |",
    )
    failures, (queued, _) = numbering(plan)
    assert failures == [], failures
    assert queued > 0, "the two-cell §2 row registered nothing"


def test_a_range_row_overlapping_a_later_row_fails(numbering) -> None:
    """``#1\u2013#3`` in §2 claims position 3, which §3 gives to another goal."""
    plan = PLAN.replace("| #1\u2013#2 |", "| #1\u2013#3 |")
    failures, _ = numbering(plan)
    assert failures, "an overlapping range row was accepted"
    assert "position #3 is registered twice" in failures[0]


def test_a_heading_reusing_a_registered_number_fails(numbering) -> None:
    """A queued goal may not take a position §2/§3 already assigns to another goal."""
    plan = PLAN.replace(
        "## 4. The programme",
        "### #3 \u2014 a second goal claiming position 3\n\n## 4. The programme",
    )
    failures, _ = numbering(plan)
    assert failures, "a heading reused a registered number"
    assert "reuses a number" in failures[0]


def test_a_position_heading_inside_section_4_fails(numbering) -> None:
    """§4's items hold no runner position, so a '#NN' heading there is the old defect."""
    plan = PLAN.replace(
        "### P1 \u2014 the first programme item", "### #3 \u2014 the first programme item"
    )
    failures, _ = numbering(plan)
    assert any("sits in \u00a74" in failure for failure in failures), failures


def test_a_programme_heading_outside_section_4_fails(numbering) -> None:
    """A queued goal carries its runner position, not a programme id."""
    plan = PLAN.replace(
        "## 4. The programme",
        "### P1 \u2014 a programme item that was queued\n\n## 4. The programme",
    )
    failures, _ = numbering(plan)
    assert any("outside \u00a74" in failure for failure in failures), failures


def test_a_reference_to_an_undefined_position_fails(numbering) -> None:
    """A citation may not point at a position the file does not define."""
    failures, _ = numbering(
        PLAN.replace("Goals #1\u2013#3 are closed", "Goals #1\u2013#3 and #9 are closed")
    )
    assert any("'#9' names no goal registered" in failure for failure in failures), failures


def test_a_reference_to_an_undefined_programme_item_fails(numbering) -> None:
    """The same rule for the programme namespace."""
    failures, _ = numbering(
        PLAN.replace("P1 and P2 are not queued yet", "P1 and P4 are not queued yet")
    )
    assert any("'P4' names no programme item" in failure for failure in failures), failures


def test_a_programme_item_defined_twice_fails(numbering) -> None:
    """Two ``### P1`` headings are one id for two goals, the same collision one level down."""
    plan = PLAN.replace(
        "### P2 \u2014 the second programme item",
        "### P1 \u2014 the second programme item",
    )
    failures, _ = numbering(plan)
    assert any("defined twice" in failure for failure in failures), failures
