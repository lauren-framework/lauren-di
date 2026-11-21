"""``.gitignore`` must ignore every artefact the project creates — and nothing else.

The file is load-bearing in two directions, and both failures are silent:

* **A missing pattern.** Every ``nox`` run leaves ``site/``, ``dist/`` and
  ``.nox/`` behind. Without an ignore entry ``git status`` stops being readable,
  and the first ``git add .`` commits a wheel and a rendered site.
* **A pattern that is too broad.** ``build*`` swallows
  ``scripts/build_reference.py``, ``*.txt`` swallows ``llms.txt`` and
  ``docs-requirements.txt``, ``docs/`` swallows the site source. Nothing fails
  until a fresh clone cannot build or ``nox -s docs_contract`` cannot find a page.

So this module checks the real file against two lists and a small gitignore
matcher:

* ``ARTEFACTS`` — paths the task runner creates; each must be ignored. The three
  top-level output directories are read from ``noxfile.py``'s own layout
  constants, so a new output directory cannot be added to the task runner
  without also being ignored here.
* ``TRACKED`` — files the repository ships; none may be ignored, and each must
  exist, so the list cannot rot into fiction.

``test_matcher_agrees_with_git`` keeps the matcher itself honest: it runs real
``git check-ignore --no-index`` over the same paths in a throwaway repository
and requires an identical verdict. Without it, a matcher bug could make every
other test in this file pass vacuously.
"""

from __future__ import annotations

import fnmatch
import pathlib
import shutil
import subprocess
from typing import Any

import pytest

pytestmark = pytest.mark.unit

ROOT = pathlib.Path(__file__).resolve().parents[2]
GITIGNORE = ROOT / ".gitignore"

#: Paths the task runner creates and a contributor should never commit. The
#: directory names are realistic children, not bare directories: ``git status``
#: reports files, and a pattern that only matches the directory itself would
#: still leave every file inside it untracked-but-visible.
ARTEFACTS: tuple[str, ...] = (
    "build/lib/lauren_di/__init__.py",
    "dist/lauren_di-0.1.0-py3-none-any.whl",
    "dist/lauren_di-0.1.0.tar.gz",
    "site/index.html",
    "site/reference/index.html",
    ".nox/unit/bin/python",
    ".pytest_cache/v/cache/nodeids",
    ".mypy_cache/3.14/lauren_di/__init__.meta.json",
    ".ruff_cache/0.9.0/1234567890",
    ".hypothesis/examples/scope-1",
    ".coverage",
    ".coverage.hostname.1234",
    "coverage.xml",
    "htmlcov/index.html",
    "lauren_di.egg-info/PKG-INFO",
    "lauren_di/__pycache__/__init__.cpython-314.pyc",
    "tests/unit/__pycache__/test_scopes.cpython-314-pytest-8.0.0.pyc",
    "scripts/__pycache__/build_reference.cpython-313.pyc",
    ".agenthicc/session.json",
    ".venv/bin/python",
    ".DS_Store",
)

#: Files the repository ships. Ignoring any of these breaks a clean checkout in
#: a way that is invisible locally, where the file already exists.
TRACKED: tuple[str, ...] = (
    ".gitignore",
    "AGENTS.md",
    "CHANGELOG.md",
    "CLAUDE.md",
    "CONTRIBUTING.md",
    "LICENSE",
    "README.md",
    "docs-requirements.txt",
    "llms-full.txt",
    "llms.txt",
    "mkdocs.yml",
    "noxfile.py",
    "pyproject.toml",
    ".github/PULL_REQUEST_TEMPLATE.md",
    ".github/ISSUE_TEMPLATE/documentation.yml",
    ".github/workflows/lint.yml",
    "docs/errors.md",
    "docs/getting-started.md",
    "docs/guides/custom-providers.md",
    "docs/guides/providers.md",
    "docs/guides/scopes.md",
    "docs/guides/testing.md",
    "docs/index.md",
    "docs/migration.md",
    "docs/reference.md",
    "docs/visibility.md",
    "lauren_di/__init__.py",
    "lauren_di/_container.py",
    "lauren_di/py.typed",
    "prds/01-product-definition-and-public-api.md",
    "prds/README.md",
    "prds/evidence/16-docs-layout.md",
    "scripts/build_llms_full.py",
    "scripts/build_reference.py",
    "scripts/check_docs_contract.py",
    "scripts/check_llms_full.py",
    "tests/conftest.py",
    "tests/packaging/test_wheel.py",
    "tests/typing/reveal_resolve.py",
    "tests/unit/test_gitignore_contract.py",
)


@pytest.fixture(scope="module")
def patterns() -> list[str]:
    """The ignore rules, in file order, comments and blank lines removed."""
    text = GITIGNORE.read_text(encoding="utf-8")
    return [
        line.rstrip()
        for line in text.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


def is_ignored(path: str, patterns: list[str]) -> bool:
    """Whether ``path`` (relative, POSIX separators) matches ``patterns``.

    Implements the parts of ``gitignore(5)`` this file uses: the last matching
    rule wins, ``!`` re-includes, a pattern containing ``/`` is anchored to the
    repository root, and one without it matches a single path component at any
    depth. A rule that matches a directory also matches everything beneath it,
    which is why every ancestor of the path is tested, not just the path.
    """
    parts = path.split("/")
    prefixes = ["/".join(parts[:index]) for index in range(1, len(parts) + 1)]
    ignored = False
    for pattern in patterns:
        negated = pattern.startswith("!")
        body = pattern[1:] if negated else pattern
        body = body.rstrip("/")
        if not body:
            continue
        anchored = "/" in body
        matched = False
        for prefix in prefixes:
            name = prefix if anchored else prefix.rsplit("/", 1)[-1]
            if fnmatch.fnmatchcase(name, body):
                matched = True
                break
        if matched:
            ignored = not negated
    return ignored


def test_gitignore_exists_and_says_why(patterns: list[str]) -> None:
    """The file exists, has rules, and explains the two traps it guards."""
    assert GITIGNORE.is_file(), ".gitignore is missing from the repository root"
    assert patterns, ".gitignore declares no rules"
    text = GITIGNORE.read_text(encoding="utf-8")
    assert "nox -s clean" in text, (
        ".gitignore must state that `nox -s clean` removes the same set, so the "
        "two lists stay comparable"
    )
    assert text.endswith("\n"), ".gitignore must end with a newline"
    assert len(patterns) == len(set(patterns)), "duplicate patterns make the file ambiguous"


def test_no_pattern_matches_everything(patterns: list[str]) -> None:
    """No rule is a bare wildcard: ``*`` or ``**`` would hide the whole checkout."""
    for pattern in patterns:
        body = pattern.lstrip("!")
        assert body.strip("/") not in {"*", "**"}, f"{pattern!r} would ignore every file"


def test_every_nox_artefact_is_ignored(patterns: list[str], noxfile: Any) -> None:
    """Each artefact the task runner writes is ignored, including its outputs."""
    derived = {
        f"{noxfile.BUILD_DIR.name}/lib/lauren_di/__init__.py",
        f"{noxfile.DIST_DIR.name}/lauren_di-0.1.0-py3-none-any.whl",
        f"{noxfile.SITE_DIR.name}/index.html",
    }
    missing = sorted({path for path in (*ARTEFACTS, *derived) if not is_ignored(path, patterns)})
    assert not missing, f"not ignored by .gitignore: {missing}"


@pytest.mark.parametrize("path", TRACKED)
def test_tracked_file_exists_and_is_not_ignored(path: str, patterns: list[str]) -> None:
    """No shipped file is ignored, and the list itself stays accurate."""
    assert (ROOT / path).exists(), f"{path} is listed as tracked but does not exist"
    assert not is_ignored(path, patterns), f"{path} must be committed, but .gitignore excludes it"


def test_matcher_agrees_with_git(patterns: list[str], tmp_path: pathlib.Path) -> None:
    """Real ``git check-ignore`` reaches the same verdict for every path above.

    ``--no-index`` makes git evaluate the patterns without consulting the index,
    so the check works in a throwaway repository that contains only the ignore
    file. ``git`` is skipped rather than failed when it is absent: the matcher is
    what the other tests use, and this test only proves it is not a fiction.
    """
    git = shutil.which("git")
    if git is None:  # pragma: no cover - every supported environment has git
        pytest.skip("git is not installed")

    (tmp_path / ".gitignore").write_text(GITIGNORE.read_text(encoding="utf-8"), encoding="utf-8")
    initialised = subprocess.run(
        [git, "-C", str(tmp_path), "init", "--quiet"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert initialised.returncode == 0, f"git init failed: {initialised.stderr}"
    candidates = [*ARTEFACTS, *TRACKED, ".env", ".env.example", "notes.txt", "dist/"]
    completed = subprocess.run(
        [git, "-C", str(tmp_path), "check-ignore", "--no-index", "--stdin"],
        input="\n".join(candidates),
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode in (0, 1), f"git check-ignore failed: {completed.stderr}"
    by_git = set(completed.stdout.split())
    by_matcher = {path for path in candidates if is_ignored(path, patterns)}
    assert by_matcher == by_git, (
        "the test's matcher and git disagree; "
        f"only in matcher: {sorted(by_matcher - by_git)}, "
        f"only in git: {sorted(by_git - by_matcher)}"
    )
