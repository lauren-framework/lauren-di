"""AGENTS.md's quick start quotes a test count, and a count is a claim that rots.

The line read ``full suite (114 tests, <2s)`` while the suite had grown to
nearly five hundred tests and the wall-clock figure was out by an order of
magnitude. Nothing read the number, so nothing noticed — the same failure as a
documented gate that no job runs. This module makes the figure a claim the
suite checks.

**Why the figure is not simply what ``pytest -q`` prints.** ``tests/parity/``
needs the ``../lauren-framework`` checkout, and ``tests/parity/test_matrix.py``
skips itself at *import* time when that checkout is absent, so the collected
total is 450, 476 or 502 depending on what is on the machine. A documented
number that changes with the environment is the same defect as a gate that
does, which is why ``[tool.coverage.report]`` quotes its figure "over the suite
that runs *everywhere*" and excludes parity. The quick start follows that
precedent: it states the environment-independent count and says what parity adds
when the reference checkout is present, so both halves can be checked here.

The count is obtained from a ``--collect-only`` subprocess rather than from
``request.session``: this module runs inside the very session it is counting, so
the total includes it, and asking a *separate* process is what keeps the number
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

#: The quick-start line: ``pytest -q  # full suite: N tests (+M parity when …)``.
_LINE_RE = re.compile(
    r"^pytest -q\s+#\s+full suite:\s+(?P<base>\d+) tests\s+\(\+(?P<parity>\d+) parity",
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
        "(+M parity …)` line; if the wording changed, update _LINE_RE in this module"
    )


@pytest.fixture(scope="module")
def documented(quick_start: str) -> tuple[int, int]:
    """The two figures the quick start states: the base count and the parity addition."""
    match = _LINE_RE.search(quick_start)
    assert match is not None
    return int(match.group("base")), int(match.group("parity"))


@pytest.fixture(scope="module")
def everywhere() -> int:
    """The count that does not depend on the optional reference checkout."""
    return _collected("--ignore=tests/parity")


def test_the_documented_count_is_the_environment_independent_one(
    documented: tuple[int, int], everywhere: int
) -> None:
    """AGENTS.md quotes the count a contributor gets without the parity checkout."""
    base, _ = documented
    assert base == everywhere, (
        f"AGENTS.md's quick start says {base} tests, but `pytest -q --ignore=tests/parity` "
        f"collects {everywhere}. Update the line — the count is a claim, not decoration."
    )


def test_the_parity_addition_is_what_the_collection_says(documented: tuple[int, int]) -> None:
    """With the reference checkout present, base + parity is the full total."""
    if not REFERENCE_ROOT.is_dir():
        pytest.skip(
            f"the parity checkout is absent at {REFERENCE_ROOT}, so the addition cannot be measured"
        )
    base, parity = documented
    total = _collected()
    assert total == base + parity, (
        f"AGENTS.md says {base} tests plus {parity} parity cases, but `pytest -q` collects "
        f"{total}. Update the line."
    )


def test_the_line_states_what_parity_needs(quick_start: str, documented: tuple[int, int]) -> None:
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
