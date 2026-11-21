"""Fixtures for the packaging tests: build the wheel, install it in a clean venv.

The build runs against a *copy* of the build inputs in a temporary directory, so
a test run never leaves ``build/`` or ``*.egg-info`` behind in the checkout — a
conformance suite that dirties the tree it is checking is a nuisance to review.

``build`` (the project's own builder, used by ``nox -s build``) is a ``dev``
extra dependency. If it is missing the fixture skips with a reason instead of
failing: the tests are still correct, they simply cannot build an artefact in
that environment.

The install fixture is session-scoped as well: the packaging row and the
import-graph row both need "the wheel, in a venv, with nothing else", and
building that twice would only make the suite slower, not stricter.
"""

from __future__ import annotations

import importlib.util
import pathlib
import shutil
import subprocess
import sys

import pytest

# ``wheel_venv`` sits next to this file, not on the path: make it importable the
# same way ``tests/docs/conftest.py`` does for its engine module.
sys.path.insert(0, str(pathlib.Path(__file__).parent))

import wheel_venv

ROOT = pathlib.Path(__file__).resolve().parents[2]

#: Everything ``python -m build`` reads from the source tree.
_BUILD_INPUTS = ("pyproject.toml", "README.md", "LICENSE")
_PACKAGE = "lauren_di"


def _source_copy(destination: pathlib.Path) -> pathlib.Path:
    """Copy the build inputs into ``destination`` and return it."""
    destination.mkdir(parents=True, exist_ok=True)
    for name in _BUILD_INPUTS:
        shutil.copy(ROOT / name, destination / name)
    shutil.copytree(
        ROOT / _PACKAGE,
        destination / _PACKAGE,
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    return destination


@pytest.fixture(scope="session")
def wheel(tmp_path_factory: pytest.TempPathFactory) -> pathlib.Path:
    """The wheel built from this checkout, built once per session."""
    if importlib.util.find_spec("build") is None:
        pytest.skip("`build` is not installed; `pip install -e '.[dev]'` provides it")

    workdir = tmp_path_factory.mktemp("wheel")
    source = _source_copy(workdir / "src")
    out = workdir / "dist"
    result = subprocess.run(
        [sys.executable, "-m", "build", "--wheel", "--outdir", str(out), str(source)],
        capture_output=True,
        text=True,
        timeout=300,
    )
    if result.returncode != 0:  # pragma: no cover - a broken build is a real failure
        pytest.fail(f"`python -m build --wheel` failed:\n{result.stdout}\n{result.stderr}")

    wheels = sorted(out.glob("*.whl"))
    assert len(wheels) == 1, f"expected exactly one wheel, got {[w.name for w in wheels]}"
    return wheels[0]


@pytest.fixture(scope="session")
def installed_wheel(
    wheel: pathlib.Path, tmp_path_factory: pytest.TempPathFactory
) -> wheel_venv.FreshInstall:
    """``wheel`` installed into a fresh virtual environment, built once per session."""
    venv = tmp_path_factory.mktemp("install") / "venv"
    try:
        return wheel_venv.install(wheel, venv)
    except RuntimeError as exc:  # pragma: no cover - environment accident
        pytest.skip(str(exc))
