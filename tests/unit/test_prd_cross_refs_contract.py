"""The citation rule ``prds/check_consistency.py`` is held to, proven by the references it must reject.

PRD 01 §2's G8 row pointed at "a tested example in PRD 10's docs contract" for
months. There is no PRD 10 — the corpus is 01–08 and the owner forbids a 09 — and
nothing noticed, because the `cross-refs` check matched only ``PRD 0?\\d §x``: a
two-digit document number did not match the pattern at all, so the reference was
not *rejected*, it was **never seen**. The gate stayed green while a reader was
sent to a document that cannot exist.

That is the failure mode these tests pin: not "a bad reference slipped past a
rule" but "a reference the rule cannot read is silently not a reference". Each
case below is a small synthetic corpus, so the rule is proven by the citation it
refuses rather than by the live corpus happening to contain only good ones. The
real ``prds/`` is never touched.

The last case is the control: a two-digit document that *does* exist must pass.
Without it, "reject ``PRD 10``" would be satisfied by a blanket ban on two-digit
numbers, which is a different (and wrong) rule.
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

#: What the fixture returns: corpus ``{file name: body}`` -> ``(failures, note)``.
Runner = collections.abc.Callable[[dict[str, str]], tuple[list[str], str]]

#: A minimal PRD: `headings()` needs numbered headings, nothing else. The title
#: deliberately does *not* read ``PRD 01`` — a document's own title is a citation
#: of itself in the live corpus, and counting it here would make every expected
#: number in this module off by one for a reason unrelated to the rule.
PRD = """\
# Synthetic document {number}

## 5. Proposed API

### 5.1 A section that exists

{body}
"""


def _load() -> types.ModuleType:
    """Import ``prds/check_consistency.py`` by path (it is not an importable package)."""
    spec = importlib.util.spec_from_file_location("prds_check_consistency_crossrefs", PRD_CHECK)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def cross_refs(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> Runner:
    """Return a callable that runs ``check_cross_refs`` over a synthetic corpus."""

    def run(documents: dict[str, str]) -> tuple[list[str], str]:
        corpus = tmp_path / "prds"
        corpus.mkdir(exist_ok=True)
        for name, body in documents.items():
            (corpus / name).write_text(PRD.format(number=name[:2], body=body), encoding="utf-8")
        module = _load()
        # Mirror the module's own corpus glob rather than a second copy of it, so
        # the fixture cannot disagree with the checker about which files exist.
        monkeypatch.setattr(module, "PRDS", sorted(corpus.glob("[0-9][0-9]-*.md")))
        module.failures.clear()
        module.notes.clear()
        module.check_cross_refs()
        note = next((n for n in module.notes if n.startswith("cross-refs:")), "")
        return list(module.failures), note

    return run


def _counts(note: str) -> tuple[int, int]:
    """``(section references, document mentions)`` as the check reports them."""
    located = note.split("'PRD NN §x' reference(s)")[0].split("cross-refs:")[1]
    mentioned = note.split("and ")[1].split("'PRD NN' mention(s)")[0]
    return int(located.strip()), int(mentioned.strip())


def test_a_citation_of_an_existing_document_and_section_passes(cross_refs: object) -> None:
    """The control: the ordinary case stays green and is counted, not skipped."""
    failures, note = cross_refs(
        {
            "01-alpha.md": "See PRD 02 §5.1 for the rule.\n",
            "02-beta.md": "The API is in PRD 01 §5.1.\n",
        }
    )
    assert failures == [], failures
    assert _counts(note) == (2, 2), note


def test_a_two_digit_document_that_exists_passes(cross_refs: object) -> None:
    """Resolution, not a blanket ban: a real PRD 10 must be accepted."""
    failures, note = cross_refs(
        {"01-alpha.md": "See PRD 10 §5.1.\n", "10-decimal.md": "Nothing to see.\n"}
    )
    assert failures == [], failures
    assert _counts(note) == (1, 1), note


def test_a_two_digit_document_that_does_not_exist_fails_and_is_counted(cross_refs: object) -> None:
    """The defect this module exists for: PRD 10 is reported, not silently skipped."""
    failures, note = cross_refs({"01-alpha.md": "See PRD 10 §3 for the rule.\n"})
    assert len(failures) == 1, failures
    assert "names no PRD on disk" in failures[0], failures[0]
    assert "'PRD 10'" in failures[0], failures[0]
    # The count is what proves it was *seen*: a skipped reference cannot move it.
    assert _counts(note) == (1, 1), note


def test_a_section_less_citation_is_checked_too(cross_refs: object) -> None:
    """G8's exact wording — ``PRD 10's docs contract`` — carried no section at all."""
    failures, note = cross_refs({"01-alpha.md": "A tested example in PRD 10's docs contract.\n"})
    assert len(failures) == 1, failures
    assert "names no PRD on disk" in failures[0], failures[0]
    assert _counts(note) == (0, 1), note


def test_a_section_the_document_does_not_have_fails(cross_refs: object) -> None:
    """An existing document with a stale section is still a broken citation."""
    failures, _ = cross_refs({"01-alpha.md": "See PRD 02 §9.9.\n", "02-beta.md": "Nothing.\n"})
    assert len(failures) == 1, failures
    assert "PRD 02 §9.9 does not exist" in failures[0], failures[0]


def test_a_missing_document_is_reported_once_per_file(cross_refs: object) -> None:
    """The section pass must not restate the mention pass's failure."""
    failures, _ = cross_refs({"01-alpha.md": "PRD 10 §3 and PRD 10 §4 and PRD 10 again.\n"})
    assert len(failures) == 1, failures
    assert failures[0].count("PRD 10") == 1, failures[0]


def test_the_live_corpus_is_clean_under_the_widened_rule() -> None:
    """The real ``prds/`` resolves, and every citation is counted rather than skipped."""
    module = _load()
    module.failures.clear()
    module.notes.clear()
    module.check_cross_refs()
    assert module.failures == [], module.failures
    note = next(n for n in module.notes if n.startswith("cross-refs:"))
    located, mentioned = _counts(note)
    assert located >= 100, note
    assert mentioned >= located, note
