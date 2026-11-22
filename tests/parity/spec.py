"""PRD 08 §5.9 as data: the matrix rows and the outcomes that differ.

The table is the specification, so it is parsed rather than retyped: if a row is
added to §5.9 and not to :data:`MATRIX`, ``test_harness`` fails; if a row is
dropped from the PRD and left in the suite, it fails too. The same holds for the
divergence register — §5.9 names its entries in one sentence, and that sentence
is the source of truth for :data:`backends.DIVERGENCES`. Its stated *size* is
parsed too (:func:`prd_register_size`), so the prose cannot say one number while
the adapter holds another: the count is derived from the sentence, never typed
twice.
"""

from __future__ import annotations

import pathlib
import re

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
PRD08 = REPO_ROOT / "prds" / "08-framework-compatibility-and-migration.md"

#: The fourteen invariants of §5.9, in table order. Each has one test in
#: ``test_matrix.py``; the test names carry the row id so a failure reads as
#: "P-7 failed", the same id the PRD uses.
MATRIX: tuple[str, ...] = (
    "P-1",
    "P-2",
    "P-3",
    "P-4",
    "P-5",
    "P-6",
    "P-7",
    "P-8",
    "P-9",
    "P-10",
    "P-11",
    "P-12",
    "P-13",
    "P-14",
)

#: What each side does on the divergent rows. A recorded outcome is the
#: assertion: the invariant compares against this value, so an implementation
#: that changes its behaviour fails and this table has to be edited in the same
#: commit (MC-31 — no ``xfail`` bucket).
RECORDED: dict[str, dict[str, str]] = {
    "P-6": {
        "lauren": "raises:MissingProviderError:str",
        "lauren_di": "ignored",
    },
    "P-11": {
        "lauren": "no-container-lifecycle",
        "lauren_di": "container-owned",
    },
    "P-13": {
        "lauren": "injects",
        "lauren_di": "ignored",
    },
    "P-14": {
        "lauren": "alias-scope-blind",
        "lauren_di": "rejects",
    },
}

_ROW_RE = re.compile(r"^\|\s*(P-\d+)\s*\|", re.MULTILINE)
_REGISTER_RE = re.compile(r"Rows (P-\d+(?:,\s*P-\d+)*) and the `values=` keyword")
_SIZE_RE = re.compile(r"divergence register:\s*(\w+)\s+entries?")
#: The register's size is prose, so it is written as a word. Only the range a
#: register of this size can plausibly occupy is accepted; a numeral, a typo or
#: a word outside it is a failure rather than a silent zero.
_WORD_NUMBERS = {
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
}


def parity_matrix_section() -> str:
    """The body of PRD 08 §5.9, without its heading or §5.10."""
    text = PRD08.read_text(encoding="utf-8")
    match = re.search(
        r"^### 5\.9 Parity matrix\s*$(.*?)^### 5\.10 ", text, re.MULTILINE | re.DOTALL
    )
    assert match is not None, "PRD 08 §5.9 (Parity matrix) is missing or renamed"
    return match.group(1)


def prd_rows() -> tuple[str, ...]:
    """Every ``P-n`` row of the §5.9 table, in order."""
    return tuple(_ROW_RE.findall(parity_matrix_section()))


def prd_register_rows() -> tuple[str, ...]:
    """The rows §5.9 names in its "complete divergence register" sentence."""
    match = _REGISTER_RE.search(parity_matrix_section())
    assert match is not None, (
        "PRD 08 §5.9 no longer states which rows form the divergence register "
        "(expected a sentence like 'Rows P-6, P-11 and the `values=` keyword ...')"
    )
    return tuple(re.findall(r"P-\d+", match.group(1)))


def prd_register_size() -> int:
    """The size §5.9's own sentence states for the register.

    The sentence reads "… the **complete** divergence register: four entries".
    Parsing the word means the prose cannot claim one size while the adapter
    holds another — the failure mode goal #51 fixed one file over, where a typed
    figure went stale inside the guard built to catch stale figures.
    """
    match = _SIZE_RE.search(parity_matrix_section())
    assert match is not None, (
        "PRD 08 §5.9 no longer states the register's size "
        "(expected '… divergence register: four entries')"
    )
    word = match.group(1).lower()
    assert word in _WORD_NUMBERS, (
        f"§5.9 states the register's size as {word!r}, which is not a number this "
        f"suite can read; write it as a word ({', '.join(_WORD_NUMBERS)})"
    )
    return _WORD_NUMBERS[word]
