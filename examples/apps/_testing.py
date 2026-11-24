"""Shared pytest helpers for the framework examples.

Two jobs, both about the same failure mode: an example that is *absent* must be
reported as a skip, never as a pass and never as a confusing ``AttributeError``.

``require``
    Skips the calling module unless the host's *distribution* is installed
    (D-62: SKIP without the package, PASS with it). It asks
    ``importlib.metadata`` rather than ``import`` on purpose: ``examples/apps``
    is on ``sys.path`` while the suite runs, and each app directory is named
    after the framework it demonstrates — so ``import starlette`` can resolve to
    the empty local directory as a namespace package when the real one is
    missing. A distribution name cannot be shadowed that way.

``load``
    Imports an app module by name after appending its directory to ``sys.path``
    (appended, never inserted, so a local file can never shadow an installed
    one). Tests use it instead of a bare ``import`` so the import happens after
    the guard.

Both are called at module level in a test module::

    from _testing import load, require

    require("flask")
    flask_app = load("flask", "flask_app")


Run
---
Imported by the tests under ``examples/apps/<framework>/tests/``::

    nox -s examples
"""

from __future__ import annotations

import importlib
import importlib.metadata
import pathlib
import sys
from types import ModuleType

import pytest

#: The directory holding one sub-directory per example app.
APPS_DIR = pathlib.Path(__file__).resolve().parent


def require(distribution: str) -> None:
    """Skip the calling test module unless ``distribution`` is installed.

    Args:
        distribution: The *distribution* name as it appears on PyPI and in
            ``importlib.metadata`` — ``django``, not the import name of a
            submodule; ``typer``, not ``typer.main``.

    Raises:
        pytest.skip.Exception: with ``allow_module_level=True`` when the
            distribution is absent, so the whole module is reported as skipped
            with a reason naming the requirement file to install.
    """
    try:
        importlib.metadata.distribution(distribution)
    except importlib.metadata.PackageNotFoundError:
        pytest.skip(
            f"{distribution} is not installed, so this host example is skipped (D-62). "
            f"Install it with `pip install -r examples/apps/{distribution}/requirements.txt` "
            "or run the whole set with `nox -s examples`.",
            allow_module_level=True,
        )


def load(app: str, module: str) -> ModuleType:
    """Import ``module`` from ``examples/apps/<app>/``.

    Args:
        app: The app directory name, e.g. ``"flask"``.
        module: The module file inside it, e.g. ``"flask_app"``.

    Returns:
        The imported module.

    Raises:
        FileNotFoundError: if the app directory does not exist — a typo here
            would otherwise surface as an import error from somewhere else.
    """
    directory = APPS_DIR / app
    if not directory.is_dir():
        raise FileNotFoundError(f"no such example app: {directory}")
    if str(directory) not in sys.path:
        sys.path.append(str(directory))
    return importlib.import_module(module)
