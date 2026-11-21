"""Parity fixtures: one ``backend`` parameter, and the pinned reference checkout.

Two things are load-bearing here.

**The backend fixture is parameterised, not skipped.** MC-29 says an
implementation that cannot be imported reports SKIP for every invariant and zero
PASS — a run where one side never executed must not look green. ``pytest``
records the skip per parameter id, so the summary shows exactly which side was
missing.

**The reference is pinned.** MC-32: ``lauren.__file__`` must resolve inside
``../lauren-framework``, and the session records the sha256 of its
``_di/__init__.py``. PRD 08 §1.5 measured a *different* build installed on the
default path in this environment, so an unpinned parity run would compare the
library against something that is not the reference.
"""

from __future__ import annotations

import hashlib
import pathlib

import pytest
from backends import BACKENDS, REFERENCE_ROOT, Backend


@pytest.fixture(params=BACKENDS, ids=lambda backend: backend.name)
def backend(request: pytest.FixtureRequest) -> Backend:
    """One implementation per parameter; SKIP (never PASS) when it is absent."""
    candidate: Backend = request.param
    if not candidate.load():
        pytest.skip(f"{candidate.module} is not importable; expected it at {REFERENCE_ROOT}")
    return candidate


@pytest.fixture(scope="session")
def reference() -> pathlib.Path:
    """The pinned ``lauren`` checkout, or a skip naming what is actually missing."""
    candidate = BACKENDS[0]
    if not candidate.load():
        if not REFERENCE_ROOT.is_dir():
            pytest.skip(f"the reference checkout is not present at {REFERENCE_ROOT}")
        # The path exists but the import failed: a missing runtime dependency
        # (`anyio`, which `nox -s parity` installs) or an interpreter older than
        # the reference's own floor. Reporting "not present" here would send a
        # reader looking for a directory that is already there.
        pytest.skip(
            f"lauren is not importable from the pinned checkout {REFERENCE_ROOT}: "
            "its runtime dependency is missing, or this interpreter is older than "
            "the reference supports (`nox -s parity` installs the dependency)"
        )
    location = pathlib.Path(candidate.origin()).resolve()
    assert REFERENCE_ROOT in location.parents, (
        f"lauren.__file__ is {location}, outside the pinned checkout {REFERENCE_ROOT} "
        "(MC-32): an unpinned run measures a different build"
    )
    return location


@pytest.fixture(scope="session")
def reference_digest(reference: pathlib.Path) -> str:
    """sha256 of the reference's ``_di/__init__.py``, recorded for the run."""
    return hashlib.sha256((reference.parent / "_di" / "__init__.py").read_bytes()).hexdigest()
