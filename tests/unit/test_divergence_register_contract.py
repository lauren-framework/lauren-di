"""The register rule ``prds/check_consistency.py`` is held to, proven by what it rejects.

Goal #56 closed two defects in PRD 02 that every other check passed:

* §6.4 closed with "Everything else in §6.2 is parity: ... same silent cases"
  while the register had no row for R-23, whose silent case *is* a divergence —
  the reference injects a function-token class-body field (`E-409`, `E-409b`) and
  this library skips it;
* §6.6 said "the three divergences in §6.4" while §6.4 had eight rows.

Both halves of each pair were well-formed, which is why nothing saw them. The
``registers`` check reads the *live* corpus, so a rule that stops firing looks
exactly like a corpus that satisfies it — the failure mode goal #49 fixed for the
registry parser. These tests therefore drive ``check_registers`` against small
synthetic PRD sets, so each rule is pinned by the mutation it rejects rather than
by the current corpus happening to trip it. The real ``prds/`` pages are never
touched.
"""

from __future__ import annotations

import importlib.util
import pathlib
import types

import pytest

pytestmark = pytest.mark.unit

ROOT = pathlib.Path(__file__).resolve().parents[2]
PRD_CHECK = ROOT / "prds" / "check_consistency.py"

#: A §6.4 register that satisfies every rule: two rows, one of them silent, a
#: closing sentence that names its exception instead of claiming parity, and a
#: ``P-9`` back-reference that PRD 08 §5.9 makes in the other direction.
PAGE_02 = """\
# PRD 02

### 6.4 Intentional divergences from the reference

| # | Divergence | Reference | `lauren-di` | Why | Decision |
|---|-----------|-----------|-------------|-----|----------|
| 1 | an error leaf | `StartupError` | `LifecycleConfigError` | leaves live under `DIError` | D-1 |
| 2 | a field token | injected (`E-409`) | skipped by the gates | P-9 in PRD 08 §5.9 | D-2 |

Everything else in §6.2 is parity: same token, same ordering. The silent cases are **not**
all the same, and row 2 is the exception: the field is skipped here and injected by the
reference (`E-409`). That is registered in PRD 08 §5.9 as P-9.

### 6.5 Module layout
"""

PAGE_03 = """\
# PRD 03

### 6.4 Intentional divergences from the reference

| # | Divergence | Reference | `lauren-di` | Why | Decision |
|---|-----------|-----------|-------------|-----|----------|
| 1 | an error leaf | `StartupError` | `LifecycleConfigError` | leaves live under `DIError` | D-3 |

Everything else in §6.2 is parity: same order, same silent cases.
"""

PAGE_04 = """\
# PRD 04

### 6.4 Intentional divergences from the reference

| # | Divergence | Reference | `lauren-di` | Why | Decision |
|---|-----------|-----------|-------------|-----|----------|
| 1 | scalar multi resolve | returns a `list` | `ProtocolAmbiguityError` | a count-dependent return type cannot be annotated | D-4 |

Everything else in §6.2 is parity: same token semantics, same ordering.
"""

PAGE_08 = """\
# PRD 08

### 5.9 Parity matrix

| # | Invariant | Fixture | Expected outcome |
|---|-----------|---------|------------------|
| P-1 | constructor injection | `container()` | the same object on both |
| P-9 | a function token in a class-body field | `clock: Depends[make_clock]` | **divergence:** reference injects it (`E-409`); library leaves it unset (recorded as PRD 02 §6.4 row 2) |

Rows P-9 and the `values=` keyword are the **complete** divergence register: two entries,
each already a decision in an earlier PRD (D-2).

### 5.10 Migration codemod
"""


def _load() -> types.ModuleType:
    """Import ``prds/check_consistency.py`` by path (it is not an importable package)."""
    spec = importlib.util.spec_from_file_location("prds_check_consistency_registers", PRD_CHECK)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    **overrides: str,
) -> list[str]:
    """Run the check over a synthetic corpus and return its failures."""
    pages = {
        "02-core-container-and-provider-registration.md": PAGE_02,
        "03-scopes-lifecycle-and-disposal.md": PAGE_03,
        "04-custom-providers-tokens-and-multi-bindings.md": PAGE_04,
        "08-framework-compatibility-and-migration.md": PAGE_08,
    }
    pages.update(overrides)
    for name, text in pages.items():
        (tmp_path / name).write_text(text, encoding="utf-8")
    module = _load()
    monkeypatch.setattr(module, "PRDS", sorted(tmp_path.glob("[0-9][0-9]-*.md")))
    module.check_registers()
    return module.failures


def test_the_clean_corpus_passes(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Every rule holds at once, so each failure below comes from the mutation alone."""
    assert _run(tmp_path, monkeypatch) == []


def test_the_census_is_reported(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A check that reads nothing must not look like a check that passed."""
    pages = {
        "02-core-container-and-provider-registration.md": PAGE_02,
        "03-scopes-lifecycle-and-disposal.md": PAGE_03,
        "04-custom-providers-tokens-and-multi-bindings.md": PAGE_04,
        "08-framework-compatibility-and-migration.md": PAGE_08,
    }
    for name, text in pages.items():
        (tmp_path / name).write_text(text, encoding="utf-8")
    module = _load()
    monkeypatch.setattr(module, "PRDS", sorted(tmp_path.glob("[0-9][0-9]-*.md")))
    module.check_registers()

    assert module.failures == []
    assert any(note.startswith("registers: 3 §6.4 register(s)") for note in module.notes), (
        f"the register census is not reported: {module.notes}"
    )


def test_a_stale_count_of_the_register_fails(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """PRD 02 §6.6's "the three divergences in §6.4" against an eight-row table."""
    page = PAGE_02.replace(
        "### 6.5 Module layout",
        "- The only edits are the three divergences in §6.4 plus deleting the branch.\n"
        "\n### 6.5 Module layout",
    )

    failures = _run(
        tmp_path, monkeypatch, **{"02-core-container-and-provider-registration.md": page}
    )

    assert any("three divergences" in f and "2 row(s)" in f for f in failures), failures


def test_a_count_of_a_row_range_is_measured_against_that_range(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ "the two divergences (§6.4 rows 1–2)" is a claim about a subset, not the register."""
    page = PAGE_02.replace(
        "### 6.5 Module layout",
        "The two divergences (§6.4 rows 1–2) are marked xfail in the shim.\n"
        "\n### 6.5 Module layout",
    )

    assert (
        _run(tmp_path, monkeypatch, **{"02-core-container-and-provider-registration.md": page})
        == []
    )


def test_a_row_range_that_does_not_add_up_fails(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The same sentence with a count the range contradicts."""
    page = PAGE_02.replace(
        "### 6.5 Module layout",
        "The three divergences (§6.4 rows 1–2) are marked xfail in the shim.\n"
        "\n### 6.5 Module layout",
    )

    failures = _run(
        tmp_path, monkeypatch, **{"02-core-container-and-provider-registration.md": page}
    )

    assert any("§6.4 rows 1–2" in f for f in failures), failures


def test_a_row_range_naming_an_absent_row_fails(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A citation of a row the register does not have is not a smaller register."""
    page = PAGE_02.replace(
        "### 6.5 Module layout",
        "The four divergences (§6.4 rows 1–4) are marked xfail in the shim.\n"
        "\n### 6.5 Module layout",
    )

    failures = _run(
        tmp_path, monkeypatch, **{"02-core-container-and-provider-registration.md": page}
    )

    assert any("does not have" in f for f in failures), failures


def test_claiming_same_silent_cases_while_a_row_records_silence_fails(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The original PRD 02 §6.4 defect: parity claimed over a silent divergence."""
    page = PAGE_02.replace(
        "Everything else in §6.2 is parity: same token, same ordering. The silent cases are **not**\n"
        "all the same, and row 2 is the exception: the field is skipped here and injected by the\n"
        "reference (`E-409`). That is registered in PRD 08 §5.9 as P-9.",
        "Everything else in §6.2 is parity: same token, same ordering, same silent cases.",
    )

    failures = _run(
        tmp_path, monkeypatch, **{"02-core-container-and-provider-registration.md": page}
    )

    assert any("same silent cases" in f and "[2]" in f for f in failures), failures


def test_the_same_claim_passes_when_no_row_records_silence(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """PRD 03 §6.4's shape: the claim is only wrong when the register contradicts it."""
    page = PAGE_03.replace(
        "| 1 | an error leaf | `StartupError` | `LifecycleConfigError` | leaves live under `DIError` | D-3 |",
        "| 1 | a state name | `active` | `closed` | an inverted name is not a behaviour | D-3 |",
    )

    assert _run(tmp_path, monkeypatch, **{"03-scopes-lifecycle-and-disposal.md": page}) == []


def test_a_hole_in_the_row_numbering_fails(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A deleted row must show as a hole, not as a smaller register."""
    page = PAGE_02.replace("| 2 | a field token |", "| 3 | a field token |")

    failures = _run(
        tmp_path, monkeypatch, **{"02-core-container-and-provider-registration.md": page}
    )

    assert any("numbered" in f for f in failures), failures


def test_a_p_id_the_parity_matrix_does_not_define_fails(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A §6.4 row cannot register an invariant PRD 08 §5.9 has never heard of."""
    page = PAGE_02.replace("P-9 in PRD 08 §5.9", "P-99 in PRD 08 §5.9")

    failures = _run(
        tmp_path, monkeypatch, **{"02-core-container-and-provider-registration.md": page}
    )

    assert any("P-99" in f and "does not define" in f for f in failures), failures


def test_a_citation_the_register_does_not_name_back_fails(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """PRD 08's "recorded as PRD 02 §6.4 row 9" must land on a row that names it."""
    page = PAGE_02.replace(" | P-9 in PRD 08 §5.9", " | a decision |")

    failures = _run(
        tmp_path, monkeypatch, **{"02-core-container-and-provider-registration.md": page}
    )

    assert any("does not name it back" in f for f in failures), failures


def test_a_citation_of_a_row_that_does_not_exist_fails(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The other half of the same link: the row has to be there."""
    page = PAGE_08.replace("PRD 02 §6.4 row 2", "PRD 02 §6.4 row 7")

    failures = _run(tmp_path, monkeypatch, **{"08-framework-compatibility-and-migration.md": page})

    assert any("row 7" in f for f in failures), failures


def test_a_divergence_row_the_register_sentence_omits_fails(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A divergence added to §5.9 but not to the register sentence is half-registered."""
    page = PAGE_08.replace(
        "| P-9 | a function token in a class-body field |",
        "| P-10 | another divergence | `f` | **divergence:** the two sides differ (`E-410`) |\n"
        "| P-9 | a function token in a class-body field |",
    )

    failures = _run(tmp_path, monkeypatch, **{"08-framework-compatibility-and-migration.md": page})

    assert any("P-10" in f and "does not name" in f for f in failures), failures


def test_a_register_sentence_that_miscounts_its_entries_fails(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The §5.9 count is measured against the entries the same sentence enumerates."""
    page = PAGE_08.replace("two entries", "three entries")

    failures = _run(tmp_path, monkeypatch, **{"08-framework-compatibility-and-migration.md": page})

    assert any("enumerates 2" in f for f in failures), failures


def test_a_missing_register_fails_rather_than_passing_quietly(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A renamed heading must be a red gate, not a check that reads nothing."""
    failures = _run(
        tmp_path,
        monkeypatch,
        **{"03-scopes-lifecycle-and-disposal.md": PAGE_03.replace("### 6.4", "### 6.6")},
    )

    assert any("03-scopes-lifecycle-and-disposal.md" in f for f in failures), failures


def test_the_live_corpus_passes() -> None:
    """The rules hold on the real PRDs, which is the only reason the gate is green."""
    module = _load()
    module.check_registers()

    assert module.failures == [], module.failures
    assert any(note.startswith("registers: 3 §6.4 register(s)") for note in module.notes)
