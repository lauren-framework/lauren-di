"""Fixtures shared by the unit suite.

One of them is load-bearing. Two contract tests read ``noxfile.py`` as data —
the posargs contract (``test_noxfile_contract.py``) and the ignore-rule contract
(``test_gitignore_contract.py``) — and each used to import it under its own
module name. Nox registers every session at *import* time, so the second import
registered each session a second time: a ``FutureWarning`` today and an error in
a future nox ("The session 'tests' has already been registered"). Importing the
file once per test session fixes the cause instead of filtering the warning.
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys
from typing import Any

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
NOXFILE = ROOT / "noxfile.py"


@pytest.fixture(scope="session")
def noxfile() -> Any:
    """The repository's real ``noxfile.py``, imported exactly once."""
    cached = sys.modules.get("_lauren_di_noxfile")
    if cached is not None:
        return cached
    spec = importlib.util.spec_from_file_location("_lauren_di_noxfile", NOXFILE)
    assert spec is not None, f"cannot build an import spec for {NOXFILE}"
    assert spec.loader is not None, f"no loader for {NOXFILE}"
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module
