"""AGENTS.md's quick start quotes a test count, and a count is a claim that rots.

The line read ``full suite (114 tests, <2s)`` while the suite had grown to
nearly five hundred tests and the wall-clock figure was out by an order of
magnitude. Nothing read the number, so nothing noticed — the same failure as a
documented gate that no job runs. This module makes the figure a claim the
suite checks.

**Two things move the collected total, so the line states both.** A single
number would be false somewhere, and a number that changes with the environment
is the same defect as a gate that does (which is why
``[tool.coverage.report]`` quotes its figure "over the suite that runs
*everywhere*" and excludes parity):

*``tests/parity/``* needs the ``../lauren-framework`` checkout, and
``tests/parity/test_matrix.py:32-40`` skips itself at *import* time when that
checkout is absent. Parity contributes the matrix's and the harness's cases, so
it is quoted as an addition rather than folded into the base.

*Python 3.10* collects fewer tests: ``tests/unit/test_readme_contract.py:42``
does ``pytest.importorskip("tomllib")`` at module level, and ``tomllib`` is
stdlib only from 3.11, so that whole module is never collected on 3.10. The
count is therefore stated per interpreter — one figure for 3.11+, one for 3.10
— and asserted on all five, not skipped on four of them.

The count comes from a ``--collect-only`` subprocess rather than from
``request.session``: this module runs inside the very session it is counting, so
the total includes it, and asking a *separate* process is what makes the number
the same one a contributor gets by typing the documented command.
"""

from __future__ import annotations

import os
import pathlib
import re
import subprocess
import sys

import pytest

pytestmark = pytest.mark.unit

ROOT = pathlib.Path(__file__).resolve().parents[2]
AGENTS = ROOT / "AGENTS.md"
REFERENCE_ROOT = ROOT.parent / "lauren-framework"

#: The quick-start line:
#: ``pytest -q  # full suite: N tests (M on 3.10); +P parity with ../lauren-framework``.
_LINE_RE = re.compile(
    r"^pytest -q\s+#\s+full suite:\s+(?P<modern>\d+) tests\s+"
    r"\((?P<legacy>\d+) on 3\.10\);\s*\+(?P<parity>\d+) parity",
    re.MULTILINE,
)
#: Any "N tests" claim in a document a reader trusts, so a second, unchecked
#: figure cannot be introduced beside the checked one.
_CLAIM_RE = re.compile(r"\b(?P<count>\d{2,4})\s+tests?\b", re.IGNORECASE)
#: The corpus that documents the project to a human or an agent.
CLAIM_DOCUMENTS = (
    "AGENTS.md",
    "CLAUDE.md",
    "README.md",
    "CONTRIBUTING.md",
    "CHANGELOG.md",
    "llms.txt",
    "llms-full.txt",
)


def expected_count(modern: int, legacy: int, *, python: tuple[int, int] | None = None) -> int:
    """The figure that applies to ``python`` (default: the running interpreter).

    ``tomllib`` arrived in 3.11, so ``tests/unit/test_readme_contract.py`` is not
    collected on 3.10 and the total is 25 lower there. Kept as a pure function
    so the 3.10 branch is testable from a 3.14 interpreter.
    """
    version = sys.version_info[:2] if python is None else python
    return legacy if version < (3, 11) else modern


def _collected(*selectors: str) -> int:
    """How many tests a fresh ``pytest`` process collects for ``selectors``.

    ``PYTEST_*`` and ``COV_CORE_*`` are stripped from the environment: this test
    can run inside ``nox -s coverage``, where ``pytest-cov`` exports those to
    make *subprocesses* report coverage, and a collection run that starts a
    coverage session is slower and noisier for no benefit. ``-p
    no:cacheprovider`` keeps the probe from touching ``.pytest_cache``.
    """
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("PYTEST_", "COV_CORE"))
    }
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "--collect-only",
            "-q",
            "-p",
            "no:cacheprovider",
            *selectors,
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        env=env,
        timeout=300,
        check=False,
    )
    match = re.search(r"(?P<count>\d+) tests? collected", completed.stdout)
    assert match is not None, (
        "pytest did not report a collection count "
        f"(exit {completed.returncode}):\n{completed.stdout[-2000:]}\n{completed.stderr[-2000:]}"
    )
    return int(match.group("count"))


@pytest.fixture(scope="module")
def quick_start() -> str:
    """The whole ``pytest -q`` line from the quick start, comment included."""
    for line in AGENTS.read_text(encoding="utf-8").splitlines():
        if _LINE_RE.search(line):
            return line
    pytest.fail(
        "AGENTS.md's quick start no longer carries a `pytest -q  # full suite: N tests "
        "(M on 3.10); +P parity …` line; if the wording changed, update _LINE_RE in this module"
    )


@pytest.fixture(scope="module")
def documented(quick_start: str) -> tuple[int, int, int]:
    """The three figures the line states: 3.11+, 3.10, and the parity addition."""
    match = _LINE_RE.search(quick_start)
    assert match is not None
    return int(match.group("modern")), int(match.group("legacy")), int(match.group("parity"))


@pytest.fixture(scope="module")
def everywhere() -> int:
    """The count that does not depend on the optional reference checkout."""
    return _collected("--ignore=tests/parity")


def test_the_documented_count_is_the_one_this_interpreter_collects(
    documented: tuple[int, int, int], everywhere: int
) -> None:
    """AGENTS.md quotes the count a contributor gets without the parity checkout."""
    modern, legacy, _ = documented
    expected = expected_count(modern, legacy)
    assert expected == everywhere, (
        f"AGENTS.md's quick start implies {expected} tests on Python "
        f"{sys.version_info[0]}.{sys.version_info[1]}, but `pytest -q --ignore=tests/parity` "
        f"collects {everywhere}. Update the line — the count is a claim, not decoration."
    )


def test_the_3_10_figure_is_the_interpreter_without_tomllib(
    documented: tuple[int, int, int], everywhere: int
) -> None:
    """The 3.10 branch is checked from whatever interpreter this runs on.

    ``tomllib`` is stdlib from 3.11, so ``tests/unit/test_readme_contract.py`` is
    not collected on 3.10. The difference is a property of the suite, so it can
    be asserted here: it is the size of that module.
    """
    modern, legacy, _ = documented
    assert expected_count(modern, legacy, python=(3, 10)) == legacy
    assert expected_count(modern, legacy, python=(3, 14)) == modern
    if sys.version_info[:2] >= (3, 11):
        assert modern - legacy == _collected("tests/unit/test_readme_contract.py")
    else:  # pragma: no cover - this branch needs a 3.10 interpreter
        assert everywhere == legacy


def test_the_parity_addition_is_what_the_collection_says(
    documented: tuple[int, int, int],
) -> None:
    """With the reference checkout present, base + parity is the full total."""
    if sys.version_info[:2] < (3, 11):
        pytest.skip("the parity addition is stated for 3.11+, where tomllib is stdlib")
    if not REFERENCE_ROOT.is_dir():
        pytest.skip(
            f"the parity checkout is absent at {REFERENCE_ROOT}, so the addition cannot be measured"
        )
    modern, _, parity = documented
    total = _collected()
    assert total == modern + parity, (
        f"AGENTS.md says {modern} tests plus {parity} parity cases, but `pytest -q` collects "
        f"{total}. Update the line."
    )


def test_the_line_states_what_parity_needs(
    quick_start: str, documented: tuple[int, int, int]
) -> None:
    """The figure is only honest with the caveat that explains the difference."""
    assert "lauren-framework" in quick_start, (
        "the quick-start line must name the reference checkout that adds the parity cases, "
        f"otherwise the {documented[0]} figure looks wrong on a machine that has one"
    )


def test_no_other_document_states_an_unchecked_test_count() -> None:
    """One checked figure, not several: a second count would be a second stale claim."""
    claims: list[str] = []
    for name in CLAIM_DOCUMENTS:
        path = ROOT / name
        if not path.exists():
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if _CLAIM_RE.search(line) and not _LINE_RE.search(line):
                claims.append(f"{name}:{number}: {line.strip()}")
    for page in sorted((ROOT / "docs").rglob("*.md")):
        for number, line in enumerate(page.read_text(encoding="utf-8").splitlines(), start=1):
            if _CLAIM_RE.search(line):
                claims.append(f"docs/{page.name}:{number}: {line.strip()}")
    assert not claims, (
        "a test count is documented outside AGENTS.md's checked quick-start line, so nothing "
        f"keeps it true: {claims}"
    )
