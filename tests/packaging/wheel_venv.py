"""A wheel installed into a throwaway virtual environment.

Two §10 rows need the same thing — the built artefact installed somewhere the
checkout cannot answer for it — so the machinery lives here once:

* the packaging row: ``lauren_di`` imports in a clean venv;
* the import-graph row: the *installed* package's imports are all standard
  library, with no framework in sight.

``--no-index --no-deps`` is deliberate. It keeps the install offline, and it
makes both claims stronger than a text search would: if the wheel needed a
runtime dependency, the import would fail here rather than quietly resolve
against whatever the developer happens to have installed.

The environment handed to the probe strips ``PYTHONPATH`` and ``PYTHONHOME`` and
uses a working directory outside the checkout, so "it imported" cannot mean "it
found the developer's editable install".
"""

from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
from dataclasses import dataclass
from typing import Any

_PACKAGE = "lauren_di"


def clean_environment() -> dict[str, str]:
    """The process environment minus anything that could leak the checkout in."""
    return {
        key: value for key, value in os.environ.items() if key not in {"PYTHONPATH", "PYTHONHOME"}
    }


def venv_python(venv: pathlib.Path) -> pathlib.Path:
    """The interpreter of a virtual environment at ``venv``."""
    if os.name == "nt":  # pragma: no cover - CI is POSIX
        return venv / "Scripts" / "python.exe"
    return venv / "bin" / "python"


def _query(python: pathlib.Path, code: str) -> str:
    """Run ``code`` with ``python`` in the clean environment and return its stdout."""
    result = subprocess.run(
        [str(python), "-c", code],
        cwd=str(python.parent.parent),
        capture_output=True,
        text=True,
        timeout=120,
        env=clean_environment(),
    )
    if result.returncode != 0:  # pragma: no cover - environment accident
        raise RuntimeError(f"{code!r} failed:\n{result.stdout}\n{result.stderr}")
    return result.stdout


@dataclass(frozen=True)
class FreshInstall:
    """A wheel installed into a virtual environment that holds nothing else."""

    venv: pathlib.Path
    python: pathlib.Path
    purelib: pathlib.Path

    @property
    def package(self) -> pathlib.Path:
        """The installed ``lauren_di`` package directory."""
        return self.purelib / _PACKAGE

    def modules(self) -> list[pathlib.Path]:
        """Every shipped python module of the installed package, sorted."""
        return sorted(self.package.rglob("*.py"))

    def run(
        self, code: str, *, cwd: pathlib.Path | None = None
    ) -> subprocess.CompletedProcess[str]:
        """Run ``code`` with the venv interpreter, isolated from the checkout."""
        return subprocess.run(
            [str(self.python), "-c", code],
            cwd=str(cwd if cwd is not None else self.venv.parent),
            capture_output=True,
            text=True,
            timeout=120,
            env=clean_environment(),
        )

    def probe(self, code: str) -> Any:
        """Run ``code``, which must print one JSON document, and decode it."""
        result = self.run(code)
        if result.returncode != 0:
            raise AssertionError(
                f"probe failed in the fresh venv:\n{result.stdout}\n{result.stderr}"
            )
        return json.loads(result.stdout)


def install(wheel: pathlib.Path, venv: pathlib.Path) -> FreshInstall:
    """Create ``venv``, install ``wheel`` into it offline, and describe it.

    Raises :class:`RuntimeError` when the environment cannot be built; the
    fixture in ``conftest.py`` turns that into a skip with the reason, because a
    missing ``venv``/``pip`` is an environment gap, not a packaging defect.
    """
    created = subprocess.run(
        [sys.executable, "-m", "venv", str(venv)],
        capture_output=True,
        text=True,
        timeout=300,
    )
    if created.returncode != 0:
        raise RuntimeError(f"cannot create a virtual environment:\n{created.stderr.strip()}")

    python = venv_python(venv)
    installed = subprocess.run(
        [
            str(python),
            "-m",
            "pip",
            "install",
            "--no-index",
            "--no-deps",
            "--disable-pip-version-check",
            "--quiet",
            str(wheel),
        ],
        capture_output=True,
        text=True,
        timeout=300,
    )
    if installed.returncode != 0:  # pragma: no cover - a broken install is a real failure
        raise RuntimeError(f"pip install failed:\n{installed.stdout}\n{installed.stderr}")

    purelib = pathlib.Path(
        _query(python, "import sysconfig; print(sysconfig.get_paths()['purelib'])").strip()
    )
    return FreshInstall(venv=venv, python=python, purelib=purelib)
