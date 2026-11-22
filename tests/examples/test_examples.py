"""``examples/`` is executable documentation, so the suite executes it.

An example that is never run rots quietly: a renamed method or a changed
signature leaves a snippet that still *reads* correctly while raising the
moment a reader copies it. This module makes five properties checked rather
than assumed:

1. every script runs to completion — exit code 0 and non-empty output — from
   the repository root, which is the command ``examples/README.md`` documents;
2. every script also runs from *inside* ``examples/``, so the directory is not
   quietly dependent on being launched from the root;
3. every script states its scenario in a module docstring and exposes an
   ``if __name__ == "__main__"`` entry point;
4. every script imports only the standard library and ``lauren_di`` — the
   library's own runtime rule (PRD 01 §6.2), which an example must not quietly
   widen by importing a web framework;
5. ``examples/README.md`` and the directory name the same ten examples, and
   the index documents the exact command this module runs.

The scripts are driven as subprocesses rather than imported: importing one
would execute it in the test process and share module-level state (``events``,
``order``, ``log``) between examples, and it would not prove that the file
works as a program. ``PYTHONPATH`` carries the repository root so a checkout
needs no install step; ``examples/README.md`` documents both spellings.
"""

from __future__ import annotations

import ast
import os
import pathlib
import re
import subprocess
import sys

import pytest

pytestmark = pytest.mark.integration

ROOT = pathlib.Path(__file__).resolve().parents[2]
EXAMPLES = ROOT / "examples"
INDEX = EXAMPLES / "README.md"

#: One index row's link: ``[01_minimal_singleton.py](01_minimal_singleton.py)``.
_INDEX_ROW_RE = re.compile(r"\[(?P<name>\d{2}_[a-z0-9_]+\.py)\]\((?P<target>[^)]+)\)")
#: The goal asks for ten distinct scenarios; a different figure is a deliberate
#: decision, not something to discover by accident.
DOCUMENTED_EXAMPLES = 10
#: ``sys.stdlib_module_names`` is the interpreter's own answer to "is this
#: stdlib?", so the allow-list cannot drift from the Python running the tests.
_ALLOWED_IMPORTS = frozenset(sys.stdlib_module_names) | {"lauren_di"}
_TIMEOUT_S = 120


def example_scripts() -> list[pathlib.Path]:
    """Every example script, in filename order."""
    return sorted(EXAMPLES.glob("*.py"))


def indexed_names() -> list[str]:
    """The example filenames ``examples/README.md`` links to."""
    text = INDEX.read_text(encoding="utf-8")
    return [match.group("name") for match in _INDEX_ROW_RE.finditer(text)]


def _env() -> dict[str, str]:
    """The subprocess environment: the repository root, then whatever was there."""
    env = dict(os.environ)
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = os.pathsep.join(part for part in (str(ROOT), existing) if part)
    return env


def _run(script: pathlib.Path, cwd: pathlib.Path) -> subprocess.CompletedProcess[str]:
    """Run one example as a program and capture everything it says."""
    return subprocess.run(
        [sys.executable, script if cwd is ROOT else script.name],
        cwd=cwd,
        capture_output=True,
        text=True,
        env=_env(),
        timeout=_TIMEOUT_S,
        check=False,
    )


def _failure(
    script: pathlib.Path, cwd: pathlib.Path, completed: subprocess.CompletedProcess[str]
) -> str:
    """A diagnosable message for a non-zero exit, with the program's own words."""
    return (
        f"{script.name} exited {completed.returncode} when run from {cwd}\n"
        f"STDOUT:\n{completed.stdout}\nSTDERR:\n{completed.stderr}"
    )


def _tree(script: pathlib.Path) -> ast.Module:
    return ast.parse(script.read_text(encoding="utf-8"), filename=str(script))


def _has_main_guard(tree: ast.Module) -> bool:
    """True when the module ends in ``if __name__ == "__main__":``."""
    for node in tree.body:
        if not isinstance(node, ast.If):
            continue
        test = node.test
        if not isinstance(test, ast.Compare):
            continue
        if len(test.comparators) != 1:
            continue
        left, right = test.left, test.comparators[0]
        if (
            isinstance(left, ast.Name)
            and left.id == "__name__"
            and isinstance(right, ast.Constant)
            and right.value == "__main__"
        ):
            return True
    return False


def _imported_roots(tree: ast.Module) -> set[str]:
    """The top-level module name of every import, plus ``"."`` for a relative one."""
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                roots.add(alias.name.split(".", 1)[0])
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                roots.add(".")
            elif node.module:
                roots.add(node.module.split(".", 1)[0])
    return roots


def test_the_directory_holds_the_ten_documented_scenarios() -> None:
    """The deliverable is ten distinct examples, not "some files"."""
    scripts = example_scripts()
    assert len(scripts) == DOCUMENTED_EXAMPLES, (
        f"examples/ holds {len(scripts)} script(s), but this suite documents "
        f"{DOCUMENTED_EXAMPLES}. Adding an eleventh is a deliberate change: update "
        "DOCUMENTED_EXAMPLES and the index table together."
    )


def test_the_index_and_the_directory_name_the_same_examples() -> None:
    """Neither direction may drift: no unindexed example, no index row without a file."""
    on_disk = {script.name for script in example_scripts()}
    indexed = indexed_names()
    assert not (missing_rows := sorted(on_disk - set(indexed))), (
        f"examples/README.md has no row for {missing_rows}"
    )
    assert not (missing_files := sorted(set(indexed) - on_disk)), (
        f"examples/README.md links to files that do not exist: {missing_files}"
    )
    assert len(indexed) == len(set(indexed)), f"examples/README.md repeats an example: {indexed}"


def test_the_index_documents_the_command_this_suite_runs() -> None:
    """The documented command is the one proven to work, spelled identically."""
    text = INDEX.read_text(encoding="utf-8")
    undocumented = [
        script.name
        for script in example_scripts()
        if f"PYTHONPATH=. python examples/{script.name}" not in text
    ]
    assert not undocumented, (
        "examples/README.md must document the exact command that is tested, "
        f"`PYTHONPATH=. python examples/<name>`; missing for {undocumented}"
    )


@pytest.mark.parametrize("script", example_scripts(), ids=lambda path: path.name)
def test_each_example_runs_from_the_repository_root(script: pathlib.Path) -> None:
    """The documented invocation works, and the program actually says something."""
    completed = _run(script, ROOT)
    assert completed.returncode == 0, _failure(script, ROOT, completed)
    assert completed.stdout.strip(), f"{script.name} exited 0 but printed nothing"


@pytest.mark.parametrize("script", example_scripts(), ids=lambda path: path.name)
def test_each_example_runs_from_inside_the_examples_directory(script: pathlib.Path) -> None:
    """The scripts do not depend on the root being the working directory."""
    completed = _run(script, EXAMPLES)
    assert completed.returncode == 0, _failure(script, EXAMPLES, completed)
    assert completed.stdout.strip(), f"{script.name} exited 0 but printed nothing"


@pytest.mark.parametrize("script", example_scripts(), ids=lambda path: path.name)
def test_each_example_states_its_scenario_and_entry_point(script: pathlib.Path) -> None:
    """A reader must be able to tell what the file is for before running it."""
    tree = _tree(script)
    docstring = ast.get_docstring(tree) or ""
    assert "Scenario" in docstring, (
        f"{script.name} has no 'Scenario' section in its module docstring"
    )
    assert len(docstring) > 200, (
        f"{script.name}'s module docstring is too thin to state a scenario "
        f"({len(docstring)} characters)"
    )
    assert _has_main_guard(tree), f'{script.name} has no `if __name__ == "__main__"` entry point'


@pytest.mark.parametrize("script", example_scripts(), ids=lambda path: path.name)
def test_each_example_imports_only_the_standard_library_and_lauren_di(
    script: pathlib.Path,
) -> None:
    """PRD 01 §6.2's runtime rule applies to the examples too, not just the package."""
    offenders = sorted(_imported_roots(_tree(script)) - _ALLOWED_IMPORTS)
    assert not offenders, (
        f"{script.name} imports outside the standard library and `lauren_di`: {offenders}. "
        "An example must not need a web framework or any third-party runtime package."
    )
